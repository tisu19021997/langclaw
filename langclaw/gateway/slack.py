"""
SlackChannel — Gateway channel for Slack using Socket Mode.

Requires: langclaw[slack]  →  uv add "langclaw[slack]"

Features:
- Socket Mode (WebSocket) - no public URL needed
- Handles direct messages and app mentions
- Splits messages that exceed Slack's limits
- Respects allow_from user whitelist (user IDs or usernames)
- /start, /help, /reset, /cron command support
- Tool-progress / tool-result rendering with code-block formatting
- Automatic reconnect on socket disconnects
- File attachment support
- Reaction emoji UX feedback (configurable):
  * 👀 (eyes) when message is received → "I'm working on it"
  * ✅ (checkmark) when response is sent → "done"
"""

from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path
from typing import Any

from tenacity import retry, stop_after_attempt, wait_exponential

from langclaw.bus.base import BaseMessageBus, InboundMessage, OutboundMessage
from langclaw.config.schema import SlackChannelConfig
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

logger = logging.getLogger(__name__)

MAX_MESSAGE_LEN = 3000  # Slack has a 3000 char limit for text blocks
MAX_ATTACHMENT_BYTES = 20 * 1024 * 1024  # 20 MB


# ---------------------------------------------------------------------------
# Channel
# ---------------------------------------------------------------------------


