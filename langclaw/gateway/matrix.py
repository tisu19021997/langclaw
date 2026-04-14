"""
MatrixChannel — Gateway channel for the Matrix protocol.

Requires: langclaw[matrix]  →  uv add "langclaw[matrix]"

Features:
- Handles direct messages (rooms with ≤2 joined members) and group rooms
- In group rooms, replies only when @-mentioned
- Converts Markdown to Matrix-safe HTML (``formatted_body``)
- Splits messages that exceed a safe 32 KiB ceiling
- Auto-joins invitations from allow-listed senders (configurable)
- Respects ``allow_from`` user whitelist (Matrix user IDs)
- ``/start``, ``/help``, ``/reset`` command support via ``CommandRouter``
- Attachment support (images, audio, video, generic files) for unencrypted rooms
- Defers E2EE rooms to a future release — aborts startup if ``e2ee_enabled=True``
"""

from __future__ import annotations

import asyncio
import base64
import mimetypes
import re
from pathlib import Path
from typing import Any

from loguru import logger

from langclaw.bus.base import BaseMessageBus, InboundMessage, OutboundMessage
from langclaw.config.schema import MatrixChannelConfig
from langclaw.cron.utils import is_cron_context_id
from langclaw.gateway.base import BaseChannel
from langclaw.gateway.commands import CommandContext
from langclaw.gateway.utils import (
    TRUNCATION_SUFFIX,
    format_tool_progress,
    is_allowed,
    make_attachment,
    split_message,
)

# Matrix has no hard size limit per event, but the spec recommends keeping
# events well under 64 KiB. 32 KiB leaves headroom for HTML formatting +
# JSON envelope overhead.
_MAX_MESSAGE_LEN = 32_000

# 20 MB ceiling for inbound media downloads — mirrors SlackChannel.
_MAX_ATTACHMENT_BYTES = 20 * 1024 * 1024


# ---------------------------------------------------------------------------
# Markdown → Matrix HTML
# ---------------------------------------------------------------------------


def _markdown_to_matrix_html(text: str) -> str:
    """Convert a Markdown string to Matrix-safe HTML.

    Matrix supports a wider HTML subset than Telegram (see MSC Spec §13.2.1.7),
    including headings, blockquotes, and lists — so the renderer is simpler
    than the Telegram counterpart: preserve structure, just escape + translate.
    """
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

    # 3. Escape HTML special characters
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    # 4. Headings (Matrix supports h1–h6)
    for i in range(6, 0, -1):
        pattern = r"^" + "#" * i + r"\s+(.+)$"
        text = re.sub(pattern, rf"<h{i}>\1</h{i}>", text, flags=re.MULTILINE)

    # 5. Blockquotes (Matrix supports <blockquote>)
    text = re.sub(r"^>\s*(.*)$", r"<blockquote>\1</blockquote>", text, flags=re.MULTILINE)

    # 6. Links [text](url)
    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', text)

    # 7. Bold
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"__(.+?)__", r"<b>\1</b>", text)

    # 8. Italic (avoid matching inside snake_case identifiers)
    text = re.sub(r"(?<![a-zA-Z0-9])_([^_]+)_(?![a-zA-Z0-9])", r"<i>\1</i>", text)

    # 9. Strikethrough
    text = re.sub(r"~~(.+?)~~", r"<s>\1</s>", text)

    # 10. Bullet lists (simple — treat each line as its own <li>)
    text = re.sub(r"^[-*]\s+(.+)$", r"<li>\1</li>", text, flags=re.MULTILINE)

    # 11. Restore inline code
    for i, code in enumerate(inline_codes):
        escaped = code.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        text = text.replace(f"\x00IC{i}\x00", f"<code>{escaped}</code>")

    # 12. Restore fenced code blocks
    for i, code in enumerate(code_blocks):
        escaped = code.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        text = text.replace(f"\x00CB{i}\x00", f"<pre><code>{escaped}</code></pre>")

    # 13. Convert remaining newlines to <br> (Matrix clients respect <br>)
    text = text.replace("\n", "<br/>")

    return text


# ---------------------------------------------------------------------------
# Channel
# ---------------------------------------------------------------------------


