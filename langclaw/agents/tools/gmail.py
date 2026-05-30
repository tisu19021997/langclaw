"""Gmail tools — read, search, send, draft, reply, and manage labels.

Each public ``make_*`` factory returns a LangChain ``BaseTool`` bound to the
provided ``GmailConfig``.  The factories are assembled in
``langclaw.agents.tools.build_gmail_tools`` based on the ``readonly`` flag.
"""

from __future__ import annotations

import asyncio
import base64
from email.mime.text import MIMEText
from typing import TYPE_CHECKING, Any

from langchain_core.tools import BaseTool, tool
from loguru import logger

if TYPE_CHECKING:
    from langclaw.config.schema import GmailConfig


def _get_gmail_service(config: GmailConfig) -> Any:
    """Build and return an authorised Gmail API service object."""
    try:
        from googleapiclient.discovery import build
    except ImportError as exc:
        raise ImportError(
            "google-api-python-client is required for Gmail tools. "
            "Install with: pip install langclaw[gmail]"
        ) from exc

    from langclaw.agents.tools.gmail_auth import get_gmail_credentials

    creds = get_gmail_credentials(config)
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


def _extract_header(headers: list[dict], name: str) -> str:
    for h in headers:
        if h.get("name", "").lower() == name.lower():
            return h.get("value", "")
    return ""


def _decode_body(payload: dict) -> str:
    """Recursively extract plain-text body from a Gmail message payload."""
    mime = payload.get("mimeType", "")

    if mime == "text/plain":
        data = payload.get("body", {}).get("data", "")
        if data:
            return base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")

    for part in payload.get("parts", []):
        text = _decode_body(part)
        if text:
            return text

    if mime.startswith("text/") and not payload.get("parts"):
        data = payload.get("body", {}).get("data", "")
        if data:
            return base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")

    return ""


def _extract_attachments(payload: dict) -> list[dict]:
    """Return metadata for each attachment in the message."""
    attachments: list[dict] = []
    for part in payload.get("parts", []):
        filename = part.get("filename")
        if filename:
            attachments.append(
                {
                    "filename": filename,
                    "mime_type": part.get("mimeType", ""),
                    "size": part.get("body", {}).get("size", 0),
                }
            )
        attachments.extend(_extract_attachments(part))
    return attachments


# ---------------------------------------------------------------------------
# Tool factories
# ---------------------------------------------------------------------------


def make_read_email_tool(config: GmailConfig) -> BaseTool:
    """Return a ``read_email`` tool that fetches a single email by ID."""

    @tool
    async def read_email(message_id: str) -> dict:
        """Read a specific Gmail email by its message ID.

        Returns the email subject, sender, recipients, date, plain-text body,
        snippet, labels, and a list of attachment metadata.

        Args:
            message_id: The Gmail message ID to read.
        """
        logger.debug("read_email: {}", message_id)
        loop = asyncio.get_running_loop()
        service = _get_gmail_service(config)

        try:
            msg = await loop.run_in_executor(
                None,
                lambda: (
                    service.users()
                    .messages()
                    .get(userId="me", id=message_id, format="full")
                    .execute()
                ),
            )
        except Exception as exc:
            return {"error": f"Failed to read email {message_id!r}: {exc}"}

        headers = msg.get("payload", {}).get("headers", [])
        payload = msg.get("payload", {})

        return {
            "id": msg.get("id", ""),
            "thread_id": msg.get("threadId", ""),
            "subject": _extract_header(headers, "Subject"),
            "from": _extract_header(headers, "From"),
            "to": _extract_header(headers, "To"),
            "cc": _extract_header(headers, "Cc"),
            "date": _extract_header(headers, "Date"),
            "snippet": msg.get("snippet", ""),
            "body": _decode_body(payload),
            "labels": msg.get("labelIds", []),
            "attachments": _extract_attachments(payload),
        }

    return read_email


