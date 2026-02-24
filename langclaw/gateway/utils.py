"""
Shared utilities for all gateway channels.

Centralises message splitting, tool-progress formatting, the user whitelist
check, and other helpers so individual channel implementations stay thin.
"""

from __future__ import annotations

from typing import Literal

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
