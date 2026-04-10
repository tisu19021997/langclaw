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
import hashlib
import traceback
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, Final

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
from langclaw.gateway.utils import attachments_to_content_blocks
from langclaw.session.manager import SessionManager
from langclaw.utils import preview_message


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
        named_agent_specs: dict[str, dict[str, Any]] | None = None,
        default_agent_spec: dict[str, Any] | None = None,
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
            gateway_manager=self,
            workspace_dir=config.agents.workspace_dir,
        )
        if extra_commands:
            for cmd_name, handler, description in extra_commands:
                self._command_router.register(cmd_name, handler, description)
        self._channel_map: dict[str, BaseChannel] = {ch.name: ch for ch in self._channels}

        # Named agent registry — "default" always points to the main agent.
        self._agent_map: dict[str, CompiledStateGraph] = {"default": agent}
        self._agent_descriptions: dict[str, str] = {"default": "main agent"}

        # Spec used to rebuild the default agent when AGENTS.md changes.
        # Mirrors the arguments used by Langclaw.create_agent().
        self._default_agent_spec: dict[str, Any] = default_agent_spec or {}

        # Track last-seen AGENTS.md content hashes per agent so we can hot-reload
        # prompts when the underlying file changes.
        self._agents_md_hashes: dict[str, str] = {}

        # Simple per-agent locks to avoid concurrent rebuilds.
        self._agent_locks: dict[str, asyncio.Lock] = {}
        # Keep a reference to the raw named-agent specs so we can rebuild them
        # when their AGENTS.md changes.
        self._named_agent_specs: dict[str, dict[str, Any]] | None = named_agent_specs

        if self._named_agent_specs:
            for spec_name, spec in self._named_agent_specs.items():
                self._agent_descriptions[spec_name] = spec.get("description", "")
                self._agent_map[spec_name] = self._build_named_agent(spec, spec_name)

        # Register /agent only when named agents exist (no-op otherwise).
        if self._named_agent_specs:
            self._setup_agent_command()

        # Phase 2 hook point — auto-routing resolver (not yet wired):
        # self._agent_resolver: Callable[[InboundMessage], Awaitable[str | None]] | None = None

    # ------------------------------------------------------------------
    # AGENTS.md hot-reload helpers
    # ------------------------------------------------------------------

    def _get_workspace_dir_for_agent(self, agent_name: str) -> Path:
        """Return the workspace directory for a given agent name.

        The default agent uses ``config.agents.workspace_dir``.
        Named agents use ``config.agents.workspace_dir / agent_name``.
        """
        base: Final[Path] = self._config.agents.workspace_dir
        return base if agent_name == "default" else base / agent_name

    def _get_agents_md_path_for_agent(self, agent_name: str) -> Path:
        """Return the AGENTS.md path for a given agent workspace.

        Mirrors the logic in ``create_claw_agent``: each agent first looks for
        ``workspace_dir / "AGENTS.md"`` and falls back to the global
        ``config.agents.agents_md_file`` when missing.
        """
        workspace_dir = self._get_workspace_dir_for_agent(agent_name)
        candidate = workspace_dir / "AGENTS.md"
        return candidate if candidate.exists() else self._config.agents.agents_md_file

    def _compute_agents_md_hash(self, path: Path) -> str:
        """Return a stable hash of the AGENTS.md contents.

        Missing files are treated as empty strings.
        """
        try:
            text = path.read_text("utf-8")
        except OSError:
            text = ""
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    def _get_agent_lock(self, agent_name: str) -> asyncio.Lock:
        lock = self._agent_locks.get(agent_name)
        if lock is None:
            lock = asyncio.Lock()
            self._agent_locks[agent_name] = lock
        return lock

    def get_agents_md_path(self, agent_name: str) -> Path:
        """Public wrapper used by debug commands."""
        return self._get_agents_md_path_for_agent(agent_name)

    def invalidate_agent_hash(self, agent_name: str) -> None:
        """Clear the stored AGENTS.md hash so the next message triggers a rebuild."""
        self._agents_md_hashes.pop(agent_name, None)

    def _build_named_agent(self, spec: dict[str, Any], agent_name: str) -> CompiledStateGraph:
        """Build a compiled agent from a named-agent spec.

        Shares the same checkpointer backend as the main agent — threads are
        isolated by ``thread_id``, which is determined by ``context_id``.
        Each named agent gets its own workspace at
        ``config.agents.workspace_dir / agent_name``.

        Args:
            spec:       Named agent spec with keys ``system_prompt``, ``tools``,
                        ``model``.
            agent_name: Registered name of the agent, used to derive its
                        isolated workspace directory.

        Returns:
            A compiled LangGraph runnable.
        """
        from langclaw.agents.builder import create_claw_agent

        return create_claw_agent(
            self._config,
            checkpointer=self._checkpointer_backend.get(),
            cron_manager=self._cron_manager,
            extra_tools=spec.get("tools"),
            system_prompt=spec.get("system_prompt"),
            model=spec.get("model"),
            context_schema=self._context_schema,
            agent_name=agent_name,
        )

    async def _ensure_agent_fresh(self, agent_name: str) -> CompiledStateGraph:
        """Return a compiled agent, rebuilding if AGENTS.md has changed.

        This method performs a cheap content-hash check of the agent's
        ``AGENTS.md`` before each use. When the hash differs from the
        last-seen value, the agent is rebuilt with the same configuration
        (tools, model, system_prompt) and the internal registry is updated.
        """
        # Fast path: if we have never computed a hash for this agent, compute it
        # and store it but do not rebuild — the current instance was just built.
        current = self._agent_map.get(agent_name, self._agent_map["default"])
        path = self._get_agents_md_path_for_agent(agent_name)
        new_hash = self._compute_agents_md_hash(path)
        old_hash = self._agents_md_hashes.get(agent_name)
        if self._config.debug:
            logger.info(
                "[debug] AGENTS.md watch — agent='{}' path='{}' hash={}",
                agent_name,
                path,
                new_hash[:12],
            )
        if old_hash is None:
            self._agents_md_hashes[agent_name] = new_hash
            return current
        if new_hash == old_hash:
            return current

        # Slow path: AGENTS.md changed — rebuild under a per-agent lock so only
        # one task performs the work.
        logger.info("AGENTS.md changed for agent '{}' ({}), rebuilding…", agent_name, path)
        lock = self._get_agent_lock(agent_name)
        async with lock:
            # Double-check inside the lock in case another task already rebuilt.
            latest_hash = self._agents_md_hashes.get(agent_name)
            if latest_hash == new_hash:
                return self._agent_map.get(agent_name, current)

            try:
                from langclaw.agents.builder import create_claw_agent
            except ImportError:
                # If deepagents or builder cannot be imported, keep using the
                # existing agent and log the error.
                logger.error(
                    "Failed to import create_claw_agent while reloading AGENTS.md; "
                    "continuing with existing agent for '{}'.",
                    agent_name,
                )
                self._agents_md_hashes[agent_name] = new_hash
                return current

            try:
                if agent_name == "default":
                    # Rebuild the main agent using the same knobs as Langclaw.create_agent().
                    rebuilt = create_claw_agent(
                        self._config,
                        checkpointer=self._checkpointer_backend.get(),
                        cron_manager=self._cron_manager,
                        extra_tools=self._default_agent_spec.get("extra_tools"),
                        extra_middleware=self._default_agent_spec.get("extra_middleware"),
                        subagents=self._default_agent_spec.get("subagents"),
                        system_prompt=self._default_agent_spec.get("system_prompt"),
                        bus=self._default_agent_spec.get("bus"),
                        model=self._default_agent_spec.get("model"),
                        context_schema=self._context_schema,
                    )
                else:
                    # Named agents reuse their original spec.
                    spec = (getattr(self, "_named_agent_specs", None) or {}).get(agent_name, {})
                    rebuilt = self._build_named_agent(spec, agent_name)

                self._agent_map[agent_name] = rebuilt
                self._agents_md_hashes[agent_name] = new_hash
                logger.info("Reloaded AGENTS.md and rebuilt agent '{}'.", agent_name)
                return rebuilt
            except Exception as exc:  # noqa: BLE001
                logger.error(
                    "Error rebuilding agent '{}' after AGENTS.md change: {}. "
                    "Continuing with existing compiled agent.",
                    agent_name,
                    exc,
                )
                # Even if rebuild fails, update the hash so we don't thrash.
                self._agents_md_hashes[agent_name] = new_hash
                return current

    def _setup_agent_command(self) -> None:
        """Register the built-in ``/agent`` command as a closure.

        The closure captures ``_agent_map``, ``_agent_descriptions``,
        ``_sessions``, and ``_bus`` by reference so validation is always
        against the fully-populated map.

        Command syntax:
          - ``/agent`` — list available agents with active marker
          - ``/agent <name>`` — switch session to that agent persistently
          - ``/agent <name> <message>`` — send one message to that agent
            without changing the active session
        """
        agent_map = self._agent_map
        agent_descriptions = self._agent_descriptions
        sessions = self._sessions
        bus = self._bus

        async def _cmd_agent(ctx: CommandContext) -> str:
            if not ctx.args:
                current = await sessions.get_active_agent(ctx.channel, ctx.user_id)
                lines = ["Available agents:"]
                for name in agent_map:
                    desc = agent_descriptions.get(name, "")
                    marker = " (active)" if name == current else ""
                    suffix = f" \u2014 {desc}" if desc else ""
                    lines.append(f"  {name}{suffix}{marker}")
                return "\n".join(lines)

            target = ctx.args[0].lower()
            if target not in agent_map:
                available = ", ".join(n for n in agent_map if n != "default")
                return (
                    f"Unknown agent '{target}'. "
                    f"Available: {available or '(none registered)'}. "
                    f"Use /agent default to return to the main agent."
                )

            if len(ctx.args) == 1:
                # Persistent switch
                await sessions.set_active_agent(ctx.channel, ctx.user_id, target)
                if target == "default":
                    return "Switched back to the main agent."
                return f"Switched to agent '{target}'."

            # One-off message: publish to bus with agent_name in metadata
            message_content = " ".join(ctx.args[1:])
            await bus.publish(
                InboundMessage(
                    channel=ctx.channel,
                    user_id=ctx.user_id,
                    context_id=ctx.context_id,
                    chat_id=ctx.chat_id,
                    content=message_content,
                    metadata={"agent_name": target},
                )
            )
            return ""  # Empty response — agent will reply directly

        self._command_router.register("agent", _cmd_agent, "send message to a named agent")

    async def _resolve_agent_name(self, msg: InboundMessage) -> str:
        """Determine which named agent should handle this message.

        Resolution order:
          1. ``agent_name`` in message metadata — stamped at cron schedule time,
             deterministic and restart-safe.
          2. Phase 2 ``agent_resolver`` hook — not yet implemented.
          3. Active agent from :meth:`SessionManager.get_active_agent` (set by ``/agent``).
          4. Falls back to ``"default"``.

        Args:
            msg: The inbound message being handled.

        Returns:
            Agent name string — always a key present in ``self._agent_map``.
        """
        # 1. Explicit agent_name in metadata — stamped at cron schedule time.
        agent_name_meta = (msg.metadata or {}).get("agent_name")
        if agent_name_meta and agent_name_meta in self._agent_map:
            return agent_name_meta

        # Phase 2 hook (uncomment and wire when implementing auto-routing):
        # if self._agent_resolver is not None:
        #     resolved = await self._agent_resolver(msg)
        #     if resolved is not None and resolved in self._agent_map:
        #         return resolved

        # 2. Stored user agent name from /agent command.
        agent_name = await self._sessions.get_active_agent(msg.channel, msg.user_id)
        return agent_name if agent_name in self._agent_map else "default"

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

    async def _handle_message_chunk(
        self,
        chunk: tuple[Any, Any],
        msg: InboundMessage,
        channel: BaseChannel,
        streaming_contexts: set[str],
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """
        Translate one ``stream_mode="messages"`` chunk into a streaming
        ``OutboundMessage`` on *channel*.

        LangGraph yields ``(message_chunk, chunk_metadata)`` tuples and
        emits chunks for *every* LLM call in the compiled graph, including
        nested calls from middleware nodes (e.g. ``SummarizationMiddleware``
        invoking its summary model). Only chunks produced by the main
        ``"model"`` node are real agent output; everything else (summary
        generation, planner sub-calls, etc.) must be dropped — symmetric
        with ``_stream_updates_to_outbound_message`` which filters the
        updates path by the same node allowlist.

        Only ``AIMessageChunk`` objects with text content are forwarded;
        tool-call-only chunks are skipped (handled by ``stream_mode="updates"``).
        """
        from langchain_core.messages import AIMessageChunk

        message_chunk, chunk_metadata = chunk

        # Drop chunks from middleware nodes (summarization, planning, etc.).
        # The agent factory registers the user-facing model node as "model";
        # every middleware before_/after_ node is suffixed with
        # ".before_model" / ".after_model" and invokes its own LLM, which
        # would otherwise leak token-by-token into the user stream.
        node_name = (chunk_metadata or {}).get("langgraph_node")
        if node_name != "model":
            # Seam: a future "expose middleware activity" feature would
            # replace this early return with a dispatch to an internal
            # handler. Debug-level so prod logs stay quiet.
            logger.debug(f"Skipping non-model stream chunk from node={node_name!r}")
            return

        if not isinstance(message_chunk, AIMessageChunk):
            return
        content = message_chunk.content
        if not content:
            return
        if not isinstance(content, str):
            content = " ".join(
                b.get("text", "") if isinstance(b, dict) else str(b) for b in content
            )
        if not content:
            return

        streaming_contexts.add(msg.context_id)
        await channel.send(
            OutboundMessage(
                channel=msg.channel,
                user_id=msg.user_id,
                context_id=msg.context_id,
                chat_id=msg.chat_id,
                content=content,
                type="ai",
                streaming=True,
                is_final=False,
                metadata=metadata,
            )
        )

    async def _stream_updates_to_outbound_message(
        self,
        chunk: dict[str, Any],
        msg: InboundMessage,
        channel: BaseChannel,
        streaming_contexts: set[str],
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
        - ``AIMessage`` with text content   → ``type="ai"`` (skipped if already streamed)
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
                    logger.info(f"Tool call | {preview_message(m)}")
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
                                    **(metadata or {}),
                                    "tool_call_id": tool_call_id,
                                    "tool": tool_name,
                                    "args": tc.get("args", {}),
                                },
                            )
                        )

                # ── Tool result: raw output from the tool ──────────────────
                elif isinstance(m, ToolMessage):
                    logger.info(f"Tool result | {preview_message(m)}")
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
                                **(metadata or {}),
                                "tool_call_id": tc_id,
                                "tool": _tool_call_names.get(tc_id, m.name or ""),
                            },
                        )
                    )

                # ── AI text response ───────────────────────────────────────
                elif isinstance(m, AIMessage) and m.content:
                    # Skip: already delivered token-by-token via stream_mode="messages"
                    if msg.context_id in streaming_contexts:
                        logger.info(f"AI response (streamed) | {preview_message(m)}")
                        continue
                    logger.info(f"AI response | {preview_message(m)}")
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
                                metadata=metadata,
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

        # Message for main agent — resolve which agent handles this session.
        agent_name = await self._resolve_agent_name(msg)
        effective_context_id = msg.context_id if agent_name == "default" else f"agent:{agent_name}"

        _input_msg = HumanMessage(
            content=attachments_to_content_blocks(msg.content, msg.attachments)
        )
        logger.info(
            f"Message received | channel={msg.channel} user={msg.user_id} "
            f"origin={msg.origin} agent={agent_name} | {preview_message(_input_msg)}"
        )

        channel_context = {
            "channel": msg.channel,
            "user_id": msg.user_id,
            "context_id": effective_context_id,
            "chat_id": msg.chat_id,
            "metadata": msg.metadata,
        }
        runnable_config = await self._sessions.get_config(
            channel=msg.channel,
            user_id=msg.user_id,
            context_id=effective_context_id,
            channel_context=channel_context,
        )

        user_role = self._resolve_user_role(msg) or "viewer"
        base_kwargs = {
            "user_role": user_role,
            "channel": msg.channel,
            "user_id": msg.user_id,
            "context_id": effective_context_id,
            "chat_id": msg.chat_id,
            "metadata": msg.metadata or {},
        }

        if self._context_factory:
            context = await self._context_factory(msg, base_kwargs)
        else:
            context = self._context_schema(**base_kwargs, **self._context_defaults)

        content = attachments_to_content_blocks(msg.content, msg.attachments)
        input_state = {
            "messages": [HumanMessage(content=content)],
        }

        # Ensure the compiled agent is up to date with the latest AGENTS.md
        # contents for this agent's workspace before streaming.
        active_agent = await self._ensure_agent_fresh(agent_name)

        try:
            stream_kwargs: dict[str, Any] = {
                "config": runnable_config,
                "stream_mode": ["updates", "messages"],
                "context": context,
            }
            if self._config.log_level.upper() == "DEBUG":
                stream_kwargs["print_mode"] = "updates"

            # Track which context_ids have received streaming chunks this turn
            # so the "updates" handler can skip the duplicate full AIMessage.
            streaming_contexts: set[str] = set()

            async for mode, chunk in active_agent.astream(
                input_state,
                **stream_kwargs,
            ):
                if mode == "messages":
                    await self._handle_message_chunk(
                        chunk, msg, channel, streaming_contexts, metadata=meta
                    )
                elif mode == "updates":
                    logger.info(f"Chunk: {chunk}")
                    await self._stream_updates_to_outbound_message(
                        chunk, msg, channel, streaming_contexts, metadata=meta
                    )

            # Signal stream end so channels can flush their buffers.
            if msg.context_id in streaming_contexts:
                await channel.send(
                    OutboundMessage(
                        channel=msg.channel,
                        user_id=msg.user_id,
                        context_id=msg.context_id,
                        chat_id=msg.chat_id,
                        content="",
                        type="ai",
                        streaming=True,
                        is_final=True,
                        metadata=meta,
                    )
                )

        except Exception:
            logger.exception(
                f"Error handling message from {msg.channel}/{msg.user_id}",
            )
            if self._config.debug:
                _MAX_TRACE_LEN = 500
                trace = traceback.format_exc()
                if len(trace) > _MAX_TRACE_LEN:
                    trace = "..." + trace[-_MAX_TRACE_LEN:]
                error_content = f"Sorry, something went wrong.\n\n```\n{trace}\n```"
            else:
                error_content = "Sorry, something went wrong. Please try again."
            try:
                await asyncio.wait_for(
                    channel.send(
                        OutboundMessage(
                            channel=msg.channel,
                            user_id=msg.user_id,
                            context_id=msg.context_id,
                            chat_id=msg.chat_id,
                            content=error_content,
                            type="ai",
                        )
                    ),
                    timeout=15.0,
                )
            except Exception:
                logger.exception(
                    "Failed to send error response.",
                )