def make_search_emails_tool(config: GmailConfig) -> BaseTool:
    """Return a ``search_emails`` tool that queries Gmail with Gmail search syntax."""

    @tool
    async def search_emails(query: str, max_results: int = 10) -> list[dict]:
        """Search Gmail emails using Gmail query syntax.

        Args:
            query: Gmail search query (e.g. ``from:alice subject:invoice``,
                ``is:unread``, ``newer_than:2d``, ``has:attachment``).
                Supports the same syntax as the Gmail search bar.
            max_results: Maximum number of emails to return (default 10, max 50).
        """
        max_results = min(max_results, 50)
        logger.debug('search_emails: "{}" (max={})', query, max_results)
        loop = asyncio.get_running_loop()
        service = _get_gmail_service(config)

        try:
            response = await loop.run_in_executor(
                None,
                lambda: (
                    service.users()
                    .messages()
                    .list(userId="me", q=query, maxResults=max_results)
                    .execute()
                ),
            )
        except Exception as exc:
            return [{"error": f"Failed to search emails: {exc}"}]

        messages = response.get("messages", [])
        if not messages:
            return []

        results: list[dict] = []
        for msg_stub in messages:
            try:
                msg = await loop.run_in_executor(
                    None,
                    lambda mid=msg_stub["id"]: (
                        service.users()
                        .messages()
                        .get(
                            userId="me",
                            id=mid,
                            format="metadata",
                            metadataHeaders=["Subject", "From", "Date"],
                        )
                        .execute()
                    ),
                )
            except Exception:
                continue
            headers = msg.get("payload", {}).get("headers", [])
            results.append(
                {
                    "id": msg.get("id", ""),
                    "thread_id": msg.get("threadId", ""),
                    "subject": _extract_header(headers, "Subject"),
                    "from": _extract_header(headers, "From"),
                    "date": _extract_header(headers, "Date"),
                    "snippet": msg.get("snippet", ""),
                    "labels": msg.get("labelIds", []),
                }
            )

        logger.debug("search_emails returned {} results", len(results))
        return results

    return search_emails


def make_send_email_tool(config: GmailConfig) -> BaseTool:
    """Return a ``send_email`` tool that sends a new email."""

    @tool
    async def send_email(
        to: str,
        subject: str,
        body: str,
        cc: str = "",
        bcc: str = "",
    ) -> dict:
        """Send a new email via Gmail.

        Args:
            to: Recipient email address(es), comma-separated for multiple.
            subject: Email subject line.
            body: Plain-text email body.
            cc: CC recipients (comma-separated). Optional.
            bcc: BCC recipients (comma-separated). Optional.
        """
        logger.debug("send_email: to={}, subject={}", to, subject)
        loop = asyncio.get_running_loop()
        service = _get_gmail_service(config)

        message = MIMEText(body)
        message["to"] = to
        message["subject"] = subject
        if cc:
            message["cc"] = cc
        if bcc:
            message["bcc"] = bcc

        raw = base64.urlsafe_b64encode(message.as_bytes()).decode("ascii")

        try:
            sent = await loop.run_in_executor(
                None,
                lambda: service.users().messages().send(userId="me", body={"raw": raw}).execute(),
            )
        except Exception as exc:
            return {"error": f"Failed to send email: {exc}"}

        return {
            "status": "sent",
            "id": sent.get("id", ""),
            "thread_id": sent.get("threadId", ""),
            "labels": sent.get("labelIds", []),
        }

    return send_email


def make_draft_email_tool(config: GmailConfig) -> BaseTool:
    """Return a ``draft_email`` tool that creates a Gmail draft."""

    @tool
    async def draft_email(
        to: str,
        subject: str,
        body: str,
        cc: str = "",
        bcc: str = "",
    ) -> dict:
        """Create a draft email in Gmail.

        Args:
            to: Recipient email address(es), comma-separated for multiple.
            subject: Email subject line.
            body: Plain-text email body.
            cc: CC recipients (comma-separated). Optional.
            bcc: BCC recipients (comma-separated). Optional.
        """
        logger.debug("draft_email: to={}, subject={}", to, subject)
        loop = asyncio.get_running_loop()
        service = _get_gmail_service(config)

        message = MIMEText(body)
        message["to"] = to
        message["subject"] = subject
        if cc:
            message["cc"] = cc
        if bcc:
            message["bcc"] = bcc

        raw = base64.urlsafe_b64encode(message.as_bytes()).decode("ascii")

        try:
            draft = await loop.run_in_executor(
                None,
                lambda: (
                    service.users()
                    .drafts()
                    .create(userId="me", body={"message": {"raw": raw}})
                    .execute()
                ),
            )
        except Exception as exc:
            return {"error": f"Failed to create draft: {exc}"}

        return {
            "status": "drafted",
            "draft_id": draft.get("id", ""),
            "message_id": draft.get("message", {}).get("id", ""),
        }

    return draft_email


