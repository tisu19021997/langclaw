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
from loguru import logger

from langclaw.agents.tools import (
    build_cron_tools,
    build_fs_tools,
    build_gmail_tools,
    build_web_tools,
)
from langclaw.config.schema import LangclawConfig
from langclaw.context import LangclawContext
from langclaw.middleware.channel_context import ChannelContextMiddleware
from langclaw.middleware.guardrails import ContentFilterMiddleware, PIIMiddleware
from langclaw.middleware.permissions import (
    build_subagent_permission_middleware,
    build_tool_permission_middleware,
    build_workflow_permission_middleware,
)
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

# The interpreter system-prompt nudge is built dynamically per agent (it lists
# the live, camelCased PTC tool names so the model calls them correctly) — see
# ``langclaw.interpreter.interpreter_system_prompt``.

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
    context_schema: type[LangclawContext],
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
            sa_middleware.append(
                build_subagent_permission_middleware(config.permissions),
            )

        sa: dict[str, Any] = {
            "name": spec["name"],
            "description": spec["description"],
            "system_prompt": spec["system_prompt"],
            "middleware": sa_middleware,
            "context_schema": context_schema,
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
            sa_middleware.append(
                build_subagent_permission_middleware(config.permissions),
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
    backend: Any | None = None,
    context_schema: type[LangclawContext] | None = None,
    agent_name: str | None = None,
    display_name: str | None = None,
    workflow_registry: Any | None = None,
    workflow_runtime: Any | None = None,
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
        backend:         A deepagents backend instance (or factory callable).
                         Overrides ``config.agents.backend``.  Use this for the
                         advanced backends that need live objects — ``StoreBackend``
                         with a custom store/namespace, ``CompositeBackend``, a
                         sandbox.  ``None`` (default) builds the config-selected
                         backend (``local_shell`` by default, which exposes the
                         ``execute`` tool) rooted at the agent's workspace.
        context_schema:  Custom context schema to use for the agent. If omitted,
                         uses the default LangclawContext.
        agent_name:      Name of the named agent being built.  When provided,
                         the agent's workspace is scoped to
                         ``config.agents.workspace_dir / agent_name`` so each
                         agent has isolated skills, AGENTS.md, and memories.
                         ``None`` (default) keeps the global workspace used by
                         the default agent.
        display_name:    Optional human-facing name for the agent. When set,
                         a ``Your name is <display_name>.`` line is prepended
                         to the system prompt so the model knows its own name.
    Returns:
        A compiled LangGraph runnable (CompiledGraph) ready for ``.invoke``
        / ``.astream``.
    """
    try:
        from deepagents import create_deep_agent
    except ImportError as exc:
        raise ImportError("deepagents is required. Install with: uv add deepagents") from exc

    from langclaw.agents.backend import backend_root_dir, make_backend

    # Per-agent workspace: named agents get an isolated subdirectory;
    # the default agent keeps the global workspace.
    workspace_dir = (
        config.agents.workspace_dir / agent_name if agent_name else config.agents.workspace_dir
    )

    # Resolve the deepagents backend. An explicit instance (store / composite /
    # sandbox / custom) wins; otherwise build the config-selected one rooted at
    # this agent's workspace. ``fs_root`` is the host directory for filesystem-
    # rooted backends (FilesystemBackend / LocalShellBackend) and ``None`` for
    # backends that don't live on the local filesystem (state / store) — every
    # local-filesystem assumption below keys off it.
    resolved_backend = (
        backend if backend is not None else make_backend(config.agents.backend, workspace_dir)
    )
    fs_root = backend_root_dir(resolved_backend)

    # Only create a real directory when the backend actually uses one.
    if fs_root is not None:
        fs_root.mkdir(parents=True, exist_ok=True)

    resolved_model = model or init_chat_model(config.agents.model, **config.agents.model_kwargs)

    skills = [config.agents.skills_source] + [
        to_virtual_path(s, workspace_dir) for s in (extra_skills or [])
    ]

    # Build all available built-in tools first.
    builtin_tools: list[Any] = []
    builtin_tools += build_web_tools(config)
    builtin_tools += build_gmail_tools(config)
    # ``move_file`` / ``delete_file`` manipulate a real directory directly, so
    # they only make sense for filesystem-rooted backends. Non-filesystem
    # backends (state / store) rely on deepagents' own backend-delegated file
    # tools (ls / read_file / write_file / edit_file) instead.
    if fs_root is not None:
        builtin_tools += build_fs_tools(config, fs_root)
    if cron_manager is not None:
        builtin_tools += build_cron_tools(config, cron_manager)

    # Process extra_tools: resolve string names to actual tools, keep tool objects as-is.
    extra_tool_objects: list[Any] = []
    extra_tool_names: list[str] = []
    for item in extra_tools or []:
        if isinstance(item, str):
            extra_tool_names.append(item)
        else:
            extra_tool_objects.append(item)

    # If extra_tools specifies tool names, select only those from built-in tools.
    # Otherwise, use all built-in tools plus any extra tool objects.
    if extra_tool_names:
        tools = _resolve_tools_by_name(extra_tool_names, builtin_tools + extra_tool_objects)
        if tools is None:
            tools = []
        tools += extra_tool_objects
    else:
        tools = builtin_tools + extra_tool_objects

    # Workflow primitive (opt-in, `config.workflows.enabled`): expose each
    # registered workflow as a `workflow_<name>` tool. The step executor closes
    # over the live toolset, so steps reach exactly the tools the agent has.
    _workflows_active = (
        config.workflows.enabled
        and workflow_registry is not None
        and workflow_runtime is not None
        and len(workflow_registry) > 0
    )
    _workflow_ptc_names: list[str] = []
    if _workflows_active:
        from langclaw.workflows import (
            build_toolset_executor,
            build_workflow_author,
            build_workflow_script_runner,
            make_workflow_tools,
            resolve_workflow_ptc_names,
            select_workflow_tools,
        )

        _live_tools = tools

        async def _workflow_executor_factory(_tool_runtime: Any) -> Any:
            return build_toolset_executor(_live_tools)

        def _tools_for_spec(spec: Any) -> list[Any]:
            # Mode 2 / saved allowlist: spec.uses_tools ∩ live toolset (none → none).
            # uses_tools names the camelCase sandbox surface (e.g. "webFetch"); the
            # live tool name is snake_case ("web_fetch"), so match on either form —
            # otherwise web_fetch/web_search silently drop out of the sandbox.
            return select_workflow_tools(_live_tools, spec.uses_tools)

        def _workflow_author_factory(spec: Any) -> Any:
            return build_workflow_author(resolved_model, tools=_tools_for_spec(spec))

        def _workflow_script_runner_factory(spec: Any) -> Any:
            return build_workflow_script_runner(_tools_for_spec(spec))

        tools = tools + make_workflow_tools(
            workflow_registry,
            workflow_runtime,
            executor_factory=_workflow_executor_factory,
            author_factory=_workflow_author_factory,
            script_runner_factory=_workflow_script_runner_factory,
        )
        # Let non-tool entry points (bus dispatch / cron / startup resume) rebuild
        # the step executor and Mode-2 callables over this live toolset.
        workflow_runtime.set_resume_executor_factory(_workflow_executor_factory)
        workflow_runtime.set_authoring_factories(
            author_factory=_workflow_author_factory,
            script_runner_factory=_workflow_script_runner_factory,
        )
        # Mode 1 PTC names (build-time, unnarrowed; per-call RBAC narrows later).
        _workflow_ptc_names = resolve_workflow_ptc_names(
            workflow_registry, workflows_config=config.workflows
        )

    # Runtime workflow authoring: the agent saves a workflow by writing a
    # `workflows/<name>.js` file with its ordinary `write_file` tool (no bespoke
    # tool). Active when the workflow primitive AND the interpreter are enabled
    # (a saved body runs in the eval sandbox) AND the backend is filesystem-rooted
    # (`fs_root` exists) — state/store backends have no host folder for the loader
    # to watch, so authoring-by-file is unavailable there. Drives the prompt nudge;
    # the gateway watches the folder and loads what gets written.
    _workflow_authoring_active = (
        config.workflows.enabled
        and config.interpreter.enabled
        and workflow_registry is not None
        and workflow_runtime is not None
        and fs_root is not None
    )

    # Resolve the base ``AGENTS.md`` system prompt. For filesystem-rooted
    # backends prefer the agent's on-disk workspace, then the global workspace,
    # then the packaged default. Non-filesystem backends have no host workspace
    # to read from, so they go straight to the packaged default.
    if fs_root is not None:
        agents_md = fs_root / "AGENTS.md"
        if not agents_md.exists():
            agents_md = config.agents.agents_md_file  # fall back to global
        if not agents_md.exists():
            agents_md = _DEFAULT_AGENTS_MD  # fall back to packaged default
    else:
        agents_md = _DEFAULT_AGENTS_MD
    base_prompt = agents_md.read_text("utf-8") if agents_md.exists() else ""
    if system_prompt:
        system_prompt = f"{base_prompt}\n\n{system_prompt}"
    else:
        system_prompt = base_prompt

    if config.interpreter.enabled:
        from langclaw.interpreter import interpreter_system_prompt

        system_prompt = f"{system_prompt}\n\n{interpreter_system_prompt(config, tools)}"

    # Make workflows discoverable to the model (mirrors the interpreter nudge).
    # Emitted when there are workflows to list OR the agent can author them via
    # write_file — so the run → save loop is explained even before the first
    # workflow exists. An agent with neither sees nothing.
    if _workflows_active or _workflow_authoring_active:
        from langclaw.workflows import workflow_system_prompt

        nudge = workflow_system_prompt(workflow_registry, authoring=_workflow_authoring_active)
        if nudge:
            system_prompt = f"{system_prompt}\n\n{nudge}"

    if display_name:
        system_prompt = f"Your name is {display_name}.\n\n{system_prompt}"

    if config.debug:
        _label = agent_name or "default"
        logger.info(
            "[debug] system_prompt for agent '{}' (AGENTS.md: {}):\n{}",
            _label,
            agents_md if agents_md.exists() else "(none)",
            system_prompt,
        )

    # Built-in middleware stack (order matters):
    #   1. ChannelContextMiddleware  — inject channel metadata first
    #   2. ToolPermission middleware — filter tools per-user role
    #   3. Subagent gate             — enforce RoleConfig.subagents on `task`
    #   4. RateLimitMiddleware       — rate-check early
    #   5. ContentFilterMiddleware   — block banned content
    #   6. PIIMiddleware             — redact PII
    #   7. caller-provided extras
    middleware: list[Any] = [
        ChannelContextMiddleware(),
    ]

    if config.permissions.enabled:
        middleware.append(
            build_tool_permission_middleware(config.permissions),
        )
        # Enforce the per-role subagent allowlist on model-invoked `task` calls.
        # The tool-permission filter governs *which tools* are visible; this
        # governs *which subagent_type* a visible `task` tool may target.
        middleware.append(
            build_subagent_permission_middleware(config.permissions),
        )
        # Enforce the per-role workflow allowlist on `workflow_<name>` tools
        # (the third RBAC axis). Runs before the interpreter so a role-stripped
        # workflow tool is unreachable from PTC too (Mode 1).
        if _workflows_active:
            middleware.append(
                build_workflow_permission_middleware(config.permissions),
            )

    # Code interpreter (opt-in). Placed *after* the permission filter so the
    # PTC surface only ever sees the role-filtered live toolset — per-call RBAC
    # for scripts falls out of middleware ordering rather than per-tool wrapping.
    if config.interpreter.enabled:
        from langclaw.interpreter import build_interpreter_middleware

        interpreter_mw = build_interpreter_middleware(
            config, tools, extra_ptc_tool_names=_workflow_ptc_names
        )
        if interpreter_mw is not None:
            middleware.append(interpreter_mw)

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
                # apply_to_tool_results=True,
            ),
            *(extra_middleware or []),
        ]
    )

    # Default to LangclawContext if not provided
    context_schema = context_schema or LangclawContext

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
        resolved_subagents.extend(
            _build_deepagent_subagents(managed_specs, tools, config, context_schema)
        )

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
                        context_schema=context_schema,
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
        backend=resolved_backend,
        middleware=middleware,
        context_schema=context_schema,
        subagents=final_subagents,
    )
