"""
Agent builder — always produces a deepagents deep agent.

Default skills and memory are bundled inside the package and copied to the
user's workspace on first ``langclaw init``. The app always uses the workspace
copies so users can modify them directly.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel
from langgraph.graph.state import CompiledStateGraph

from langclaw.agents.tools import build_cron_tools, build_gmail_tools, build_web_tools
from langclaw.config.schema import LangclawConfig
from langclaw.context import LangclawContext
from langclaw.middleware.channel_context import ChannelContextMiddleware
from langclaw.middleware.guardrails import ContentFilterMiddleware, PIIMiddleware
from langclaw.middleware.permissions import build_tool_permission_middleware
from langclaw.middleware.rate_limit import RateLimitMiddleware
from langclaw.utils import to_virtual_path  # for extra_skills conversion

if TYPE_CHECKING:
    from langchain.agents.middleware import AgentMiddleware
    from langchain_core.tools import BaseTool
    from langgraph.types import Checkpointer

    from langclaw.bus.base import BaseMessageBus
    from langclaw.cron.scheduler import CronManager

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_DEFAULTS_DIR = Path(__file__).parent / "defaults"
_DEFAULT_AGENTS_MD = _DEFAULTS_DIR / "AGENTS.md"
_DEFAULT_SKILLS_DIR = _DEFAULTS_DIR / "skills"

# ---------------------------------------------------------------------------
# Subagent helpers
# ---------------------------------------------------------------------------


def _resolve_tools_by_name(
    tool_names: list[str] | None,
    all_tools: list[Any],
) -> list[Any] | None:
    """Resolve tool name strings to tool objects.

    Returns ``None`` when *tool_names* is ``None`` (inherit from main agent).
    Raises ``ValueError`` for unrecognised names.
    """
    if tool_names is None:
        return None

    tool_map: dict[str, Any] = {}
    for t in all_tools:
        name = getattr(t, "name", None)
        if name:
            tool_map[name] = t

    resolved: list[Any] = []
    for name in tool_names:
        if name not in tool_map:
            available = ", ".join(sorted(tool_map)) or "(none)"
            raise ValueError(
                f"Subagent requested unknown tool {name!r}. Available tools: {available}"
            )
        resolved.append(tool_map[name])
    return resolved


def _build_deepagent_subagents(
    specs: list[dict[str, Any]],
    all_tools: list[Any],
    config: LangclawConfig,
) -> list[dict[str, Any]]:
    """Convert langclaw subagent specs to deepagents ``SubAgent`` dicts.

    Each subagent receives its own lightweight middleware stack
    (channel context injection and, when enabled, RBAC filtering).
    """
    result: list[dict[str, Any]] = []
    for spec in specs:
        if spec.get("output", "main_agent") != "main_agent":
            continue

        sa_tools = _resolve_tools_by_name(spec.get("tools"), all_tools)

        sa_middleware: list[Any] = [ChannelContextMiddleware()]
        if config.permissions.enabled:
            sa_middleware.append(
                build_tool_permission_middleware(config.permissions),
            )

        sa: dict[str, Any] = {
            "name": spec["name"],
            "description": spec["description"],
            "system_prompt": spec["system_prompt"],
            "middleware": sa_middleware,
        }
        if sa_tools is not None:
            sa["tools"] = sa_tools
        if spec.get("model") is not None:
            sa["model"] = spec["model"]

        result.append(sa)
    return result


def _prepare_external_subagents(
    specs: list[dict[str, Any]],
    config: LangclawConfig,
) -> list[dict[str, Any]]:
    """Prepare user-provided SubAgent / CompiledSubAgent dicts for deepagents.

    ``CompiledSubAgent`` dicts (containing ``"runnable"``) pass through
    unchanged — the user controls everything.

    ``SubAgent`` dicts (declarative, no ``"runnable"``) get Langclaw
    middleware prepended (channel context and, when enabled, RBAC) so
    they participate in the same request pipeline as Langclaw-managed
    subagents.
    """
    result: list[dict[str, Any]] = []
    for spec in specs:
        if "runnable" in spec:
            result.append(spec)
            continue

        sa_middleware: list[Any] = [ChannelContextMiddleware()]
        if config.permissions.enabled:
            sa_middleware.append(
                build_tool_permission_middleware(config.permissions),
            )

        existing_mw = list(spec.get("middleware", []))
        prepared = {**spec, "middleware": sa_middleware + existing_mw}
        result.append(prepared)
    return result


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def create_claw_agent(
    config: LangclawConfig,
    *,
    checkpointer: Checkpointer | None = None,
    cron_manager: CronManager | None = None,
    extra_tools: list[BaseTool | Any] | None = None,
    extra_skills: list[str] | None = None,
    extra_middleware: list[AgentMiddleware] | None = None,
    subagents: list[dict[str, Any]] | None = None,
    system_prompt: str | None = None,
    bus: BaseMessageBus | None = None,
    model: BaseChatModel | None = None,
) -> CompiledStateGraph:
    """
    Create a langclaw deep agent backed by ``deepagents.create_deep_agent``.

    The agent starts with the built-in default skills (summarize) and a
    persistent memory tool scoped to ``config.memories_dir``. Extra
    capabilities stack on top via ``extra_tools`` and ``extra_skills``.

    Args:
        config:          Loaded LangclawConfig.
        checkpointer:    LangGraph ``BaseCheckpointSaver`` for persisting
                         conversation state across turns. Without this the
                         agent starts fresh on every message.
        cron_manager:    Running ``CronManager`` instance. When provided and
                         ``config.cron.enabled`` is ``True``, the ``cron``
                         tool is added as a default tool so the agent can
                         schedule, list, and remove recurring jobs.
        extra_tools:     Additional LangChain tools beyond the defaults.
        extra_skills:    Paths to directories containing ``SKILL.md`` files.
        extra_middleware: Additional ``AgentMiddleware`` instances inserted
                         after the built-in middleware stack.
        subagents:       Unified list of subagent specs.  Partitioned by
                         shape at build time:

                         - Dicts with ``"output"`` — Langclaw declarative
                           specs.  Tool names are resolved and channel-
                           routing is handled.
                         - Dicts with ``"runnable"`` — ``CompiledSubAgent``
                           pass-throughs.  Used as-is by deepagents.
                         - Other dicts — external ``SubAgent`` specs.
                           Langclaw middleware is injected before passing
                           to deepagents.
        system_prompt:   Extra instructions appended after the base
                         ``AGENTS.md``.  ``None`` means use the base
                         prompt only.
        bus:             Running ``BaseMessageBus`` — required when any
                         subagent uses ``output="channel"`` (Phase 2).
        model:           Pre-built chat model. If omitted, resolved from config.

    Returns:
        A compiled LangGraph runnable (CompiledGraph) ready for ``.invoke``
        / ``.astream``.
    """
    try:
        from deepagents import create_deep_agent
        from deepagents.backends import FilesystemBackend
    except ImportError as exc:
        raise ImportError("deepagents is required. Install with: uv add deepagents") from exc

    resolved_model = model or init_chat_model(config.agents.model)

    skills = [config.agents.skills_source] + [
        to_virtual_path(s, config.agents.workspace_dir) for s in (extra_skills or [])
    ]

    tools: list[Any] = list(extra_tools or [])
    tools += build_web_tools(config)
    tools += build_gmail_tools(config)
    if cron_manager is not None:
        tools += build_cron_tools(config, cron_manager)

    base_prompt = config.agents.agents_md_file.read_text("utf-8")
    if system_prompt:
        system_prompt = f"{base_prompt}\n\n{system_prompt}"
    else:
        system_prompt = base_prompt

    # Built-in middleware stack (order matters):
    #   1. ChannelContextMiddleware  — inject channel metadata first
    #   2. ToolPermission middleware — filter tools per-user role
    #   3. RateLimitMiddleware       — rate-check early
    #   4. ContentFilterMiddleware   — block banned content
    #   5. PIIMiddleware             — redact PII
    #   6. caller-provided extras
    middleware: list[Any] = [
        ChannelContextMiddleware(),
    ]

    if config.permissions.enabled:
        middleware.append(
            build_tool_permission_middleware(config.permissions),
        )

    middleware.extend(
        [
            RateLimitMiddleware(rpm=config.agents.rate_limit_rpm),
            ContentFilterMiddleware(
                banned_keywords=config.agents.banned_keywords,
            ),
            PIIMiddleware(
                "azure_openai_api_key",
                detector=r"^[a-zA-Z0-9]{84}$",
                strategy="redact",
                apply_to_output=True,
                apply_to_tool_results=True,
            ),
            *(extra_middleware or []),
        ]
    )

    context_schema = LangclawContext

    # --- Subagents -----------------------------------------------------------
    # Partition by shape:
    #   "runnable" key  → CompiledSubAgent (pass through)
    #   "output" key    → Langclaw declarative (tool resolution + channel routing)
    #   otherwise       → external SubAgent dict (middleware injection only)
    managed_specs = [s for s in (subagents or []) if "output" in s]
    external_specs = [s for s in (subagents or []) if "output" not in s and "runnable" not in s]
    compiled_specs = [s for s in (subagents or []) if "runnable" in s]

    resolved_subagents: list[dict[str, Any]] = list(compiled_specs)

    if managed_specs:
        resolved_subagents.extend(_build_deepagent_subagents(managed_specs, tools, config))

        channel_routed = [s for s in managed_specs if s.get("output") == "channel"]
        if channel_routed:
            from langclaw.agents.subagents import build_channel_routed_subagent

            if bus is None:
                raise ValueError(
                    "A running message bus is required for channel-routed "
                    "subagents (output='channel'). This is normally provided "
                    "by app.run(); if using create_claw_agent directly, pass "
                    "the bus= argument."
                )
            for spec in channel_routed:
                sa_tools = _resolve_tools_by_name(spec.get("tools"), tools)
                resolved_subagents.append(
                    build_channel_routed_subagent(
                        spec=spec,
                        bus=bus,
                        tools=sa_tools or tools,
                        model=spec.get("model") or resolved_model,
                        config=config,
                    )
                )

    if external_specs:
        resolved_subagents.extend(_prepare_external_subagents(external_specs, config))

    final_subagents: list[dict[str, Any]] | None = resolved_subagents or None

    return create_deep_agent(
        model=resolved_model,
        tools=tools,
        skills=skills,
        system_prompt=system_prompt,
        checkpointer=checkpointer,
        backend=FilesystemBackend(
            root_dir=str(config.agents.workspace_dir),
            virtual_mode=True,
        ),
        middleware=middleware,
        context_schema=context_schema,
        subagents=final_subagents,
    )
