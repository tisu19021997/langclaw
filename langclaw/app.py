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
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from loguru import logger

from langclaw.agents.builder import create_claw_agent
from langclaw.config.schema import LangclawConfig, PermissionsConfig, RoleConfig, load_config

if TYPE_CHECKING:
    from langchain_core.language_models import BaseChatModel
    from langgraph.graph.state import CompiledStateGraph
    from langgraph.types import Checkpointer

    from langclaw.cron.scheduler import CronManager
    from langclaw.gateway.base import BaseChannel


class Langclaw:
    """Central application object for building multi-channel agent systems.

    Wraps :func:`~langclaw.agents.builder.create_claw_agent` and the gateway
    infrastructure, exposing a declarative API for tool/role/channel/middleware
    registration.

    Args:
        config: Pre-built configuration. When ``None``, loaded from env vars,
                ``.env``, and ``~/.langclaw/config.json`` via
                :func:`~langclaw.config.schema.load_config`.
    """

    def __init__(self, config: LangclawConfig | None = None) -> None:
        self._config = config or load_config()
        self._extra_tools: list[Any] = []
        self._extra_channels: list[BaseChannel] = []
        self._extra_middleware: list[Any] = []
        self._extra_roles: dict[str, list[str]] = {}
        self._startup_hooks: list[Callable] = []
        self._shutdown_hooks: list[Callable] = []

    @classmethod
    def from_env(cls) -> Langclaw:
        """Create a ``Langclaw`` app from env vars / ``.env`` / ``config.json``."""
        return cls(config=load_config())

    @property
    def config(self) -> LangclawConfig:
        """The resolved configuration object."""
        return self._config

    # ------------------------------------------------------------------
    # Tool registration
    # ------------------------------------------------------------------

    def tool(
        self, *, roles: list[str] | None = None
    ) -> Callable:
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
    # Agent creation (lower-level API for REPL / tests)
    # ------------------------------------------------------------------

    def create_agent(
        self,
        *,
        checkpointer: Checkpointer | None = None,
        cron_manager: CronManager | None = None,
        model: BaseChatModel | None = None,
    ) -> CompiledStateGraph:
        """Build the agent with all registered tools, middleware, and roles.

        This is the lower-level API — use it when you need the compiled
        LangGraph agent without the full gateway (e.g. for a REPL or
        tests).  :meth:`run` calls this internally.

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
            model=model,
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

        bus = make_message_bus(
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
                )

                manager = GatewayManager(
                    config=self._build_effective_config(),
                    bus=bus,
                    checkpointer_backend=checkpointer_backend,
                    agent=agent,
                    channels=channels,
                    cron_manager=cron_manager,
                )

                cron_status = "enabled" if cron_manager else "disabled"
                logger.info(
                    "Gateway starting — channels: {}, bus: {}, "
                    "checkpointer: {}, cron: {}",
                    [ch.name for ch in channels],
                    bus_cfg.backend,
                    cp_cfg.backend,
                    cron_status,
                )
                await manager.run()
        finally:
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
                    "Discord enabled but discord.py not installed. "
                    "Run: uv add 'langclaw[discord]'"
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


__all__ = ["Langclaw"]
