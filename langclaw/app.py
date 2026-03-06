"""
Langclaw application class — the developer's primary interface.

Usage::

    from langclaw import Langclaw

    app = Langclaw()

    @app.tool()
    async def my_tool(query: str) -> str:
        \"\"\"My custom tool.\"\"\"
        return f"Result: {query}"

    app.role("power_user", tools=["my_tool", "web_search"])
    app.run()
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any, Literal

from loguru import logger

from langclaw.agents.builder import create_claw_agent
from langclaw.config.schema import (
    LangclawConfig,
    PermissionsConfig,
    RoleConfig,
    load_config,
)
from langclaw.context import LangclawContext
from langclaw.gateway.commands import CommandContext

if TYPE_CHECKING:
    from langchain_core.language_models import BaseChatModel
    from langchain_core.runnables import Runnable
    from langgraph.graph.state import CompiledStateGraph
    from langgraph.types import Checkpointer

    from langclaw.bus.base import BaseMessageBus, InboundMessage
    from langclaw.cron.scheduler import CronManager
    from langclaw.gateway.base import BaseChannel


class Langclaw:
    """Central application object for building multi-channel agent systems.

    Wraps :func:`~langclaw.agents.builder.create_claw_agent` and the gateway
    infrastructure, exposing a declarative API for tool/role/channel/middleware
    registration.

    Args:
        config:        Pre-built configuration. When ``None``, loaded from
                       env vars, ``.env``, and ``~/.langclaw/config.json``
                       via :func:`~langclaw.config.schema.load_config`.
        system_prompt: Additional instructions **appended** after the base
                       ``AGENTS.md`` prompt.  Use this to give your app a
                       distinct personality, domain focus, or behavioural
                       rules without replacing the built-in defaults
                       (memory protocol, tone, tool-use guidelines).

                       The base ``AGENTS.md`` is always loaded first from
                       the workspace (``~/.langclaw/workspace/AGENTS.md``).
                       Your ``system_prompt`` is concatenated after it,
                       separated by a blank line.  To fully replace the
                       base prompt, edit ``AGENTS.md`` directly instead.

                       Example::

                           app = Langclaw(
                               system_prompt=(
                                   "## Research Assistant\\n"
                                   "You are a financial research assistant.\\n"
                                   "Always check stock prices before answering."
                               ),
                           )
        context_schema: Custom context schema to use for the agent. If omitted,
                       uses the default LangclawContext.
    """

    def __init__(
        self,
        config: LangclawConfig | None = None,
        *,
        system_prompt: str | None = None,
        context_schema: type[LangclawContext] | None = None,
    ) -> None:
        self._config = config or load_config()
        self._system_prompt = system_prompt
        self._context_schema = context_schema
        self._extra_tools: list[Any] = []
        self._extra_channels: list[BaseChannel] = []
        self._extra_middleware: list[Any] = []
        self._extra_roles: dict[str, list[str]] = {}
        self._extra_commands: list[tuple[str, Callable[[CommandContext], Awaitable[str]], str]] = []
        self._subagents: list[dict[str, Any]] = []
        self._named_agents: dict[str, dict[str, Any]] = {}
        self._startup_hooks: list[Callable] = []
        self._shutdown_hooks: list[Callable] = []
        self._bus: BaseMessageBus | None = None
        self._context_defaults: dict[str, Any] = {}
        self._context_factory: (
            Callable[[InboundMessage, dict[str, Any]], Awaitable[LangclawContext]] | None
        ) = None

    @classmethod
    def from_env(cls) -> Langclaw:
        """Create a ``Langclaw`` app from env vars / ``.env`` / ``config.json``."""
        return cls(config=load_config())

    @property
    def config(self) -> LangclawConfig:
        """The resolved configuration object."""
        return self._config

    def get_bus(self) -> BaseMessageBus | None:
        """Return the running message bus, or ``None`` if not yet started."""
        return self._bus

    # ------------------------------------------------------------------
    # Tool registration
    # ------------------------------------------------------------------

    def tool(self, *, roles: list[str] | None = None) -> Callable:
        """Decorator to register a function as a LangChain tool.

        If the decorated function is not already a ``BaseTool``, it is
        wrapped with ``langchain_core.tools.tool``.

        Args:
            roles: Optional list of role names that should be granted
                   access to this tool.  When provided, the corresponding
                   roles are created/updated in the RBAC config.

        Returns:
            A decorator that registers the tool and returns it.
        """

        def decorator(fn: Callable) -> Any:
            from langchain_core.tools import BaseTool as _BaseTool
            from langchain_core.tools import tool as lc_tool

            t = fn if isinstance(fn, _BaseTool) else lc_tool(fn)
            self._extra_tools.append(t)

            if roles:
                for role_name in roles:
                    self._extra_roles.setdefault(role_name, []).append(t.name)

            return t

        return decorator

    def register_tool(self, tool: Any) -> None:
        """Register an existing ``BaseTool`` instance."""
        self._extra_tools.append(tool)

    def register_tools(self, tools: list[Any]) -> None:
        """Register multiple ``BaseTool`` instances at once."""
        self._extra_tools.extend(tools)

    # ------------------------------------------------------------------
    # Command registration
    # ------------------------------------------------------------------

    def command(
        self,
        name: str,
        *,
        description: str = "",
    ) -> Callable:
        """Decorator to register a custom bot command.

        Commands bypass the LLM and message bus — they are fast system
        operations handled directly by the :class:`CommandRouter`.

        The decorated function must accept a single
        :class:`~langclaw.gateway.commands.CommandContext` argument and
        return a ``str`` response.

        Args:
            name:        Command name without the leading ``/``
                         (e.g. ``"ping"``).
            description: Short help text shown by ``/help``.

        Returns:
            A decorator that registers the command and returns the
            original function.

        Example::

            @app.command("ping", description="check if bot is alive")
            async def ping(ctx: CommandContext) -> str:
                return "Pong!"
        """

        def decorator(
            fn: Callable[[CommandContext], Awaitable[str]],
        ) -> Callable[[CommandContext], Awaitable[str]]:
            self._extra_commands.append((name, fn, description))
            return fn

        return decorator

    # ------------------------------------------------------------------
    # RBAC
    # ------------------------------------------------------------------

    def role(self, name: str, *, tools: list[str]) -> None:
        """Define or update a permission role.

        If the role already exists (from config or a prior call), the
        tool lists are merged.  Registering any role automatically
        enables the permissions system.

        Args:
            name:  Role identifier (e.g. ``"admin"``, ``"viewer"``).
            tools: Tool names this role may invoke. Use ``["*"]`` for all.
        """
        existing = self._extra_roles.get(name, [])
        merged = list(dict.fromkeys(existing + tools))
        self._extra_roles[name] = merged

    # ------------------------------------------------------------------
    # Subagent registration
    # ------------------------------------------------------------------

    def subagent(
        self,
        name: str,
        *,
        description: str,
        graph: Runnable | dict[str, Any] | None = None,
        system_prompt: str | None = None,
        tools: list[str] | None = None,
        model: str | BaseChatModel | None = None,
        roles: list[str] | None = None,
        output: Literal["main_agent", "channel"] = "main_agent",
    ) -> None:
        """Register a subagent that the main agent can delegate tasks to.

        Subagents are invoked by the main agent via the ``task`` tool
        provided by deepagents.  Each subagent runs in an isolated
        context and returns a single result.

        There are three ways to define what the subagent does:

        1. **Declarative** — pass ``system_prompt`` (and optionally
           ``tools``, ``model``).  Langclaw builds the agent, resolves
           tool names, and injects its middleware.

        2. **Pre-built graph** — pass a ``Runnable`` or
           ``CompiledStateGraph`` via ``graph``.  Langclaw wraps it
           into a deepagents ``CompiledSubAgent`` and passes it through
           as-is.  The runnable's state schema **must** include a
           ``messages`` key.

        3. **deepagents dict** — pass a ``SubAgent`` or
           ``CompiledSubAgent`` TypedDict via ``graph``.  For
           ``SubAgent`` dicts, Langclaw prepends its middleware
           (channel context, RBAC).  ``CompiledSubAgent`` dicts (with
           a ``runnable`` key) are passed through unchanged.

        When ``graph`` is ``None``, ``system_prompt`` is required.

        Args:
            name:          Unique identifier used by the main agent when
                           calling the ``task`` tool.
            description:   What this subagent does.  Be specific —
                           the main agent uses this to decide when to
                           delegate.
            graph:         A pre-built ``Runnable``, ``CompiledStateGraph``,
                           or deepagents ``SubAgent``/``CompiledSubAgent``
                           dict.  Mutually exclusive with ``system_prompt``.
            system_prompt: Instructions for the subagent (declarative mode).
                           Required when ``graph`` is not provided.
            tools:         Tool **names** this subagent may use (declarative
                           mode only).  Resolved at build time against all
                           registered tools.  ``None`` inherits the main
                           agent's full tool set.
            model:         Override the main agent's model (declarative mode
                           only).  Accepts ``"provider:model"`` strings or
                           a ``BaseChatModel`` instance.
            roles:         Reserved for future RBAC scoping of which user
                           roles may trigger this subagent.
            output:        ``"main_agent"`` (default) returns the result
                           to the main agent.  ``"channel"`` publishes
                           the result directly to the originating channel
                           via the message bus (declarative mode only).

        Raises:
            ValueError: If neither ``graph`` nor ``system_prompt`` is
                        provided, or if both are provided, or if
                        ``output`` is invalid.

        Example::

            # Declarative — Langclaw builds the agent
            app.subagent(
                "researcher",
                description="Researches topics using web search",
                system_prompt="You are a thorough researcher...",
                tools=["web_search", "web_fetch"],
                model="openai:gpt-4.1",
            )

            # Pre-built LangGraph graph
            my_graph = create_agent("openai:gpt-4.1", tools=[...])
            app.subagent(
                "my-graph",
                description="Custom LangGraph pipeline",
                graph=my_graph,
            )

            # deepagents SubAgent dict
            app.subagent(
                "analyst",
                description="Financial analyst",
                graph={
                    "system_prompt": "Analyze data.",
                    "tools": [my_tool],
                    "model": "openai:gpt-4.1",
                },
            )
        """
        from langchain_core.runnables import Runnable as _Runnable

        if graph is not None and system_prompt is not None:
            raise ValueError(
                "'graph' and 'system_prompt' are mutually exclusive. "
                "Use 'graph' to bring a pre-built agent, or "
                "'system_prompt' for Langclaw to build one."
            )

        if graph is not None:
            if isinstance(graph, _Runnable):
                self._subagents.append(
                    {
                        "name": name,
                        "description": description,
                        "runnable": graph,
                    }
                )
            elif isinstance(graph, dict):
                self._subagents.append({**graph, "name": name, "description": description})
            else:
                raise TypeError(f"'graph' must be a Runnable or dict, got {type(graph).__name__}")
            return

        if system_prompt is None:
            raise ValueError("Either 'graph' or 'system_prompt' is required.")

        if output not in ("main_agent", "channel"):
            raise ValueError(
                f"Invalid output mode {output!r} for subagent {name!r}. "
                "Must be 'main_agent' or 'channel'."
            )

        self._subagents.append(
            {
                "name": name,
                "description": description,
                "system_prompt": system_prompt,
                "tools": tools,
                "model": model,
                "roles": roles,
                "output": output,
            }
        )

    # ------------------------------------------------------------------
    # Named agent registration
    # ------------------------------------------------------------------

    def agent(
        self,
        name: str,
        *,
        description: str,
        system_prompt: str | None = None,
        tools: list[Any] | None = None,
        model: str | BaseChatModel | None = None,
    ) -> None:
        """Register a named agent that users can switch to via ``/switch <name>``.

        Named agents are fully independent agent instances built with the same
        :func:`~langclaw.agents.builder.create_claw_agent` factory as the main
        agent.  Each named agent:

        - Gets its own isolated LangGraph conversation thread
          (``context_id = "agent:<name>"``), so history never bleeds across modes.
        - Shares the same checkpointer backend as the main agent.
        - Can use a different system prompt, tool set, or model.

        Users switch between agents via the built-in ``/switch <name>`` command,
        and can return to the main agent with ``/switch default``.

        Args:
            name:          Unique identifier used with ``/switch <name>``.
                           Must not be ``"default"`` (reserved sentinel).
            description:   Short description shown by ``/switch`` with no args.
            system_prompt: System prompt for this agent.  When ``None``, the
                           base ``AGENTS.md`` prompt is used unchanged.
            tools:         Explicit list of tool instances for this agent.
                           ``None`` inherits the config-driven built-in tools
                           without the extra tools registered on the app.
            model:         Override the default model.  Accepts
                           ``"provider:model"`` strings or a ``BaseChatModel``.

        Raises:
            ValueError: If ``name`` is ``"default"`` (reserved).

        Example::

            app.agent(
                "researcher",
                description="Deep research mode with web tools",
                system_prompt="You are a meticulous researcher. Always cite sources.",
                tools=[web_search, web_fetch],
                model="openai:gpt-4.1",
            )
        """
        if name == "default":
            raise ValueError(
                "'default' is a reserved agent name — it refers to the main agent. "
                "Choose a different name."
            )
        self._named_agents[name] = {
            "name": name,
            "description": description,
            "system_prompt": system_prompt,
            "tools": tools,
            "model": model,
        }

    # ------------------------------------------------------------------
    # Channels & middleware
    # ------------------------------------------------------------------

    def add_channel(self, channel: BaseChannel) -> None:
        """Register a custom channel alongside config-driven ones."""
        self._extra_channels.append(channel)

    def add_middleware(self, middleware: Any) -> None:
        """Append middleware to the end of the built-in stack."""
        self._extra_middleware.append(middleware)

    # ------------------------------------------------------------------
    # Lifecycle hooks
    # ------------------------------------------------------------------

    def on_startup(self, fn: Callable) -> Callable:
        """Decorator to register an async function called on gateway startup."""
        self._startup_hooks.append(fn)
        return fn

    def on_shutdown(self, fn: Callable) -> Callable:
        """Decorator to register an async function called on gateway shutdown."""
        self._shutdown_hooks.append(fn)
        return fn

    # ------------------------------------------------------------------
    # Context hooks
    # ------------------------------------------------------------------

    def set_context_defaults(self, **kwargs: Any) -> None:
        """Set extra kwargs merged into context construction.

        Use for app-level singletons like service clients or shared runners
        that every context instance needs.

        Args:
            **kwargs: Extra keyword arguments to pass to the context schema.
        """
        self._context_defaults.update(kwargs)

    def context_factory(
        self,
        fn: Callable[[InboundMessage, dict[str, Any]], Awaitable[LangclawContext]],
    ) -> Callable[[InboundMessage, dict[str, Any]], Awaitable[LangclawContext]]:
        """Decorator to register a per-message context factory.

        The factory receives the inbound message and base kwargs dict,
        and must return a context instance. Takes precedence over
        ``set_context_defaults()`` when set.

        Args:
            fn: Async callable ``(msg, base_kwargs) -> LangclawContext``.

        Returns:
            The original function.
        """
        self._context_factory = fn
        return fn

    # ------------------------------------------------------------------
    # Agent creation (lower-level API for REPL / tests)
    # ------------------------------------------------------------------

    def create_agent(
        self,
        *,
        checkpointer: Checkpointer | None = None,
        cron_manager: CronManager | None = None,
        model: BaseChatModel | None = None,
        bus: BaseMessageBus | None = None,
        context_schema: type[LangclawContext] | None = None,
    ) -> CompiledStateGraph:
        """Build the agent with all registered tools, middleware, and roles.

        This is the lower-level API — use it when you need the compiled
        LangGraph agent without the full gateway (e.g. for a REPL or
        tests).  :meth:`run` calls this internally.

        Args:
            checkpointer: LangGraph checkpoint saver for conversation state.
            cron_manager:  Running cron manager for scheduled jobs.
            model:         Override the configured LLM.
            bus:           Running message bus — required when any registered
                           subagent uses ``output="channel"``.

        Returns:
            A compiled LangGraph runnable.
        """
        effective_config = self._build_effective_config()

        return create_claw_agent(
            effective_config,
            checkpointer=checkpointer,
            cron_manager=cron_manager,
            extra_tools=self._extra_tools or None,
            extra_middleware=self._extra_middleware or None,
            subagents=self._subagents or None,
            system_prompt=self._system_prompt,
            bus=bus,
            model=model,
            context_schema=context_schema,
        )

    # ------------------------------------------------------------------
    # Gateway (high-level API)
    # ------------------------------------------------------------------

    def run(self) -> None:
        """Start the multi-channel gateway (blocking).

        Wires up the message bus, checkpointer, channels, cron manager,
        and agent, then runs ``GatewayManager`` until cancelled.
        """
        asyncio.run(self._run_async())

    async def _run_async(self) -> None:
        """Async gateway startup — the core wiring logic."""
        from langclaw.bus import make_message_bus
        from langclaw.checkpointer import make_checkpointer_backend
        from langclaw.gateway.manager import GatewayManager

        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%H:%M:%S",
        )

        cfg = self._config
        bus_cfg = cfg.bus
        cp_cfg = cfg.checkpointer

        bus = self._bus = make_message_bus(
            bus_cfg.backend,
            rabbitmq_url=bus_cfg.rabbitmq.amqp_url,
            rabbitmq_queue=bus_cfg.rabbitmq.queue_name,
            kafka_servers=bus_cfg.kafka.bootstrap_servers,
            kafka_topic=bus_cfg.kafka.topic,
            kafka_group_id=bus_cfg.kafka.group_id,
        )
        checkpointer_backend = make_checkpointer_backend(
            cp_cfg.backend,
            db_path=cp_cfg.sqlite.db_path,
            dsn=cp_cfg.postgres.dsn,
        )

        channels = self._build_all_channels()
        if not channels:
            logger.error(
                "No channels enabled. Enable at least one in your config "
                "or register one with app.add_channel()."
            )
            return

        for hook in self._startup_hooks:
            await hook()

        try:
            async with bus, checkpointer_backend:
                cron_manager = None
                if cfg.cron.enabled:
                    from langclaw.cron import make_cron_manager

                    cron_manager = make_cron_manager(bus=bus, config=cfg.cron)

                agent = self.create_agent(
                    checkpointer=checkpointer_backend.get(),
                    cron_manager=cron_manager,
                    bus=bus,
                    context_schema=self._context_schema,
                )

                manager = GatewayManager(
                    config=self._build_effective_config(),
                    bus=bus,
                    checkpointer_backend=checkpointer_backend,
                    agent=agent,
                    channels=channels,
                    cron_manager=cron_manager,
                    extra_commands=self._extra_commands or None,
                    context_schema=self._context_schema,
                    context_defaults=self._context_defaults,
                    context_factory=self._context_factory,
                    named_agent_specs=self._named_agents or None,
                )

                cron_status = "enabled" if cron_manager else "disabled"
                logger.info(
                    "Gateway starting — channels: {}, bus: {}, checkpointer: {}, cron: {}",
                    [ch.name for ch in channels],
                    bus_cfg.backend,
                    cp_cfg.backend,
                    cron_status,
                )
                await manager.run()
        finally:
            self._bus = None
            for hook in self._shutdown_hooks:
                await hook()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_effective_config(self) -> LangclawConfig:
        """Return a config copy with programmatic roles merged in."""
        if not self._extra_roles:
            return self._config

        cfg = self._config.model_copy(deep=True)
        cfg.permissions = self._merge_permissions(cfg.permissions)
        return cfg

    def _merge_permissions(self, base: PermissionsConfig) -> PermissionsConfig:
        """Merge ``app.role()`` definitions into the permissions config.

        Programmatic roles are merged on top of config-file roles.
        Registering any role auto-enables the permissions system.
        """
        perms = base.model_copy(deep=True)
        perms.enabled = True

        for name, tool_names in self._extra_roles.items():
            if name in perms.roles:
                existing = perms.roles[name].tools
                merged = list(dict.fromkeys(existing + tool_names))
                perms.roles[name] = RoleConfig(tools=merged)
            else:
                perms.roles[name] = RoleConfig(tools=tool_names)

        return perms

    def _build_all_channels(self) -> list[BaseChannel]:
        """Build channels from config + programmatically registered ones."""
        channels: list[BaseChannel] = []

        ch_cfg = self._config.channels

        if ch_cfg.telegram.enabled:
            try:
                from langclaw.gateway.telegram import TelegramChannel

                channels.append(TelegramChannel(ch_cfg.telegram))
            except ImportError:
                logger.warning(
                    "Telegram enabled but python-telegram-bot not installed. "
                    "Run: uv add 'langclaw[telegram]'"
                )

        if ch_cfg.discord.enabled:
            try:
                from langclaw.gateway.discord import DiscordChannel

                channels.append(DiscordChannel(ch_cfg.discord))
            except ImportError:
                logger.warning(
                    "Discord enabled but discord.py not installed. Run: uv add 'langclaw[discord]'"
                )

        if ch_cfg.websocket.enabled:
            try:
                from langclaw.gateway.websocket import WebSocketChannel

                channels.append(WebSocketChannel(ch_cfg.websocket))
            except ImportError:
                logger.warning(
                    "WebSocket enabled but websockets not installed. "
                    "Run: uv add 'langclaw[websocket]'"
                )

        channels.extend(self._extra_channels)
        return channels


__all__ = ["Langclaw", "CommandContext"]