def make_reply_email_tool(config: GmailConfig) -> BaseTool:
    """Return a ``reply_email`` tool that replies to an existing email thread."""

    @tool
    async def reply_email(message_id: str, body: str) -> dict:
        """Reply to an existing email. The reply is sent in the same thread.

        Args:
            message_id: The Gmail message ID to reply to.
            body: Plain-text reply body.
        """
        logger.debug("reply_email: message_id={}", message_id)
        loop = asyncio.get_running_loop()
        service = _get_gmail_service(config)

        try:
            original = await loop.run_in_executor(
                None,
                lambda: (
                    service.users()
                    .messages()
                    .get(
                        userId="me",
                        id=message_id,
                        format="metadata",
                        metadataHeaders=["Subject", "From", "To", "Message-ID"],
                    )
                    .execute()
                ),
            )
        except Exception as exc:
            return {"error": f"Failed to fetch original message {message_id!r}: {exc}"}

        orig_headers = original.get("payload", {}).get("headers", [])
        orig_subject = _extract_header(orig_headers, "Subject")
        orig_from = _extract_header(orig_headers, "From")
        orig_message_id = _extract_header(orig_headers, "Message-ID")
        thread_id = original.get("threadId", "")

        subject = orig_subject if orig_subject.startswith("Re:") else f"Re: {orig_subject}"

        message = MIMEText(body)
        message["to"] = orig_from
        message["subject"] = subject
        if orig_message_id:
            message["In-Reply-To"] = orig_message_id
            message["References"] = orig_message_id

        raw = base64.urlsafe_b64encode(message.as_bytes()).decode("ascii")

        try:
            sent = await loop.run_in_executor(
                None,
                lambda: (
                    service.users()
                    .messages()
                    .send(userId="me", body={"raw": raw, "threadId": thread_id})
                    .execute()
                ),
            )
        except Exception as exc:
            return {"error": f"Failed to send reply: {exc}"}

        return {
            "status": "replied",
            "id": sent.get("id", ""),
            "thread_id": sent.get("threadId", ""),
        }

    return reply_email


def make_manage_labels_tool(config: GmailConfig) -> BaseTool:
    """Return a ``manage_labels`` tool for adding/removing labels on messages."""

    @tool
    async def manage_labels(
        message_id: str,
        add_labels: list[str] | None = None,
        remove_labels: list[str] | None = None,
    ) -> dict:
        """Add or remove labels on a Gmail message.

        Common labels: ``INBOX``, ``UNREAD``, ``STARRED``, ``IMPORTANT``,
        ``SPAM``, ``TRASH``, ``CATEGORY_PERSONAL``, ``CATEGORY_PROMOTIONS``.

        To mark as read: remove ``UNREAD``.
        To archive: remove ``INBOX``.
        To star: add ``STARRED``.

        Args:
            message_id: The Gmail message ID to modify.
            add_labels: Label IDs to add. Optional.
            remove_labels: Label IDs to remove. Optional.
        """
        add_labels = add_labels or []
        remove_labels = remove_labels or []

        if not add_labels and not remove_labels:
            return {"error": "Provide at least one of add_labels or remove_labels."}

        logger.debug(
            "manage_labels: {} add={} remove={}",
            message_id,
            add_labels,
            remove_labels,
        )
        loop = asyncio.get_running_loop()
        service = _get_gmail_service(config)

        try:
            modified = await loop.run_in_executor(
                None,
                lambda: (
                    service.users()
                    .messages()
                    .modify(
                        userId="me",
                        id=message_id,
                        body={
                            "addLabelIds": add_labels,
                            "removeLabelIds": remove_labels,
                        },
                    )
                    .execute()
                ),
            )
        except Exception as exc:
            return {"error": f"Failed to modify labels on {message_id!r}: {exc}"}

        return {
            "status": "modified",
            "id": modified.get("id", ""),
            "labels": modified.get("labelIds", []),
        }

    return manage_labels


__all__ = [
    "make_draft_email_tool",
    "make_manage_labels_tool",
    "make_read_email_tool",
    "make_reply_email_tool",
    "make_search_emails_tool",
    "make_send_email_tool",
]
