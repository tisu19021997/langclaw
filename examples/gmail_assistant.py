"""
Gmail Assistant — an agent with full Gmail integration.

Demonstrates
------------
- **Gmail tools** — read, search, send, draft, reply, and manage labels,
  all wired via ``GmailConfig``.
- **OAuth 2.0 setup** — credentials from env vars or config.
- **RBAC** — admin role can send/draft/reply; viewer role is read-only.
- ``@app.command()`` — ``/inbox`` shows recent emails without the LLM.

Gmail setup
-----------
1. Create a Google Cloud project and enable the Gmail API.
2. Create OAuth 2.0 "Desktop app" credentials in the Cloud Console.
3. Set the following env vars (or add to ``.env``)::

       LANGCLAW__TOOLS__GMAIL__ENABLED=true
       LANGCLAW__TOOLS__GMAIL__CLIENT_ID=your-client-id.apps.googleusercontent.com
       LANGCLAW__TOOLS__GMAIL__CLIENT_SECRET=your-client-secret
       LANGCLAW__TOOLS__GMAIL__READONLY=false

4. On first run, a browser window opens for OAuth consent. The token is
   saved to ``~/.langclaw/gmail_token.json`` and reused on subsequent runs.

Run
---
1. Complete the Gmail setup above.
2. Copy ``.env.example`` to ``.env`` and fill in an LLM provider key
   and a channel token.
3. ``pip install 'langclaw[gmail,telegram]'``
4. ``python examples/gmail_assistant.py``
5. Try:
   - "Show my latest 5 unread emails"
   - "Read email <message_id>"
   - "Draft a reply to <message_id> saying thanks"
   - "Search for emails from alice@example.com with attachments"
   - ``/inbox`` (instant, no LLM)
"""

from __future__ import annotations

import asyncio

from loguru import logger

from langclaw import Langclaw
from langclaw.config.schema import load_config
from langclaw.gateway.commands import CommandContext

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

base_config = load_config()

if not base_config.tools.gmail.enabled:
    logger.warning(
        "Gmail is not enabled. Set LANGCLAW__TOOLS__GMAIL__ENABLED=true "
        "and provide OAuth credentials. See this file's docstring for setup."
    )

app = Langclaw(
    config=base_config,
    system_prompt=(
        "## Gmail Assistant\n"
        "You are a personal email assistant with access to the user's Gmail.\n\n"
        "Guidelines:\n"
        "- When the user asks about emails, use `search_emails` first to find "
        "matching messages, then `read_email` for full details if needed.\n"
        "- Summarize email contents concisely — don't dump raw bodies.\n"
        "- When drafting or replying, confirm the content with the user "
        "before sending.\n"
        "- Never reveal full email addresses of third parties in summaries.\n"
        "- Use `manage_labels` to help organize (archive, star, mark read)."
    ),
)


# ---------------------------------------------------------------------------
# Command: quick inbox peek (no LLM)
# ---------------------------------------------------------------------------


@app.command("inbox", description="show 5 most recent emails (no AI)")
async def inbox_cmd(ctx: CommandContext) -> str:
    gmail_cfg = app.config.tools.gmail
    if not gmail_cfg.enabled:
        return "Gmail is not configured. Set LANGCLAW__TOOLS__GMAIL__ENABLED=true in .env."

    try:
        from langclaw.agents.tools.gmail import (
            _extract_header,
            _get_gmail_service,
        )
    except ImportError:
        return "Gmail dependencies not installed. Run: pip install 'langclaw[gmail]'"

    loop = asyncio.get_running_loop()
    service = _get_gmail_service(gmail_cfg)

    response = await loop.run_in_executor(
        None,
        lambda: (
            service.users().messages().list(userId="me", maxResults=5, q="is:inbox").execute()
        ),
    )

    messages = response.get("messages", [])
    if not messages:
        return "Inbox is empty."

    lines = ["Recent emails:"]
    for msg_stub in messages:
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
        headers = msg.get("payload", {}).get("headers", [])
        subject = _extract_header(headers, "Subject") or "(no subject)"
        sender = _extract_header(headers, "From")
        date = _extract_header(headers, "Date")
        lines.append(f"  [{msg['id'][:8]}] {subject}")
        lines.append(f"    From: {sender}  ({date})")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# RBAC: admin can send/draft/reply, viewer can only read/search
# ---------------------------------------------------------------------------

app.role("admin", tools=["*"])
app.role("viewer", tools=["search_emails", "read_email", "manage_labels", "web_search"])

# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    app.run()
