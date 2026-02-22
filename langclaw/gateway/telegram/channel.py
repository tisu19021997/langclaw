"""
TelegramChannel — Gateway channel for Telegram.

Requires: langclaw[telegram]  →  uv add "langclaw[telegram]"

Features:
- Handles private messages and group/supergroup messages
- Converts Markdown to Telegram-safe HTML with plain-text fallback
- Splits messages that exceed Telegram's 4 096-char limit
- Streams response as progressive message edits (typing UX)
- Sends 'typing…' chat action while the agent is thinking
- Respects allow_from user whitelist
- /start, /help, /reset command support
- Tenacity retry on transient network errors (ConnectTimeout / TimedOut)
"""

from __future__ import annotations

import asyncio
import logging
import re

from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from langclaw.bus.base import BaseMessageBus, InboundMessage, OutboundMessage
from langclaw.config.schema import TelegramChannelConfig
from langclaw.gateway.base import BaseChannel
from langclaw.session.manager import SessionManager

logger = logging.getLogger(__name__)

# Minimum characters changed before sending a streaming edit to avoid Telegram
# rate-limiting on rapid small updates.
_STREAM_EDIT_MIN_DELTA = 20

# Telegram hard limit per message.
_MAX_MESSAGE_LEN = 4000
_TRUNCATION_SUFFIX = "…[truncated]"

# ---------------------------------------------------------------------------
# Markdown → Telegram HTML
# ---------------------------------------------------------------------------


def _markdown_to_telegram_html(text: str) -> str:
    """Convert a Markdown string to Telegram-safe HTML (parse_mode='HTML')."""
    if not text:
        return ""

    # 1. Extract and protect fenced code blocks
    code_blocks: list[str] = []

    def _save_code_block(m: re.Match) -> str:
        code_blocks.append(m.group(1))
        return f"\x00CB{len(code_blocks) - 1}\x00"

    text = re.sub(r"```[\w]*\n?([\s\S]*?)```", _save_code_block, text)

    # 2. Extract and protect inline code
    inline_codes: list[str] = []

    def _save_inline_code(m: re.Match) -> str:
        inline_codes.append(m.group(1))
        return f"\x00IC{len(inline_codes) - 1}\x00"

    text = re.sub(r"`([^`]+)`", _save_inline_code, text)

    # 3. Strip ATX headings (keep text)
    text = re.sub(r"^#{1,6}\s+(.+)$", r"\1", text, flags=re.MULTILINE)

    # 4. Strip blockquotes (keep text)
    text = re.sub(r"^>\s*(.*)$", r"\1", text, flags=re.MULTILINE)

    # 5. Escape HTML special characters
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    # 6. Links [text](url)
    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', text)

    # 7. Bold
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"__(.+?)__", r"<b>\1</b>", text)

    # 8. Italic (avoid matching inside snake_case identifiers)
    text = re.sub(r"(?<![a-zA-Z0-9])_([^_]+)_(?![a-zA-Z0-9])", r"<i>\1</i>", text)

    # 9. Strikethrough
    text = re.sub(r"~~(.+?)~~", r"<s>\1</s>", text)

    # 10. Bullet lists
    text = re.sub(r"^[-*]\s+", "• ", text, flags=re.MULTILINE)

    # 11. Restore inline code
    for i, code in enumerate(inline_codes):
        escaped = code.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        text = text.replace(f"\x00IC{i}\x00", f"<code>{escaped}</code>")

    # 12. Restore fenced code blocks
    for i, code in enumerate(code_blocks):
        escaped = code.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        text = text.replace(f"\x00CB{i}\x00", f"<pre><code>{escaped}</code></pre>")

    return text


# ---------------------------------------------------------------------------
# Tool-progress formatting
# ---------------------------------------------------------------------------

