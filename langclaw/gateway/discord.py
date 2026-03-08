"""
DiscordChannel — Gateway channel for Discord.

Requires: langclaw[discord]  →  uv add "langclaw[discord]"

Features:
- Handles DMs and guild (server) text channel messages
- Splits messages that exceed Discord's 2 000-char limit
- Sends typing indicator while the agent is thinking
- Respects allow_from user whitelist (user IDs or usernames)
- /start, /help, /reset, /cron command support (prefix-based)
- Tool-progress / tool-result rendering with code-block formatting
- Automatic reconnect on gateway disconnects
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from langclaw.bus.base import BaseMessageBus, InboundMessage, OutboundMessage
from langclaw.config.schema import DiscordChannelConfig
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

MAX_MESSAGE_LEN = 2000
MAX_ATTACHMENT_BYTES = 20 * 1024 * 1024  # 20 MB


# ---------------------------------------------------------------------------
# Channel
# ---------------------------------------------------------------------------


class DiscordChannel(BaseChannel):
    """
    Discord bot channel backed by discord.py v2+.

    Uses the Gateway websocket for receiving messages and the REST API
    for sending.  Reconnection is handled automatically by discord.py.

    Args:
        config: Discord-specific section of LangclawConfig.channels.discord.
    """

    name = "discord"

    def __init__(self, config: DiscordChannelConfig) -> None:
        self._config = config
        self._client: Any = None
        self._bus: BaseMessageBus | None = None
        self._running = False
        self._typing_tasks: dict[str, asyncio.Task] = {}
        self._tool_call_buffer: dict[str, dict] = {}

    def is_enabled(self) -> bool:
        return self._config.enabled and bool(self._config.token)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self, bus: BaseMessageBus) -> None:
        try:
            import discord
        except ImportError as exc:
            raise ImportError(
                "DiscordChannel requires 'langclaw[discord]'. "
                "Install with: uv add 'langclaw[discord]'"
            ) from exc

        self._bus = bus
        self._running = True

        intents = discord.Intents.default()
        intents.message_content = True
        intents.guilds = True
        intents.dm_messages = True

        client = discord.Client(intents=intents)
        self._client = client

        tree = discord.app_commands.CommandTree(client)
        self._tree = tree

        self._register_slash_commands(tree, discord)

        @client.event
        async def on_ready() -> None:
            logger.info(
                "Discord bot %s connected (guilds: %d)",
                client.user,
                len(client.guilds),
            )
            try:
                synced = await tree.sync()
                logger.info("Synced %d Discord slash commands", len(synced))
            except Exception as exc:
                logger.warning("Failed to sync Discord slash commands: %s", exc)

        @client.event
        async def on_message(message: discord.Message) -> None:
            logger.debug(
                "Discord raw event: author=%s bot=%s channel=%s content=%r",
                message.author,
                message.author.bot,
                message.channel.id,
                message.content[:80] if message.content else "",
            )
            if message.author.bot:
                return

            is_dm = isinstance(message.channel, discord.DMChannel)
            mentioned = client.user in message.mentions if client.user else False
            if not is_dm and not mentioned:
                return

            if mentioned and client.user:
                message.content = message.content.replace(f"<@{client.user.id}>", "").strip()

            await self._on_message(message)

        @client.event
        async def on_message_edit(_before: discord.Message, after: discord.Message) -> None:
            if after.author.bot:
                return
            mentioned = client.user in after.mentions if client.user else False
            if not mentioned:
                return
            if client.user:
                after.content = after.content.replace(f"<@{client.user.id}>", "").strip()
            await self._on_message(after)

        logger.info("DiscordChannel starting…")
        await client.start(self._config.token)

    async def stop(self) -> None:
        self._running = False
        for chat_id in list(self._typing_tasks):
            self._stop_typing(chat_id)
        if self._client is not None:
            try:
                logger.info("Stopping DiscordChannel…")
                await self._client.close()
            except Exception:
                logger.exception("Error stopping DiscordChannel")
            self._client = None

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
        if self._client is None or is_cron_context_id(msg.context_id):
            return

        tc_id = msg.metadata.get("tool_call_id", "")
        call_info = self._tool_call_buffer.pop(tc_id, {})
        header = format_tool_progress(
            call_info.get("tool", ""),
            call_info.get("args") or {},
            markup="markdown",
        )

        # Truncate result to fit within Discord's limit, reserving room for
        # the header line and code-block fences.
        _CODE_BLOCK_OVERHEAD = len("```\n\n```") + 1
        max_content = MAX_MESSAGE_LEN - len(header) - _CODE_BLOCK_OVERHEAD - len(TRUNCATION_SUFFIX)
        result_text = msg.content or ""
        if len(result_text) > max_content:
            result_text = result_text[:max_content] + TRUNCATION_SUFFIX

        text = f"{header}\n```\n{result_text}\n```"
        await self._send_text(msg.chat_id, text)

    async def send_ai_message(self, msg: OutboundMessage) -> None:
        """Stop the typing indicator and deliver the final AI response."""
        if self._client is None:
            return
        self._stop_typing(msg.chat_id)
        if not msg.content:
            return
        reply_to_id = (msg.metadata or {}).get("reply_to")
        for chunk in split_message(msg.content, max_len=MAX_MESSAGE_LEN):
            await self._send_text(msg.chat_id, chunk, reply_to_id=reply_to_id)

    # ------------------------------------------------------------------
    # Sending helpers
    # ------------------------------------------------------------------

    async def _send_text(
        self,
        chat_id: str,
        text: str,
        reply_to_id: str | None = None,
    ) -> None:
        """Send a text message to a Discord channel, with retry on rate-limit."""
        import discord

        if not self._client:
            return

        channel = self._client.get_channel(int(chat_id))
        if channel is None:
            try:
                channel = await self._client.fetch_channel(int(chat_id))
            except discord.NotFound:
                logger.warning("Discord channel %s not found", chat_id)
                return
            except Exception as exc:
                logger.error("Failed to fetch Discord channel %s: %s", chat_id, exc)
                return

        for chunk in split_message(text, max_len=MAX_MESSAGE_LEN):
            reference = None
            if reply_to_id:
                try:
                    reference = discord.MessageReference(
                        message_id=int(reply_to_id),
                        channel_id=int(chat_id),
                    )
                except (ValueError, TypeError):
                    pass

            for attempt in range(3):
                try:
                    await channel.send(chunk, reference=reference)
                    break
                except discord.HTTPException as exc:
                    if exc.status == 429:
                        retry_after = getattr(exc, "retry_after", 1.0)
                        logger.warning(f"Discord rate limited, retrying in {retry_after}s")
                        await asyncio.sleep(float(retry_after))
                        continue
                    if attempt == 2:
                        logger.error(f"Error sending Discord message: {exc}")
                    else:
                        await asyncio.sleep(1)
                except Exception as exc:
                    if attempt == 2:
                        logger.error(f"Error sending Discord message: {exc}")
                    else:
                        await asyncio.sleep(1)

            # Only attach reply reference to the first chunk
            reply_to_id = None

    # ------------------------------------------------------------------
    # Typing indicator
    # ------------------------------------------------------------------

    def _start_typing(self, chat_id: str) -> None:
        """Start a looping typing indicator for *chat_id*."""
        self._stop_typing(chat_id)
        self._typing_tasks[chat_id] = asyncio.create_task(
            self._typing_loop(chat_id), name=f"discord-typing:{chat_id}"
        )

    def _stop_typing(self, chat_id: str) -> None:
        """Cancel the typing indicator for *chat_id*."""
        task = self._typing_tasks.pop(chat_id, None)
        if task and not task.done():
            task.cancel()

    async def _typing_loop(self, chat_id: str) -> None:
        """Send typing indicators every 8 s until cancelled."""
        import discord

        try:
            while self._client:
                channel = self._client.get_channel(int(chat_id))
                if channel is None:
                    return
                try:
                    await channel.trigger_typing()
                except discord.HTTPException:
                    pass
                await asyncio.sleep(8)
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.debug("Discord typing indicator stopped for %s: %s", chat_id, exc)

    # ------------------------------------------------------------------
    # Inbound message handling
    # ------------------------------------------------------------------

    async def _on_message(self, message: Any) -> None:
        """Route an incoming Discord message to the command router or bus."""
        import discord

        if not isinstance(message, discord.Message):
            return

        sender_id = str(message.author.id)
        username = message.author.name
        channel_id = str(message.channel.id)
        content = message.content or ""

        if not sender_id or not channel_id:
            logger.debug(
                f"Discord message dropped: author={message.author} "
                f"bot={message.author.bot} "
                f"channel={message.channel.id} "
                f"content={message.content[:80] if message.content else ''}",
            )
            return

        if not self._is_allowed(sender_id, username):
            logger.warning(
                f"Discord user {sender_id} ({username}) not in allow_from — dropping message",
                sender_id,
                username,
            )
            try:
                await message.reply(
                    "Sorry, you are not authorised to use this bot.",
                    mention_author=False,
                )
            except Exception as exc:
                logger.debug(f"Failed to send 'not authorised' reply: {exc}")
            return

        # -- Command handling (/start, /help, /reset, /cron) --
        stripped = content.strip()
        if stripped.startswith("/"):
            parts = stripped.split()
            cmd = parts[0].lstrip("/").lower() if parts else ""
            args = parts[1:] if len(parts) > 1 else []

            if cmd and self._command_router is not None:
                display_name = message.author.display_name or message.author.name or ""
                ctx = CommandContext(
                    channel=self.name,
                    user_id=sender_id,
                    context_id=channel_id,
                    chat_id=channel_id,
                    args=args,
                    display_name=display_name,
                )
                response = await self._command_router.dispatch(cmd, ctx)
                try:
                    await message.reply(response, mention_author=False)
                except Exception as exc:
                    logger.error(f"Failed to send command response: {exc}")
                return

        if self._bus is None:
            return

        # -- Attachment handling --
        from langclaw.bus.base import Attachment

        content_parts = [content] if content else []
        msg_attachments: list[Attachment] = []
        media_dir = Path.home() / ".langclaw" / "media"

        for attachment in message.attachments:
            if attachment.size and attachment.size > MAX_ATTACHMENT_BYTES:
                content_parts.append(f"[attachment: {attachment.filename} - too large]")
                continue
            try:
                media_dir.mkdir(parents=True, exist_ok=True)
                file_path = media_dir / f"{attachment.id}_{attachment.filename.replace('/', '_')}"
                await attachment.save(file_path)
                msg_attachments.append(
                    make_attachment(
                        file_path=file_path,
                        filename=attachment.filename,
                        size=attachment.size or 0,
                    )
                )
            except Exception as exc:
                logger.warning(f"Failed to download Discord attachment: {exc}")
                content_parts.append(f"[attachment: {attachment.filename} - download failed]")

        is_dm = isinstance(message.channel, discord.DMChannel)
        guild_id = str(message.guild.id) if message.guild else None

        reply_to = None
        if message.reference and message.reference.message_id:
            reply_to = str(message.reference.message_id)

        self._start_typing(channel_id)

        await self._bus.publish(
            InboundMessage(
                channel=self.name,
                user_id=sender_id,
                context_id=channel_id,
                chat_id=channel_id,
                content="\n".join(p for p in content_parts if p) or "[empty message]",
                origin="channel",
                attachments=msg_attachments,
                metadata={
                    "platform": "discord",
                    "username": username,
                    "message_id": str(message.id),
                    "guild_id": guild_id,
                    "reply_to": reply_to,
                    "is_dm": is_dm,
                },
            )
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _register_slash_commands(self, tree: Any, discord: Any) -> None:
        """Dynamically register Discord slash commands from the CommandRouter.

        The ``cron`` command keeps its typed parameters (action, job_id).
        All other commands are registered with no extra parameters.
        """
        if self._command_router is None:
            return

        def _make_handler(cmd_name: str):
            async def handler(interaction: discord.Interaction) -> None:
                await self._handle_slash(interaction, cmd_name)

            return handler

        for entry in self._command_router.list_commands():
            if entry.name == "cron":

                @tree.command(
                    name="cron",
                    description=entry.description or "List or remove cron jobs",
                )
                @discord.app_commands.describe(
                    action="list or remove", job_id="Job ID (for remove)"
                )
                async def slash_cron(
                    interaction: discord.Interaction,
                    action: str = "list",
                    job_id: str | None = None,
                ) -> None:
                    args = [action]
                    if job_id:
                        args.append(job_id)
                    await self._handle_slash(interaction, "cron", args)

            elif entry.name == "switch":

                @tree.command(
                    name="switch",
                    description=entry.description or "Switch to a named agent",
                )
                @discord.app_commands.describe(
                    agent_name="Agent name to switch to (omit to list agents)"
                )
                async def slash_switch(
                    interaction: discord.Interaction,
                    agent_name: str | None = None,
                ) -> None:
                    args = [agent_name] if agent_name else []
                    await self._handle_slash(interaction, "switch", args)

            else:
                tree.command(
                    name=entry.name,
                    description=entry.description or entry.name,
                )(_make_handler(entry.name))

    async def _handle_slash(
        self,
        interaction: Any,
        cmd: str,
        args: list[str] | None = None,
    ) -> None:
        """Bridge a Discord slash-command interaction to the CommandRouter."""
        if self._command_router is None:
            await interaction.response.send_message(
                f"Command /{cmd} is not available.",
                ephemeral=True,
            )
            return

        user = interaction.user
        ctx = CommandContext(
            channel=self.name,
            user_id=str(user.id),
            context_id=str(interaction.channel_id),
            chat_id=str(interaction.channel_id),
            args=args or [],
            display_name=user.display_name or user.name or "",
        )
        await interaction.response.defer()
        response = await self._command_router.dispatch(cmd, ctx)
        await interaction.followup.send(response)

    def _is_allowed(self, user_id: str, username: str | None) -> bool:
        """Return True if the user passes the allow_from whitelist check."""
        return is_allowed(self._config.allow_from, user_id, username)
