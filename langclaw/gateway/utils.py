"""
Shared utilities for all gateway channels.

Centralises message splitting, tool-progress formatting, the user whitelist
check, and other helpers so individual channel implementations stay thin.
"""

from __future__ import annotations

import base64
import mimetypes
from pathlib import Path
from typing import Any, Literal

from langclaw.bus.base import Attachment, AttachmentType

TRUNCATION_SUFFIX = "…[truncated]"

# ---------------------------------------------------------------------------
# Tool-progress labels (shared across all channels)
# ---------------------------------------------------------------------------

TOOL_LABELS: dict[str, str] = {
    "read_file": "📄 Reading",
    "write_file": "✏️ Writing",
    "edit_file": "📝 Editing",
    "ls": "📁 Listing",
    "glob": "🔍 Globbing",
    "grep": "🔎 Searching",
    "execute": "⚙️ Running",
    "task": "🤖 Subagent",
    "write_todos": "📋 Todos",
}


# ---------------------------------------------------------------------------
# Tool-progress formatting
# ---------------------------------------------------------------------------


def _tool_arg_suffix(tool: str, args: dict) -> tuple[str, bool]:
    """Extract a human-readable suffix from tool args.

    Returns ``(raw_text, is_path_like)`` where *is_path_like* is True when
    the suffix should be wrapped in a code/monospace span.
    """
    if tool in ("read_file", "write_file", "edit_file"):
        path = args.get("path") or args.get("file_path") or ""
        return (path, True) if path else ("", False)
    if tool == "ls":
        return (args.get("path") or ".", True)
    if tool in ("glob", "grep"):
        pattern = args.get("pattern") or args.get("glob") or ""
        return (pattern, True) if pattern else ("", False)
    if tool == "execute":
        cmd = (args.get("command") or args.get("cmd") or "")[:60]
        return (cmd, True) if cmd else ("", False)
    if tool == "task":
        desc = (args.get("description") or args.get("prompt") or "")[:60]
        return (f": {desc}…", False) if desc else ("…", False)
    return ("", False)


def format_tool_progress(
    tool: str,
    args: dict,
    markup: Literal["html", "markdown"] = "markdown",
) -> str:
    """Return a one-line description of a tool invocation.

    *markup* controls the output format:
      - ``"html"``     → ``<b>`` / ``<code>`` (Telegram)
      - ``"markdown"`` → ``**`` / backticks   (Discord, Slack, …)
    """
    label = TOOL_LABELS.get(tool, f"🔧 {tool}")
    raw, is_code = _tool_arg_suffix(tool, args)

    if markup == "html":
        bold = lambda s: f"<b>{s}</b>"  # noqa: E731
        code = lambda s: f"<code>{s}</code>"  # noqa: E731
    else:
        bold = lambda s: f"**{s}**"  # noqa: E731
        code = lambda s: f"`{s}`"  # noqa: E731

    if raw:
        suffix = f" {code(raw)}" if is_code else raw
        return f"Ran {bold(label)}{suffix}"
    return f"Ran {bold(label)}{code(str(args))}"


# ---------------------------------------------------------------------------
# Message splitting
# ---------------------------------------------------------------------------


def split_message(content: str, max_len: int = 2000) -> list[str]:
    """Split *content* into chunks of at most *max_len* chars.

    Prefers breaking at newlines, then spaces, falling back to a hard cut.
    """
    if not content:
        return []
    if len(content) <= max_len:
        return [content]
    chunks: list[str] = []
    while content:
        if len(content) <= max_len:
            chunks.append(content)
            break
        cut = content[:max_len]
        pos = cut.rfind("\n")
        if pos <= 0:
            pos = cut.rfind(" ")
        if pos <= 0:
            pos = max_len
        chunks.append(content[:pos])
        content = content[pos:].lstrip()
    return chunks


# ---------------------------------------------------------------------------
# User whitelist
# ---------------------------------------------------------------------------


def is_allowed(
    allow_from: list[str],
    user_id: str,
    username: str | None = None,
) -> bool:
    """Return True if *user_id* or *username* passes the *allow_from* whitelist.

    An empty *allow_from* list means "allow everyone".
    """
    if not allow_from:
        return True
    allowed = set(allow_from)
    return user_id in allowed or (username is not None and username in allowed)