class MatrixChannel(BaseChannel):
    """
    Matrix bot channel backed by matrix-nio.

    Uses long-polling via :pymeth:`nio.AsyncClient.sync_forever`, so no public
    IP, webhook, or appservice registration is required. Authentication is
    token-based (``access_token`` + ``device_id``); password login is
    intentionally omitted in this release to keep secret management consistent
    with the other channels.

    Args:
        config: Matrix-specific section of ``LangclawConfig.channels.matrix``.
    """

    name = "matrix"

    def __init__(self, config: MatrixChannelConfig) -> None:
        self._config = config
        self._client: Any = None  # nio.AsyncClient (imported lazily in start())
        self._bus: BaseMessageBus | None = None
        self._sync_task: asyncio.Task | None = None
        self._running = False
        # Keyed by tool_call_id; stashed on tool_progress, consumed on tool_result.
        self._tool_call_buffer: dict[str, dict] = {}
        # Tracks which rooms are DMs so we don't fetch membership on every event.
        self._dm_cache: dict[str, bool] = {}
        # Monotonic timestamp (ms) marking channel startup — events older than
        # this are ignored (catch-up noise after a restart).
        self._started_after_ms: int = 0

    def is_enabled(self) -> bool:
        return self._config.enabled and all(
            [
                self._config.homeserver_url,
                self._config.user_id,
                self._config.access_token,
                self._config.device_id,
            ]
        )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self, bus: BaseMessageBus) -> None:
        try:
            from nio import (
                AsyncClient,
                AsyncClientConfig,
                InviteMemberEvent,
                RoomMessageAudio,
                RoomMessageFile,
                RoomMessageImage,
                RoomMessageText,
                RoomMessageVideo,
            )
        except ImportError as exc:
            raise ImportError(
                "MatrixChannel requires 'langclaw[matrix]'. Install with: uv add 'langclaw[matrix]'"
            ) from exc

        if self._config.e2ee_enabled:
            raise RuntimeError(
                "MatrixChannel: e2ee_enabled=True is reserved for a future release "
                "and currently not supported. Set e2ee_enabled=False (default) to "
                "start the channel in unencrypted mode, or disable the channel."
            )

        self._bus = bus
        self._running = True
        self._started_after_ms = int(asyncio.get_event_loop().time() * 1000)

        store_path = (
            Path(self._config.store_path).expanduser()
            if self._config.store_path
            else Path.home() / ".langclaw" / "matrix_store"
        )
        store_path.mkdir(parents=True, exist_ok=True)

        client_config = AsyncClientConfig(
            store_sync_tokens=True,
            encryption_enabled=False,
        )
        client = AsyncClient(
            homeserver=self._config.homeserver_url,
            user=self._config.user_id,
            device_id=self._config.device_id,
            store_path=str(store_path),
            config=client_config,
        )
        client.access_token = self._config.access_token
        client.user_id = self._config.user_id
        self._client = client

        # Verify the token is valid before entering the sync loop.
        try:
            whoami = await client.whoami()
            if hasattr(whoami, "user_id") and whoami.user_id:
                logger.info(f"Matrix bot connected as {whoami.user_id}")
            else:
                logger.warning(f"Matrix whoami returned unexpected response: {whoami}")
        except Exception as exc:
            logger.error(f"Matrix whoami failed — access_token may be invalid: {exc}")
            raise

        client.add_event_callback(self._on_text, RoomMessageText)
        client.add_event_callback(
            self._on_media,
            (RoomMessageImage, RoomMessageFile, RoomMessageAudio, RoomMessageVideo),
        )
        if self._config.auto_join_invites:
            client.add_event_callback(self._on_invite, InviteMemberEvent)

        logger.info("MatrixChannel starting (sync_forever)…")
        self._sync_task = asyncio.create_task(
            client.sync_forever(timeout=30_000, full_state=False),
            name="matrix-sync",
        )

        # Keep the task alive until cancelled.
        while self._running:
            await asyncio.sleep(1)

    async def stop(self) -> None:
        self._running = False
        if self._sync_task is not None:
            self._sync_task.cancel()
            try:
                await self._sync_task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._sync_task = None
        if self._client is not None:
            try:
                logger.info("Stopping MatrixChannel…")
                await self._client.close()
            except Exception:
                logger.exception("Error stopping MatrixChannel")
            self._client = None

    # ------------------------------------------------------------------
    # Outbound hooks
    # ------------------------------------------------------------------

    async def send_tool_progress(self, msg: OutboundMessage) -> None:
        """Stash the tool call info; actual rendering happens on tool_result."""
        if is_cron_context_id(msg.context_id):
            return
        tc_id = msg.metadata.get("tool_call_id", "")
        if tc_id:
            self._tool_call_buffer[tc_id] = {
                "tool": msg.metadata.get("tool", ""),
                "args": msg.metadata.get("args") or {},
            }

    async def send_tool_result(self, msg: OutboundMessage) -> None:
        """Pop the matching tool call and render both as one message."""
        if self._client is None or is_cron_context_id(msg.context_id):
            return
        tc_id = msg.metadata.get("tool_call_id", "")
        call_info = self._tool_call_buffer.pop(tc_id, {})
        header = format_tool_progress(
            call_info.get("tool", ""),
            call_info.get("args") or {},
            markup="html",
        )
        escaped = msg.content.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        _BLOCK_OVERHEAD = len("<pre><code></code></pre>") + 1
        max_content = _MAX_MESSAGE_LEN - len(header) - _BLOCK_OVERHEAD - len(TRUNCATION_SUFFIX)
        if len(escaped) > max_content:
            escaped = escaped[:max_content] + TRUNCATION_SUFFIX
        html = f"{header}<br/><pre><code>{escaped}</code></pre>"
        plain = f"{_strip_tags(header)}\n{msg.content[:max_content]}"
        await self._send_html(msg.chat_id, plain=plain, html=html)

    async def send_ai_message(self, msg: OutboundMessage) -> None:
        """Deliver the final AI response."""
        if self._client is None or not msg.content:
            return
        for chunk in split_message(msg.content, max_len=_MAX_MESSAGE_LEN):
            html = _markdown_to_matrix_html(chunk)
            await self._send_html(msg.chat_id, plain=chunk, html=html)

    # ------------------------------------------------------------------
    # Sending helpers
    # ------------------------------------------------------------------

    async def _send_html(self, room_id: str, *, plain: str, html: str) -> None:
        """Send a formatted message with plaintext fallback."""
        if not self._client or not room_id:
            return
        content = {
            "msgtype": "m.text",
            "body": plain,
            "format": "org.matrix.custom.html",
            "formatted_body": html,
        }
        try:
            await self._client.room_send(
                room_id=room_id,
                message_type="m.room.message",
                content=content,
                ignore_unverified_devices=True,
            )
        except Exception as exc:
            logger.error(f"Matrix room_send failed for {room_id}: {exc}")

    # ------------------------------------------------------------------
    # Inbound callbacks
    # ------------------------------------------------------------------

    async def _on_text(self, room: Any, event: Any) -> None:
        """Forward an incoming Matrix text event to the bus or command router."""
        if not self._should_process(room, event):
            return

        body = (getattr(event, "body", "") or "").strip()
        is_dm = self._is_dm(room)

        # In group rooms, only respond to @-mentions. Strip the mention from the body.
        if not is_dm:
            mentioned, body = self._strip_mention(event, body)
            if not mentioned:
                return

        # Command handling (/start, /help, /reset, …)
        if body.startswith("/"):
            parts = body.split()
            cmd = parts[0].lstrip("/").lower() if parts else ""
            args = parts[1:] if len(parts) > 1 else []
            if cmd and self._command_router is not None:
                ctx = CommandContext(
                    channel=self.name,
                    user_id=event.sender,
                    context_id=room.room_id,
                    chat_id=room.room_id,
                    args=args,
                    display_name=self._display_name(room, event.sender),
                )
                response = await self._command_router.dispatch(cmd, ctx)
                for chunk in split_message(response, max_len=_MAX_MESSAGE_LEN):
                    await self._send_html(
                        room.room_id, plain=chunk, html=_markdown_to_matrix_html(chunk)
                    )
                return

        if self._bus is None:
            return

        await self._bus.publish(
            InboundMessage(
                channel=self.name,
                user_id=event.sender,
                context_id=room.room_id,
                chat_id=room.room_id,
                content=body,
                origin="channel",
                metadata={
                    "platform": "matrix",
                    "event_id": getattr(event, "event_id", ""),
                    "is_group": not is_dm,
                    "room_name": getattr(room, "display_name", "") or room.room_id,
                },
            )
        )

    async def _on_media(self, room: Any, event: Any) -> None:
        """Forward an incoming Matrix media event to the bus."""
        if not self._should_process(room, event):
            return

        # Skip encrypted rooms — E2EE attachment decryption is out of scope for v1.
        if getattr(room, "encrypted", False):
            logger.debug(
                f"Skipping encrypted media in room {room.room_id} (E2EE not yet supported)"
            )
            return

        url = getattr(event, "url", "") or ""
        if not url.startswith("mxc://"):
            return

        file_size = 0
        info = getattr(event, "source", {}).get("content", {}).get("info", {}) or {}
        if isinstance(info, dict):
            file_size = int(info.get("size", 0) or 0)
        if file_size and file_size > _MAX_ATTACHMENT_BYTES:
            logger.info(
                f"Matrix media too large ({file_size} bytes > {_MAX_ATTACHMENT_BYTES}); skipping"
            )
            return

        mime_type = ""
        filename = getattr(event, "body", "") or ""
        if isinstance(info, dict):
            mime_type = info.get("mimetype", "") or ""
        if not mime_type and filename:
            mime_type = mimetypes.guess_type(filename)[0] or ""

        data_b64 = ""
        try:
            # nio's download() parses the mxc URI internally.
            resp = await self._client.download(mxc=url)
            raw = getattr(resp, "body", None)
            if isinstance(raw, (bytes, bytearray)) and raw:
                data_b64 = base64.b64encode(bytes(raw)).decode("ascii")
                if not mime_type:
                    mime_type = getattr(resp, "content_type", "") or "application/octet-stream"
                if not file_size:
                    file_size = len(raw)
        except Exception as exc:
            logger.warning(f"Matrix media download failed for {url}: {exc}")
            return

        if not data_b64:
            return

        attachment = make_attachment(
            data=data_b64,
            mime_type=mime_type or "application/octet-stream",
            filename=filename or "attachment",
            size=file_size,
        )

        if self._bus is None:
            return

        is_dm = self._is_dm(room)
        await self._bus.publish(
            InboundMessage(
                channel=self.name,
                user_id=event.sender,
                context_id=room.room_id,
                chat_id=room.room_id,
                content="",
                origin="channel",
                attachments=[attachment],
                metadata={
                    "platform": "matrix",
                    "event_id": getattr(event, "event_id", ""),
                    "is_group": not is_dm,
                    "room_name": getattr(room, "display_name", "") or room.room_id,
                },
            )
        )

    async def _on_invite(self, room: Any, event: Any) -> None:
        """Auto-join rooms the bot is invited to, respecting ``allow_from``."""
        if self._client is None:
            return
        # Only react to invites addressed to us.
        if getattr(event, "state_key", None) != self._config.user_id:
            return
        if getattr(event, "membership", None) != "invite":
            return
        inviter = getattr(event, "sender", "")
        if not self._is_allowed(inviter):
            logger.info(f"Ignoring invite to {room.room_id} from non-allowed user {inviter}")
            return
        try:
            await self._client.join(room.room_id)
            logger.info(f"Auto-joined Matrix room {room.room_id} (invited by {inviter})")
        except Exception as exc:
            logger.warning(f"Failed to auto-join {room.room_id}: {exc}")

    # ------------------------------------------------------------------
    # Filters & helpers
    # ------------------------------------------------------------------

    def _should_process(self, room: Any, event: Any) -> bool:
        """Drop events from self, stale catch-up events, or blocked users."""
        sender = getattr(event, "sender", "") or ""
        if not sender or sender == self._config.user_id:
            return False
        if not self._is_allowed(sender):
            logger.debug(
                f"Dropping Matrix event from non-allow-listed user {sender} in {room.room_id}"
            )
            return False
        # Drop events that arrived before startup (nio replays the sync window).
        ts = int(getattr(event, "server_timestamp", 0) or 0)
        if ts and ts < self._started_after_ms:
            return False
        return True

    def _is_dm(self, room: Any) -> bool:
        """Return True when *room* has ≤2 joined members (i.e. a direct chat)."""
        cached = self._dm_cache.get(room.room_id)
        if cached is not None:
            return cached
        joined = getattr(room, "users", None) or getattr(room, "joined_users", None) or {}
        is_dm = len(joined) <= 2
        self._dm_cache[room.room_id] = is_dm
        return is_dm

    def _strip_mention(self, event: Any, body: str) -> tuple[bool, str]:
        """Detect bot mentions and return ``(mentioned, body_without_mention)``."""
        bot = self._config.user_id
        # 1. Structured mention via m.mentions
        source = getattr(event, "source", {}) or {}
        content = source.get("content", {}) if isinstance(source, dict) else {}
        mentions = content.get("m.mentions") if isinstance(content, dict) else None
        if isinstance(mentions, dict) and bot in (mentions.get("user_ids") or []):
            return True, body
        # 2. matrix.to link in the formatted body
        formatted = content.get("formatted_body", "") if isinstance(content, dict) else ""
        if isinstance(formatted, str) and f"matrix.to/#/{bot}" in formatted:
            return True, body
        # 3. Fallback: localpart or full MXID in the plaintext body
        localpart = bot.split(":", 1)[0].lstrip("@")
        prefix_patterns = [
            rf"^\s*{re.escape(bot)}[:,]?\s*",
            rf"^\s*@{re.escape(localpart)}[:,]?\s*",
        ]
        for pattern in prefix_patterns:
            new_body, n = re.subn(pattern, "", body, count=1, flags=re.IGNORECASE)
            if n:
                return True, new_body.strip()
        if bot in body or localpart in body:
            return True, body
        return False, body

    def _display_name(self, room: Any, user_id: str) -> str:
        """Best-effort display name for a user in a room (falls back to MXID)."""
        try:
            users = getattr(room, "users", {}) or {}
            member = users.get(user_id)
            if member is not None:
                name = getattr(member, "display_name", "") or getattr(member, "name", "")
                if name:
                    return str(name)
        except Exception:
            pass
        return user_id

    def _is_allowed(self, user_id: str) -> bool:
        """Return True if *user_id* passes the ``allow_from`` whitelist check."""
        return is_allowed(self._config.allow_from, user_id)


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


_TAG_RE = re.compile(r"<[^>]+>")


def _strip_tags(html: str) -> str:
    """Remove HTML tags — used to build plaintext fallback bodies."""
    return _TAG_RE.sub("", html)
