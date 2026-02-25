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
from typing import Any

from langchain_core.messages import HumanMessage
from langgraph.graph.state import CompiledStateGraph
from loguru import logger

from langclaw.bus.base import BaseMessageBus, InboundMessage, OutboundMessage
from langclaw.checkpointer.base import BaseCheckpointerBackend
from langclaw.config.schema import LangclawConfig
from langclaw.cron.scheduler import CronManager
from langclaw.gateway.base import BaseChannel
from langclaw.gateway.commands import CommandRouter
from langclaw.middleware.permissions import LangclawContext
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
    """

    def __init__(
        self,
        config: LangclawConfig,
        bus: BaseMessageBus,
        checkpointer_backend: BaseCheckpointerBackend,
        agent: CompiledStateGraph,
        channels: list[BaseChannel],
        cron_manager: CronManager | None = None,
    ) -> None:
        self._config = config
        self._bus = bus
        self._checkpointer_backend = checkpointer_backend
        self._agent = agent
        self._channels = [ch for ch in channels if ch.is_enabled()]
        self._cron_manager = cron_manager
        self._sessions = SessionManager()
        self._command_router = CommandRouter(
            self._sessions, self._cron_manager,
        )
        self._channel_map: dict[str, BaseChannel] = {
            ch.name: ch for ch in self._channels
        }

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
                        await channel.send(
                            OutboundMessage(
                                channel=msg.channel,
                                user_id=msg.user_id,
                                context_id=msg.context_id,
                                chat_id=msg.chat_id,
                                content="",
                                type="tool_progress",
                                metadata={
                                    "tool_call_id": tc.get("id", ""),
                                    "tool": tc.get("name", ""),
                                    "args": tc.get("args", {}),
                                },
                            )
                        )

                # ── Tool result: raw output from the tool ──────────────────
                elif isinstance(m, ToolMessage):
                    content = m.content
                    if not isinstance(content, str):
                        content = str(content)
                    await channel.send(
                        OutboundMessage(
                            channel=msg.channel,
                            user_id=msg.user_id,
                            context_id=msg.context_id,
                                chat_id=msg.chat_id,
                            content=content,
                            type="tool_result",
                            metadata={"tool_call_id": m.tool_call_id or ""},
                        )
                    )

                # ── AI text response ───────────────────────────────────────
                elif isinstance(m, AIMessage) and m.content:
                    raw = m.content
                    if not isinstance(raw, str):
                        raw = " ".join(
                            b.get("text", "") if isinstance(b, dict) else str(b)
                            for b in raw
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
        """
        perms = self._config.permissions
        if not perms.enabled:
            return None
        ch_cfg = getattr(
            self._config.channels, msg.channel, None,
        )
        if ch_cfg is None:
            return perms.default_role
        user_roles: dict[str, str] = getattr(
            ch_cfg, "user_roles", {},
        )
        return user_roles.get(msg.user_id, perms.default_role)

    async def _handle(self, msg: InboundMessage) -> None:
        """
        Full message handling pipeline:
          1. Resolve / create LangGraph thread
          2. Resolve user RBAC role (if permissions enabled)
          3. Build RunnableConfig with channel context
          4. Stream agent updates back to the originating channel
        """
        channel = self._channel_map.get(msg.channel)
        if channel is None:
            logger.warning(
                f"No channel handler for '{msg.channel}'"
                " — dropping message.",
            )
            return

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

        user_role = self._resolve_user_role(msg)
        context: LangclawContext | None = None
        if user_role is not None:
            context = LangclawContext(user_role=user_role)

        input_state = {
            "messages": [HumanMessage(content=msg.content)],
        }

        try:
            stream_kwargs: dict[str, Any] = {
                "config": runnable_config,
                "stream_mode": "updates",
                "print_mode": "updates",
            }
            if context is not None:
                stream_kwargs["context"] = context

            async for chunk in self._agent.astream(
                input_state,
                **stream_kwargs,
            ):
                await self._stream_updates_to_outbound_message(
                    chunk, msg, channel,
                )

        except Exception:
            logger.exception(
                f"Error handling message from"
                f" {msg.channel}/{msg.user_id}",
            )
            try:
                await channel.send(
                    OutboundMessage(
                        channel=msg.channel,
                        user_id=msg.user_id,
                        context_id=msg.context_id,
                        chat_id=msg.chat_id,
                        content=(
                            "Sorry, something went wrong."
                            " Please try again."
                        ),
                        type="ai",
                    )
                )
            except Exception:
                logger.exception(
                    "Failed to send error response.",
                )