# ---------------------------------------------------------------------------
# Attachment helpers
# ---------------------------------------------------------------------------

_MIME_TO_TYPE: dict[str, AttachmentType] = {
    "image": AttachmentType.IMAGE,
    "audio": AttachmentType.AUDIO,
    "video": AttachmentType.VIDEO,
}


def infer_attachment_type(mime_type: str) -> AttachmentType:
    """Infer ``AttachmentType`` from a MIME type string.

    Args:
        mime_type: MIME type like ``"image/jpeg"``, ``"audio/ogg"``.

    Returns:
        The matching ``AttachmentType``, defaulting to ``FILE``.
    """
    major = mime_type.split("/")[0] if mime_type else ""
    return _MIME_TO_TYPE.get(major, AttachmentType.FILE)


def make_attachment(
    *,
    filename: str = "",
    mime_type: str = "",
    url: str = "",
    data: str = "",
    file_path: str | Path = "",
    size: int = 0,
    attachment_type: AttachmentType | None = None,
) -> Attachment:
    """Create a standardised ``Attachment`` from platform-specific data.

    Channels call this to normalise their platform attachments.
    Exactly one of *url*, *data*, or *file_path* should be provided.

    When *file_path* is given the file is read and base64-encoded into
    *data*, and *mime_type* is guessed from the extension if not provided.

    Args:
        filename: Original filename.
        mime_type: MIME type. Guessed from *filename*/*file_path* if omitted.
        url: Public URL to the attachment.
        data: Pre-encoded base64 string.
        file_path: Local file path — read and base64-encode.
        size: File size in bytes.
        attachment_type: Explicit type override. Inferred from *mime_type* if omitted.

    Returns:
        A populated ``Attachment`` instance.
    """
    if file_path:
        p = Path(file_path)
        if not mime_type:
            mime_type = mimetypes.guess_type(str(p))[0] or "application/octet-stream"
        if not filename:
            filename = p.name
        if not data:
            data = base64.b64encode(p.read_bytes()).decode("ascii")
        if not size:
            size = p.stat().st_size

    if not mime_type and filename:
        mime_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"

    resolved_type = attachment_type or infer_attachment_type(mime_type)
    attachment = Attachment(
        type=resolved_type,
        mime_type=mime_type,
        filename=filename,
        url=url,
        data=data,
        size=size,
    )
    return attachment


def attachments_to_content_blocks(
    text: str,
    attachments: list[Attachment],
) -> str | list[dict[str, Any]]:
    """Convert text + attachments into LangChain multimodal content.

    When there are no attachments, returns the plain text string (preserving
    backward compatibility with models that don't support content blocks).

    When attachments are present, returns a list of content-block dicts
    suitable for ``HumanMessage(content=[...])``.

    Args:
        text: The text content of the message.
        attachments: ``Attachment`` objects from the ``InboundMessage``.

    Returns:
        Either a plain string or a list of LangChain content-block dicts.
    """
    if not attachments:
        return text

    blocks: list[dict[str, Any]] = []

    if text:
        blocks.append({"type": "text", "text": text})

    for att in attachments:
        if att.type == AttachmentType.IMAGE:
            if att.data:
                img_url = f"data:{att.mime_type};base64,{att.data}"
            elif att.url:
                img_url = att.url
            else:
                continue
            blocks.append({"type": "image_url", "image_url": {"url": img_url}})

        elif att.type in (
            AttachmentType.FILE,
            AttachmentType.AUDIO,
            AttachmentType.VIDEO,
        ):
            block: dict[str, Any] = {"type": "file"}
            if att.data:
                block["source"] = {
                    "type": "base64",
                    "media_type": att.mime_type,
                    "data": att.data,
                }
            elif att.url:
                block["source"] = {"type": "url", "url": att.url}
            else:
                continue
            if att.filename:
                block["filename"] = att.filename
            blocks.append(block)

    # If all attachments were skipped, fall back to plain text for
    # backward compatibility (avoids sending a single text-only block).
    has_media = any(b["type"] != "text" for b in blocks)
    if not has_media:
        return text

    return blocks if blocks else text