class SlackChannel(BaseChannel):
    """
    Slack bot channel using Socket Mode (WebSocket).

    Uses the bolt-python framework with Socket Mode adapter for receiving
    messages. Reconnection is handled automatically by the socket mode handler.

    Args:
        config: Slack-specific section of LangclawConfig.channels.slack.
    """

    name = "slack"

    def __init__(self, config: SlackChannelConfig) -> None:
        self._config = config
        self._app: Any = None
        self._handler: Any = None
        self._bus: BaseMessageBus | None = None
        self._running = False
        self._tool_call_buffer: dict[str, dict] = {}
        # Track (channel_id, message_ts) pairs for reaction management
        self._reaction_tracking: dict[str, tuple[str, str]] = {}  # context_id -> (channel, ts)
        # In-memory cache for user_id -> username to avoid rate-limiting users_info
        self._user_cache: dict[str, str] = {}
        # Bot user ID for stripping mentions from app_mention events
        self._bot_user_id: str | None = None

    def is_enabled(self) -> bool:
        return (
            self._config.enabled and bool(self._config.bot_token) and bool(self._config.app_token)
        )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self, bus: BaseMessageBus) -> None:
        try:
            from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler
            from slack_bolt.async_app import AsyncApp
        except ImportError as exc:
            raise ImportError(
                "SlackChannel requires 'langclaw[slack]'. Install with: uv add 'langclaw[slack]'"
            ) from exc

        self._bus = bus
        self._running = True

        # Initialize Slack app
        app = AsyncApp(token=self._config.bot_token)
        self._app = app

        # Fetch bot user ID for mention stripping
        try:
            auth_response = await app.client.auth_test()
            self._bot_user_id = auth_response.get("user_id")
            logger.info(f"Slack bot connected as {self._bot_user_id}")  
        except Exception as exc:
            logger.warning(f"Failed to fetch bot user ID: {exc}")

        # Register event handlers
        @app.event("message")
        async def handle_message(event: dict, say: Any) -> None:
            # Handle DMs or messages that mention the bot
            # (app_mention is preferred, but this provides fallback if not subscribed)
            is_dm = event.get("channel_type") == "im"
            has_mention = self._bot_user_id and f"<@{self._bot_user_id}>" in (event.get("text") or "")

            if not is_dm and not has_mention:
                return

            # Ignore message subtypes except file_share
            subtype = event.get("subtype")
            if subtype and subtype not in ["file_share"]:
                return
            await self._on_message(event)

        @app.event("app_mention")
        async def handle_mention(event: dict, say: Any) -> None:
            await self._on_message(event)

        # Register slash commands if command router exists
        if self._command_router:
            for entry in self._command_router.list_commands():
                self._register_slash_command(app, entry.name, entry.description or entry.name)

        logger.info(
            f"SlackChannel starting… "
            f"(reaction_feedback={'enabled' if self._config.reaction_feedback_enabled else 'disabled'})"
        )

        # Start socket mode handler
        handler = AsyncSocketModeHandler(app, self._config.app_token)
        self._handler = handler

        try:
            await handler.start_async()
        except Exception as exc:
            logger.error(f"Failed to start Slack Socket Mode: {exc}")
            raise

        # Keep the task alive until cancelled (mirrors TelegramChannel pattern)
        while self._running:
            await asyncio.sleep(1)

    async def stop(self) -> None:
        self._running = False
        if self._handler is not None:
            try:
                logger.info("Stopping SlackChannel…")
                await self._handler.close_async()
            except Exception:
                logger.exception("Error stopping SlackChannel")
            self._handler = None
        self._app = None

    # ------------------------------------------------------------------
    # Outbound hooks
    # ------------------------------------------------------------------

    async def send_tool_progress(self, msg: OutboundMessage) -> None:
        """Stash the tool call info; rendered together with tool_result."""
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
        if self._app is None or is_cron_context_id(msg.context_id):
            return

        tc_id = msg.metadata.get("tool_call_id", "")
        call_info = self._tool_call_buffer.pop(tc_id, {})
        header = format_tool_progress(
            call_info.get("tool", ""),
            call_info.get("args") or {},
            markup="markdown",
        )

        # Truncate result to fit within Slack's limit
        _CODE_BLOCK_OVERHEAD = len("```\n\n```") + 1
        max_content = MAX_MESSAGE_LEN - len(header) - _CODE_BLOCK_OVERHEAD - len(TRUNCATION_SUFFIX)
        result_text = msg.content or ""
        if len(result_text) > max_content:
            result_text = result_text[:max_content] + TRUNCATION_SUFFIX

        text = f"{header}\n```\n{result_text}\n```"
        thread_ts = msg.metadata.get("thread_ts")
        await self._send_text(msg.chat_id, text, thread_ts=thread_ts)

    async def send_ai_message(self, msg: OutboundMessage) -> None:
        """Deliver the final AI response."""
        if self._app is None:
            return
        if not msg.content:
            return

        try:
            from slackify_markdown import slackify_markdown
        except ImportError:
            slackify_markdown = None  # type: ignore

        thread_ts = (msg.metadata or {}).get("thread_ts")
        # Convert markdown to Slack's mrkdwn format
        text = slackify_markdown(msg.content) if slackify_markdown else msg.content
        for chunk in split_message(text, max_len=MAX_MESSAGE_LEN):
            await self._send_text(msg.chat_id, chunk, thread_ts=thread_ts)

        # Update reaction: 👀 → ✅
        if self._config.reaction_feedback_enabled:
            await self._swap_reaction(msg.context_id)

    # ------------------------------------------------------------------
    # Sending helpers
    # ------------------------------------------------------------------

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
    )
    async def _send_text(
        self,
        channel_id: str,
        text: str,
        thread_ts: str | None = None,
    ) -> None:
        """Send a text message to a Slack channel."""
        if not self._app:
            return

        try:
            kwargs: dict[str, Any] = {
                "channel": channel_id,
                "text": text,
            }
            if thread_ts:
                kwargs["thread_ts"] = thread_ts

            await self._app.client.chat_postMessage(**kwargs)
        except Exception as exc:
            logger.error(f"Failed to send Slack message: {exc}")

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
    )
    async def _add_reaction(self, channel: str, timestamp: str, emoji: str) -> None:
        """Add a reaction emoji to a message."""
        if not self._app:
            return

        try:
            await self._app.client.reactions_add(
                channel=channel,
                timestamp=timestamp,
                name=emoji,
            )
        except Exception as exc:
            # Extract Slack API error code if available
            slack_error = None
            if hasattr(exc, "response") and isinstance(exc.response, dict):
                slack_error = exc.response.get("error")

            # Silently ignore common non-critical errors
            if slack_error in ["already_reacted", "no_reaction"]:
                return

            # Log actionable errors
            if slack_error == "missing_scope":
                logger.warning(f"Reaction failed: missing 'reactions:write' scope")
            else:
                logger.debug(f"Failed to add reaction '{emoji}': {slack_error or exc}")

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
    )
    async def _remove_reaction(self, channel: str, timestamp: str, emoji: str) -> None:
        """Remove a reaction emoji from a message."""
        if not self._app:
            return

        try:
            await self._app.client.reactions_remove(
                channel=channel,
                timestamp=timestamp,
                name=emoji,
            )
        except Exception as exc:
            # Extract Slack API error code if available
            slack_error = None
            if hasattr(exc, "response") and isinstance(exc.response, dict):
                slack_error = exc.response.get("error")

            # Silently ignore common non-critical errors
            if slack_error in ["no_reaction", "already_reacted"]:
                return

            # Log actionable errors
            if slack_error == "missing_scope":
                logger.warning(f"Reaction failed: missing 'reactions:write' scope")
            else:
                logger.debug(f"Failed to remove reaction '{emoji}': {slack_error or exc}")

    async def _swap_reaction(self, context_id: str) -> None:
        """Swap processing reaction (👀) for complete reaction (✅)."""
        tracking = self._reaction_tracking.pop(context_id, None)
        if not tracking:
            return

        channel, timestamp = tracking
        await self._remove_reaction(channel, timestamp, self._config.reaction_processing)
        await self._add_reaction(channel, timestamp, self._config.reaction_complete)

    # ------------------------------------------------------------------
    # Inbound message handling
    # ------------------------------------------------------------------

    async def _on_message(self, event: dict) -> None:
        """Route an incoming Slack message to the command router or bus."""
        # Ignore bot messages
        if event.get("bot_id") or event.get("bot_profile"):
            return

        # Ignore message subtypes except file_share
        if event.get("subtype") and event.get("subtype") not in ("file_share",):
            return

        user_id = event.get("user", "")
        channel_id = event.get("channel", "")
        text = event.get("text", "")
        message_ts = event.get("ts", "")
        # DMs don't need thread replies; channel mentions always reply in thread
        is_dm = event.get("channel_type") == "im"
        thread_ts = None if is_dm else (event.get("thread_ts") or event.get("ts"))

        # Strip bot mention markup from app_mention events
        if self._bot_user_id:
            text = re.sub(rf"<@{re.escape(self._bot_user_id)}>\s*", "", text).strip()

        if not user_id or not channel_id:
            logger.debug("Slack message dropped: incomplete event data")
            return

        # Add 👀 reaction immediately to signal "processing"
        if self._config.reaction_feedback_enabled and message_ts:
            self._reaction_tracking[channel_id] = (channel_id, message_ts)
            await self._add_reaction(channel_id, message_ts, self._config.reaction_processing)

        # Get user info for username (with in-memory cache to avoid rate limits)
        username = self._user_cache.get(user_id, "")
        if not username:
            try:
                if self._app:
                    user_info = await self._app.client.users_info(user=user_id)
                    username = user_info.get("user", {}).get("name", "")
                    if username:
                        self._user_cache[user_id] = username
            except Exception as exc:
                logger.debug(f"Failed to fetch Slack user info: {exc}")

        # Check allow_from whitelist
        if not self._is_allowed(user_id, username):
            logger.warning(
                f"Slack user {user_id} ({username}) not in allow_from — dropping message"
            )
            try:
                await self._send_text(
                    channel_id,
                    "Sorry, you are not authorized to use this bot.",
                    thread_ts=thread_ts,
                )
            except Exception as exc:
                logger.debug(f"Failed to send 'not authorized' reply: {exc}")
            return

        # -- Command handling (/start, /help, /reset, /cron) --
        stripped = text.strip()
        if stripped.startswith("/"):
            parts = stripped.split()
            cmd = parts[0].lstrip("/").lower() if parts else ""
            args = parts[1:] if len(parts) > 1 else []

            if cmd and self._command_router is not None:
                ctx = CommandContext(
                    channel=self.name,
                    user_id=user_id,
                    context_id=channel_id,
                    chat_id=channel_id,
                    args=args,
                    display_name=username or user_id,
                )
                response = await self._command_router.dispatch(cmd, ctx)
                try:
                    await self._send_text(channel_id, response, thread_ts=thread_ts)
                except Exception as exc:
                    logger.error(f"Failed to send command response: {exc}")

                # Update reaction: 👀 → ✅ for command responses
                if self._config.reaction_feedback_enabled:
                    await self._swap_reaction(channel_id)
                return

        if self._bus is None:
            return

        # -- Attachment handling --
        from langclaw.bus.base import Attachment

        try:
            import aiohttp
        except ImportError:
            aiohttp = None  # type: ignore

        content_parts = [text] if text else []
        msg_attachments: list[Attachment] = []
        media_dir = Path.home() / ".langclaw" / "media"

        files = event.get("files", [])
        for file_info in files:
            file_size = file_info.get("size", 0)
            if file_size > MAX_ATTACHMENT_BYTES:
                content_parts.append(f"[attachment: {file_info.get('name', 'file')} - too large]")
                continue

            # Download file
            try:
                media_dir.mkdir(parents=True, exist_ok=True)
                file_name = file_info.get("name", "unknown")
                file_id = file_info.get("id", "")
                file_path = media_dir / f"{file_id}_{file_name.replace('/', '_')}"

                # Get download URL (private URL requires auth)
                url_private = file_info.get("url_private")
                if url_private and self._app and aiohttp:
                    headers = {"Authorization": f"Bearer {self._config.bot_token}"}
                    async with aiohttp.ClientSession() as session:
                        async with session.get(url_private, headers=headers) as resp:
                            if resp.status == 200:
                                data = await resp.read()
                                await asyncio.to_thread(file_path.write_bytes, data)
                                msg_attachments.append(
                                    make_attachment(
                                        file_path=file_path,
                                        filename=file_name,
                                        size=file_size,
                                    )
                                )
                            else:
                                content_parts.append(f"[attachment: {file_name} - download failed]")
            except Exception as exc:
                logger.warning(f"Failed to download Slack attachment: {exc}")
                file_name = file_info.get("name", "file")
                content_parts.append(f"[attachment: {file_name} - download failed]")

        # Thread-scoped context for channels, channel-scoped for DMs
        if is_dm:
            context_id = channel_id
        elif thread_ts:
            context_id = f"{channel_id}:{thread_ts}"
        else:
            context_id = f"{channel_id}:{message_ts}"

        await self._bus.publish(
            InboundMessage(
                channel=self.name,
                user_id=user_id,
                context_id=context_id,
                chat_id=channel_id,
                content="\n".join(p for p in content_parts if p) or "[empty message]",
                origin="channel",
                attachments=msg_attachments,
                metadata={
                    "platform": "slack",
                    "username": username,
                    "thread_ts": thread_ts,
                    "message_ts": message_ts,
                },
            )
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _register_slash_command(self, app: Any, name: str, description: str) -> None:
        """Register a slash command handler."""

        @app.command(f"/{name}")
        async def handler(ack: Any, command: dict) -> None:
            await ack()
            await self._handle_slash_command(command, name)

    async def _handle_slash_command(self, command: dict, cmd_name: str) -> None:
        """Bridge a Slack slash command to the CommandRouter."""
        if self._command_router is None:
            return

        user_id = command.get("user_id", "")
        channel_id = command.get("channel_id", "")
        text = command.get("text", "")
        args = text.split() if text else []

        # Get username
        username = command.get("user_name", "")

        ctx = CommandContext(
            channel=self.name,
            user_id=user_id,
            context_id=channel_id,
            chat_id=channel_id,
            args=args,
            display_name=username or user_id,
        )
        response = await self._command_router.dispatch(cmd_name, ctx)

        # Send response
        try:
            if self._app:
                await self._app.client.chat_postMessage(
                    channel=channel_id,
                    text=response,
                )
        except Exception as exc:
            logger.error(f"Failed to send slash command response: {exc}")

    def _is_allowed(self, user_id: str, username: str | None) -> bool:
        """Return True if the user passes the allow_from whitelist check."""
        return is_allowed(self._config.allow_from, user_id, username)
