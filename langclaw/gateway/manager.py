"""
GatewayManager — orchestrates channels, the message bus, and the agent loop.

Architecture:
  - All enabled channels run as sibling tasks inside asyncio.TaskGroup
  - A single _bus_worker task reads from the bus and dispatches agent calls
  - Each message is handled concurrently (one asyncio.Task per message)
  - Streaming agent chunks are forwarded to the originating channel in real time
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from langchain_core.messages import HumanMessage
from langgraph.graph.state import CompiledStateGraph
from loguru import logger

from langclaw.bus.base import BaseMessageBus, InboundMessage, OutboundMessage
from langclaw.checkpointer.base import BaseCheckpointerBackend
from langclaw.config.schema import LangclawConfig
from langclaw.context import LangclawContext
from langclaw.cron.scheduler import CronManager
from langclaw.gateway.base import BaseChannel
from langclaw.gateway.commands import CommandContext, CommandRouter
from langclaw.session.manager import SessionManager


class GatewayManager:
    """
    Central orchestrator for the multi-channel gateway.

    Responsibilities:
    - Start/stop all registered channels (via asyncio.TaskGroup)
    - Start/stop the CronManager alongside channels when provided
    - Run the bus worker that feeds messages to the agent
    - Handle per-message agent streaming back to the originating channel

    Args:
        config:               Loaded LangclawConfig.
        bus:                  Initialised BaseMessageBus.
        checkpointer_backend: Initialised BaseCheckpointerBackend (in context).
        agent:                Compiled LangGraph agent (from create_claw_agent).
        channels:             List of BaseChannel implementations to manage.
        cron_manager:         Optional ``CronManager`` to start/stop with the
                              gateway. When provided, scheduled jobs publish
                              ``InboundMessage``s to the bus and flow through
                              the same agent pipeline as channel messages.
        extra_commands:       Optional list of ``(name, handler, description)``
                              tuples registered via ``@app.command()``.
    """

    def __init__(
        self,
        config: LangclawConfig,
        bus: BaseMessageBus,
        checkpointer_backend: BaseCheckpointerBackend,
        agent: CompiledStateGraph,
        channels: list[BaseChannel],
        cron_manager: CronManager | None = None,
        extra_commands: (
            list[tuple[str, Callable[[CommandContext], Awaitable[str]], str]] | None
        ) = None,
        context_schema: type[LangclawContext] | None = None,
        context_defaults: dict[str, Any] | None = None,
        context_factory: (
            Callable[[InboundMessage, dict[str, Any]], Awaitable[LangclawContext]] | None
        ) = None,
    ) -> None:
        self._config = config
        self._bus = bus
        self._checkpointer_backend = checkpointer_backend
        self._agent = agent
        self._channels = [ch for ch in channels if ch.is_enabled()]
        self._cron_manager = cron_manager
        self._context_schema = context_schema or LangclawContext
        self._context_defaults = context_defaults or {}
        self._context_factory = context_factory
        self._sessions = SessionManager()
        self._command_router = CommandRouter(
            self._sessions,
            self._cron_manager,
        )
        if extra_commands:
            for cmd_name, handler, description in extra_commands:
                self._command_router.register(cmd_name, handler, description)
        self._channel_map: dict[str, BaseChannel] = {ch.name: ch for ch in self._channels}

    async def run(self) -> None:
        """
        Start all channels, the bus worker, and (optionally) the cron scheduler.

        Uses Python 3.11+ ``asyncio.TaskGroup`` for structured concurrency:
        if any channel task raises an unhandled exception the group cancels
        all sibling tasks, preventing zombie processes.

        The ``CronManager`` is started before the TaskGroup and stopped in a
        ``finally`` block so scheduled jobs are always cleaned up on exit.
        APScheduler manages its own internal async loop once started, so it
        does not need its own sibling task.
        """
        if self._cron_manager is not None:
            await self._cron_manager.start()
            logger.info("CronManager started.")

        for channel in self._channels:
            channel.set_command_router(self._command_router)

        try:
            async with asyncio.TaskGroup() as tg:
                for channel in self._channels:
                    tg.create_task(
                        self._run_channel(channel),
                        name=f"channel:{channel.name}",
                    )
                tg.create_task(self._bus_worker(), name="bus_worker")
        except* Exception as eg:
            for exc in eg.exceptions:
                logger.error(f"Gateway task failed: {exc}")
            raise
        finally:
            if self._cron_manager is not None:
                await self._cron_manager.stop()
                logger.info("CronManager stopped.")

    async def _run_channel(self, channel: BaseChannel) -> None:
        """Start a single channel, stopping it cleanly on cancellation."""
        logger.info(f"Starting channel: {channel.name}")
        try:
            await channel.start(self._bus)
        except asyncio.CancelledError:
            pass
        finally:
            logger.info(f"Stopping channel: {channel.name}")
            await channel.stop()

    async def _bus_worker(self) -> None:
        """
        Consume InboundMessages from the bus.
        Each message spawns an independent asyncio task so channels remain responsive.
        """
        logger.info("Bus worker started.")
        async for msg in self._bus.subscribe():
            asyncio.create_task(
                self._handle(msg),
                name=f"handle:{msg.channel}:{msg.user_id}",
            )

    async def _stream_updates_to_outbound_message(
        self,
        chunk: dict[str, Any],
        msg: InboundMessage,
        channel: BaseChannel,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """
        Translate one ``stream_mode="updates"`` chunk into ``OutboundMessage``
        calls on *channel*.

        With ``stream_mode="updates"`` each chunk is a dict of
        ``{node_name: node_state_update}``.  Only node updates that carry a
        ``"messages"`` list are relevant; all others (middleware side-effects,
        etc.) are skipped.

        Each LangGraph message maps to exactly one ``OutboundMessage``.
        Correlation between a tool call and its result is left to the channel
        via the shared ``"tool_call_id"`` metadata key.

        - ``AIMessage`` with ``tool_calls`` → ``type="tool_progress"`` per call
        - ``ToolMessage``                   → ``type="tool_result"``
        - ``AIMessage`` with text content   → ``type="ai"``
        """
        from langchain_core.messages import AIMessage, ToolMessage

        if not hasattr(self, "_tool_call_names"):
            self._tool_call_names: dict[str, str] = {}
        _tool_call_names = self._tool_call_names

        for node_name, node_update in chunk.items():
            if not isinstance(node_update, dict):
                continue
            # Only handle model and tools nodes
            # Skip middleware nodes
            if node_name not in ["model", "tools"]:
                continue
            messages = node_update.get("messages")
            if not messages:
                continue

            for m in messages:
                # ── Tool-progress: LLM decided to call a tool ─────────────
                if isinstance(m, AIMessage) and m.tool_calls:
                    for tc in m.tool_calls:
                        tool_name = tc.get("name", "")
                        tool_call_id = tc.get("id", "")
                        _tool_call_names[tool_call_id] = tool_name
                        await channel.send(
                            OutboundMessage(
                                channel=msg.channel,
                                user_id=msg.user_id,
                                context_id=msg.context_id,
                                chat_id=msg.chat_id,
                                content=tool_name,
                                type="tool_progress",
                                metadata={
                                    "tool_call_id": tool_call_id,
                                    "tool": tool_name,
                                    "args": tc.get("args", {}),
                                },
                            )
                        )

                # ── Tool result: raw output from the tool ──────────────────
                elif isinstance(m, ToolMessage):
                    content = m.content
                    if not isinstance(content, str):
                        content = str(content)
                    tc_id = m.tool_call_id or ""
                    await channel.send(
                        OutboundMessage(
                            channel=msg.channel,
                            user_id=msg.user_id,
                            context_id=msg.context_id,
                            chat_id=msg.chat_id,
                            content=content,
                            type="tool_result",
                            metadata={
                                "tool_call_id": tc_id,
                                "tool": _tool_call_names.get(tc_id, m.name or ""),
                            },
                        )
                    )

                # ── AI text response ───────────────────────────────────────
                elif isinstance(m, AIMessage) and m.content:
                    raw = m.content
                    if not isinstance(raw, str):
                        raw = " ".join(
                            b.get("text", "") if isinstance(b, dict) else str(b) for b in raw
                        )
                    if raw:
                        await channel.send(
                            OutboundMessage(
                                channel=msg.channel,
                                user_id=msg.user_id,
                                context_id=msg.context_id,
                                chat_id=msg.chat_id,
                                content=raw,
                                type="ai",
                            )
                        )

    def _resolve_user_role(self, msg: InboundMessage) -> str | None:
        """Look up the user's RBAC role from the channel config.

        Returns the role string, or ``None`` when permissions are
        disabled so the caller can skip passing context entirely.

        If the message carries a pre-resolved ``user_role`` in its
        metadata (e.g. from a cron job that persisted the role at
        schedule time), that value is used directly.  Otherwise falls
        back to checking the channel's ``user_roles`` mapping by
        user ID and username.
        """
        perms = self._config.permissions
        if not perms.enabled:
            return None

        stored_role = (msg.metadata or {}).get("user_role")
        if stored_role:
            logger.debug(
                "Using pre-resolved role '{}' from message metadata for user_id {}",
                stored_role,
                msg.user_id,
            )
            return stored_role

        ch_cfg = getattr(
            self._config.channels,
            msg.channel,
            None,
        )
        if ch_cfg is None:
            return perms.default_role
        user_roles: dict[str, str] = getattr(
            ch_cfg,
            "user_roles",
            {},
        )
        logger.debug(f"Checking permissions for user_id {msg.user_id}")
        role = user_roles.get(msg.user_id)
        if role is None:
            username = (msg.metadata or {}).get("username", "")
            logger.debug(f"No role found for user_id {msg.user_id}, checking username {username}")
            if username:
                role = user_roles.get(username)
        return role if role is not None else perms.default_role

    async def _handle(self, msg: InboundMessage) -> None:
        """
        Full message handling pipeline:
          1. Resolve / create LangGraph thread
          2. Resolve user RBAC role (if permissions enabled)
          3. Build RunnableConfig with channel context
          4. Stream agent updates back to the originating channel

        Routing is controlled by the ``to`` field on InboundMessage:
          - ``to="channel"``: bypass agent, send straight to the channel
          - ``to="agent"`` (default): feed to the main agent pipeline

        The ``origin`` field indicates who produced the message and is
        passed through to the channel in outbound metadata.

        For backward compatibility, ``metadata["_direct_delivery"]`` is
        still honoured if ``to="agent"`` but the flag is set.
        """
        channel = self._channel_map.get(msg.channel)
        if channel is None:
            logger.warning(
                f"No channel handler for '{msg.channel}' — dropping message.",
            )
            return

        meta = msg.metadata or {}

        # Route directly to channel if msg.to == "channel" or legacy _direct_delivery
        direct_to_channel = msg.to == "channel" or meta.get("_direct_delivery")
        if direct_to_channel:
            out_meta = {
                "origin": msg.origin,
                "subagent_name": meta.get("subagent_name", ""),
                **{k: v for k, v in meta.items()},
            }
            await channel.send(
                OutboundMessage(
                    channel=msg.channel,
                    user_id=msg.user_id,
                    context_id=msg.context_id,
                    chat_id=msg.chat_id,
                    content=msg.content,
                    type="ai",
                    metadata=out_meta,
                )
            )
            return

        # Message for main agent
        channel_context = {
            "channel": msg.channel,
            "user_id": msg.user_id,
            "context_id": msg.context_id,
            "chat_id": msg.chat_id,
            "metadata": msg.metadata,
        }
        runnable_config = await self._sessions.get_config(
            channel=msg.channel,
            user_id=msg.user_id,
            context_id=msg.context_id,
            channel_context=channel_context,
        )

        user_role = self._resolve_user_role(msg) or "viewer"
        base_kwargs = {
            "user_role": user_role,
            "channel": msg.channel,
            "user_id": msg.user_id,
            "context_id": msg.context_id,
            "chat_id": msg.chat_id,
            "metadata": msg.metadata or {},
        }

        if self._context_factory:
            context = await self._context_factory(msg, base_kwargs)
        else:
            context = self._context_schema(**base_kwargs, **self._context_defaults)

        input_state = {
            "messages": [HumanMessage(content=msg.content)],
        }

        try:
            stream_kwargs: dict[str, Any] = {
                "config": runnable_config,
                "stream_mode": "updates",
                "print_mode": "updates",
                "context": context,
            }

            async for chunk in self._agent.astream(
                input_state,
                **stream_kwargs,
            ):
                await self._stream_updates_to_outbound_message(chunk, msg, channel, metadata=meta)

        except Exception:
            logger.exception(
                f"Error handling message from {msg.channel}/{msg.user_id}",
            )
            try:
                await channel.send(
                    OutboundMessage(
                        channel=msg.channel,
                        user_id=msg.user_id,
                        context_id=msg.context_id,
                        chat_id=msg.chat_id,
                        content=("Sorry, something went wrong. Please try again."),
                        type="ai",
                    )
                )
            except Exception:
                logger.exception(
                    "Failed to send error response.",
                )