_TOOL_LABELS: dict[str, str] = {
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


def _format_tool_progress(tool: str, args: dict) -> str:
    """Return a one-line human-readable description of a tool invocation."""
    label = _TOOL_LABELS.get(tool, f"🔧 {tool}")

    if tool in ("read_file", "write_file", "edit_file"):
        path = args.get("path") or args.get("file_path") or ""
        suffix = f" <code>{path}</code>" if path else ""
    elif tool == "ls":
        path = args.get("path") or "."
        suffix = f" <code>{path}</code>"
    elif tool in ("glob", "grep"):
        pattern = args.get("pattern") or args.get("glob") or ""
        suffix = f" <code>{pattern}</code>" if pattern else ""
    elif tool == "execute":
        cmd = (args.get("command") or args.get("cmd") or "")[:60]
        suffix = f" <code>{cmd}</code>" if cmd else ""
    elif tool == "task":
        desc = (args.get("description") or args.get("prompt") or "")[:60]
        suffix = f": {desc}…" if desc else "…"
    else:
        suffix = ""

    if suffix:
        return f"Ran <b>{label}</b>{suffix}"
    # Return raw args for not built-in tools
    return f"Ran <b>{label}</b><code>{args}</code>"


# ---------------------------------------------------------------------------
# Message splitting
# ---------------------------------------------------------------------------


def _split_message(content: str, max_len: int = _MAX_MESSAGE_LEN) -> list[str]:
    """Split *content* into chunks of at most *max_len* chars at line/word breaks."""
    if len(content) <= max_len:
        return [content]
    chunks: list[str] = []
    while content:
        if len(content) <= max_len:
            chunks.append(content)
            break
        cut = content[:max_len]
        pos = cut.rfind("\n")
        if pos == -1:
            pos = cut.rfind(" ")
        if pos == -1:
            pos = max_len
        chunks.append(content[:pos])
        content = content[pos:].lstrip()
    return chunks


# ---------------------------------------------------------------------------
# Channel
# ---------------------------------------------------------------------------


class TelegramChannel(BaseChannel):
    """
    Telegram bot channel backed by python-telegram-bot (PTB) v21+.

    Uses long-polling (no public IP / webhook required).
    Retries transient send errors up to 3 times with exponential back-off.

    Args:
        config: Telegram-specific section of LangclawConfig.channels.telegram.
    """

    name = "telegram"

    def __init__(self, config: TelegramChannelConfig) -> None:
        self._config = config
        self._app: object = None  # telegram.ext.Application
        self._session_manager = SessionManager()
        self._bus: BaseMessageBus | None = None
        self._running = False
        self._typing_tasks: dict[str, asyncio.Task] = {}
        # Keyed by tool_call_id; stashed on tool_progress, consumed on tool_result.
        self._tool_call_buffer: dict[str, dict] = {}

    def is_enabled(self) -> bool:
        return self._config.enabled and bool(self._config.token)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self, bus: BaseMessageBus) -> None:
        try:
            from telegram.ext import (
                Application,
                CommandHandler,
                MessageHandler,
                filters,
            )
            from telegram.request import HTTPXRequest
        except ImportError as exc:
            raise ImportError(
                "TelegramChannel requires 'langclaw[telegram]'. "
                "Install with: uv add 'langclaw[telegram]'"
            ) from exc

        self._bus = bus
        self._running = True

        # Larger connection pool + explicit timeouts prevent ConnectTimeout on
        # long-lived polling connections.
        req = HTTPXRequest(
            connection_pool_size=16,
            pool_timeout=5.0,
            connect_timeout=30.0,
            read_timeout=30.0,
            write_timeout=30.0,
        )
        app = (
            Application.builder()
            .token(self._config.token)
            .request(req)
            .get_updates_request(req)
            .build()
        )
        self._app = app

        app.add_error_handler(self._on_error)
        app.add_handler(CommandHandler("reset", self._handle_reset))
        app.add_handler(CommandHandler("start", self._handle_start))
        app.add_handler(CommandHandler("help", self._handle_help))
        app.add_handler(
            MessageHandler(filters.TEXT & ~filters.COMMAND, self._handle_message)
        )

        logger.info("TelegramChannel starting (polling mode)…")
        await app.initialize()
        await app.start()

        bot_info = await app.bot.get_me()
        logger.info(f"Telegram bot @{bot_info.username} connected.")

        try:
            from telegram import BotCommand

            await app.bot.set_my_commands(
                [
                    BotCommand("start", "Start the bot"),
                    BotCommand("help", "Show available commands"),
                    BotCommand("reset", "Start a fresh conversation"),
                ]
            )
        except Exception as e:
            logger.warning(f"Failed to register bot commands: {e}")

        await app.updater.start_polling(
            allowed_updates=["message"],
            drop_pending_updates=True,
        )

        # Keep the task alive until cancelled.
        while self._running:
            await asyncio.sleep(1)

    async def stop(self) -> None:
        self._running = False
        for chat_id in list(self._typing_tasks):
            self._stop_typing(chat_id)
        if self._app is not None:
            try:
                logger.info("Stopping TelegramChannel…")
                await self._app.updater.stop()  # type: ignore[attr-defined]
                await self._app.stop()  # type: ignore[attr-defined]
                await self._app.shutdown()  # type: ignore[attr-defined]
            except Exception:
                logger.exception("Error stopping TelegramChannel")
            self._app = None

    # ------------------------------------------------------------------
    # Outbound hooks
    # ------------------------------------------------------------------

    async def send_tool_progress(self, msg: OutboundMessage) -> None:
        """Stash the tool call info; actual rendering happens on tool_result."""
        tc_id = msg.metadata.get("tool_call_id", "")
        if tc_id:
            self._tool_call_buffer[tc_id] = {
                "tool": msg.metadata.get("tool", ""),
                "args": msg.metadata.get("args") or {},
            }

    async def send_tool_result(self, msg: OutboundMessage) -> None:
        """
        Pop the matching tool call from the buffer and render both as one message.

        Format:
          <header line>
          <blockquote expandable>raw tool output</blockquote>

        The blockquote content is truncated to keep the total message within
        Telegram's 4 096-char limit. The full result is always available in the
        agent's LangGraph state; this display is informational only.
        """
        if self._app is None:
            return
        tc_id = msg.metadata.get("tool_call_id", "")
        call_info = self._tool_call_buffer.pop(tc_id, {})
        header = _format_tool_progress(
            call_info.get("tool", ""),
            call_info.get("args") or {},
        )
        escaped = (
            msg.content.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        )
        # Leave room for header + blockquote tags + newline + safety margin.
        _BLOCKQUOTE_OVERHEAD = len("<blockquote expandable></blockquote>") + 1
        max_content = (
            _MAX_MESSAGE_LEN
            - len(header)
            - _BLOCKQUOTE_OVERHEAD
            - len(_TRUNCATION_SUFFIX)
        )
        if len(escaped) > max_content:
            escaped = escaped[:max_content] + _TRUNCATION_SUFFIX
        html = f"{header}\n<blockquote expandable>{escaped}</blockquote>"
        await self._send_progress(msg.context_id, html)

    async def send_ai_message(self, msg: OutboundMessage) -> None:
        """Stop the typing indicator and deliver the final AI response."""
        if self._app is None:
            return
        self._stop_typing(msg.context_id)
        if not msg.content:
            return
        for chunk in _split_message(msg.content):
            await self._send_chunk(msg.context_id, chunk)

    @retry(
        retry=retry_if_exception_type(Exception),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        stop=stop_after_attempt(3),
        reraise=True,
    )
    async def _send_chunk(self, chat_id: str, text: str) -> None:
        """Send a single chunk with HTML → plain-text fallback and tenacity retry."""
        try:
            from telegram.error import BadRequest
        except ImportError:
            BadRequest = Exception  # type: ignore[misc,assignment]

        bot = self._app.bot  # type: ignore[attr-defined]

        html = _markdown_to_telegram_html(text)
        try:
            await bot.send_message(chat_id=chat_id, text=html, parse_mode="HTML")
        except BadRequest as exc:
            if "can't parse" in str(exc).lower() or "parse" in str(exc).lower():
                logger.warning(f"HTML parse failed ({exc}), retrying as plain text.")
                await bot.send_message(chat_id=chat_id, text=text)
            else:
                raise

    async def _send_progress(self, chat_id: str, html: str) -> None:
        """Send a small tool-progress status line (best-effort, no retry)."""
        if not self._app:
            return
        try:
            await self._app.bot.send_message(  # type: ignore[attr-defined]
                chat_id=chat_id, text=html, parse_mode="HTML"
            )
        except Exception as exc:
            logger.debug(f"Tool-progress send failed for {chat_id}: {exc}")

    # ------------------------------------------------------------------
    # Typing indicator
    # ------------------------------------------------------------------

    def _start_typing(self, chat_id: str) -> None:
        """Start a looping 'typing…' chat action for *chat_id*."""
        self._stop_typing(chat_id)
        self._typing_tasks[chat_id] = asyncio.create_task(
            self._typing_loop(chat_id), name=f"typing:{chat_id}"
        )

    def _stop_typing(self, chat_id: str) -> None:
        """Cancel the typing indicator for *chat_id*."""
        task = self._typing_tasks.pop(chat_id, None)
        if task and not task.done():
            task.cancel()

    async def _typing_loop(self, chat_id: str) -> None:
        """Repeatedly broadcast 'typing' every 4 s until cancelled."""
        try:
            while self._app:
                await self._app.bot.send_chat_action(  # type: ignore[attr-defined]
                    chat_id=int(chat_id), action="typing"
                )
                await asyncio.sleep(4)
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.debug(f"Typing indicator stopped for {chat_id}: {exc}")

    # ------------------------------------------------------------------
    # PTB error handler
    # ------------------------------------------------------------------

    async def _on_error(self, update: object, context: object) -> None:
        """Log PTB polling / handler errors."""
        err = getattr(context, "error", context)
        logger.error(f"Telegram error: {err}")

    # ------------------------------------------------------------------
    # PTB message handlers
    # ------------------------------------------------------------------

    async def _handle_message(self, update: object, context: object) -> None:
        """Forward an incoming Telegram text message to the bus."""
        from telegram import Update as TGUpdate

        if not isinstance(update, TGUpdate) or not update.message:
            return

        user = update.message.from_user
        if not user:
            return

        user_id = str(user.id)
        if not self._is_allowed(user_id, user.username):
            await update.message.reply_text(
                "Sorry, you are not authorised to use this bot."
            )
            return

        chat_id = str(update.message.chat_id)
        text = update.message.text or ""

        if self._bus is None:
            return

        self._start_typing(chat_id)

        await self._bus.publish(
            InboundMessage(
                channel=self.name,
                user_id=user_id,
                context_id=chat_id,
                content=text,
                metadata={
                    "source": "channel",
                    "platform": "telegram",
                    "username": user.username or "",
                    "message_id": update.message.message_id,
                    "is_group": update.message.chat.type != "private",
                },
            )
        )

    async def _handle_reset(self, update: object, context: object) -> None:
        """Clear the conversation thread for this user."""
        from telegram import Update as TGUpdate

        if not isinstance(update, TGUpdate) or not update.message:
            return

        user = update.message.from_user
        if not user:
            return

        chat_id = str(update.message.chat_id)
        deleted = await self._session_manager.delete_thread(
            channel=self.name,
            user_id=str(user.id),
            context_id=chat_id,
        )
        reply = (
            "Conversation reset. Starting fresh!" if deleted else "Nothing to reset."
        )
        await update.message.reply_text(reply)

    async def _handle_start(self, update: object, context: object) -> None:
        from telegram import Update as TGUpdate

        if not isinstance(update, TGUpdate) or not update.message:
            return

        user = update.message.from_user
        name = user.first_name if user else "there"
        await update.message.reply_text(
            f"Hi {name}! I'm powered by langclaw.\n\n"
            "Send me a message to get started.\n"
            "Use /reset to start a fresh conversation."
        )

    async def _handle_help(self, update: object, context: object) -> None:
        from telegram import Update as TGUpdate

        if not isinstance(update, TGUpdate) or not update.message:
            return

        await update.message.reply_text(
            "/start  — say hello\n"
            "/reset  — clear conversation history\n"
            "/help   — show this message"
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _is_allowed(self, user_id: str, username: str | None) -> bool:
        """Return True if the user passes the allow_from whitelist check."""
        if not self._config.allow_from:
            return True
        allowed = set(self._config.allow_from)
        return user_id in allowed or (username is not None and username in allowed)
