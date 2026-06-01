"""
ToolPermissionMiddleware — per-user tool filtering.

Uses LangChain's ``@wrap_model_call`` runtime-context pattern:

1. Gateway resolves ``user_id -> role`` from the channel's
   ``user_roles`` config.
2. The resolved role is passed as
   ``context={"user_role": "editor"}`` when invoking the agent.
3. This middleware reads ``request.runtime.context.user_role``
   and removes tools the role is not allowed to use *before*
   the model sees them.

Reference:
  https://docs.langchain.com/oss/python/langchain/agents
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import TYPE_CHECKING

from langchain.agents.middleware import wrap_model_call, wrap_tool_call
from loguru import logger

# Prefix carried by every workflow tool. Sourced from langclaw.naming (the single
# source of truth shared with the bridge + reservation guard); that module imports
# nothing from langclaw, so there is no cycle.
from langclaw.naming import WORKFLOW_TOOL_PREFIX as _WORKFLOW_TOOL_PREFIX

if TYPE_CHECKING:
    from langchain.agents.middleware import ModelRequest, ModelResponse, ToolCallRequest

    from langclaw.config.schema import PermissionsConfig


# ---------------------------------------------------------------------------
# Pure RBAC helpers — shared by the middleware *and* the interpreter PTC
# allowlist resolver so the two can never drift.
# ---------------------------------------------------------------------------


def allowed_tool_names(
    config: PermissionsConfig,
    role: str,
    all_tool_names: Iterable[str],
) -> set[str]:
    """Return the set of tool names *role* may invoke, given the live toolset.

    This is the single source of truth for tool-level RBAC.  Both
    :func:`build_tool_permission_middleware` and the interpreter PTC allowlist
    resolver call it, so a script can never reach a tool the requesting user
    is not permitted to call.

    Semantics mirror the middleware exactly:

    - An **unknown role** (not in ``config.roles``) is *not* filtered — it
      yields the full toolset (matching the middleware's pass-through).
    - A role whose ``tools`` contains ``"*"`` yields the full toolset.
    - Otherwise the role's listed tool names, intersected with what is
      actually available.

    Args:
        config:         RBAC definitions.
        role:           Resolved user role.
        all_tool_names: Names of the tools currently on offer.

    Returns:
        The allowed subset of ``all_tool_names``.
    """
    universe = set(all_tool_names)
    role_cfg = config.roles.get(role)
    if role_cfg is None or "*" in role_cfg.tools:
        return universe
    return set(role_cfg.tools) & universe


def allowed_subagents(config: PermissionsConfig, role: str) -> set[str]:
    """Return the subagent types *role* may invoke via ``tools.task``.

    **Default-deny**: a role that lists no ``subagents`` (or an unknown role)
    yields the empty set.  This is a separate axis from ``tools`` — a role with
    ``tools=["*"]`` still gets ``set()`` here unless it explicitly lists
    subagents.  A ``"*"`` entry is preserved verbatim (interpreted as
    "all subagents" by :func:`check_subagent_permission`).
    """
    role_cfg = config.roles.get(role)
    if role_cfg is None:
        return set()
    return set(role_cfg.subagents)


def allowed_workflow_names(
    config: PermissionsConfig,
    role: str,
    all_workflow_names: Iterable[str],
) -> set[str]:
    """Return the set of workflow names *role* may invoke, given the registry.

    The third RBAC axis, alongside :func:`allowed_tool_names` and
    :func:`allowed_subagents`.  Semantics mirror ``allowed_subagents``
    (**default-deny**), not ``allowed_tool_names`` (pass-through):

    - An **unknown role** (not in ``config.roles``) yields the empty set.
    - A role whose ``workflows`` contains ``"*"`` yields every registered
      workflow.
    - Otherwise the role's listed workflow names, intersected with what is
      actually registered.

    Default-deny is deliberate: a workflow can compose tools and subagents, so
    a role with ``tools=["*"]`` should still not reach a workflow unless it is
    explicitly granted.  Shared by the ``workflow_<name>`` tool gate, the
    ``/workflow`` command, cron dispatch, and the PTC workflow-namespace
    resolver so the axis cannot drift (unification tracked in #37).

    Args:
        config:             RBAC definitions.
        role:               Resolved user role.
        all_workflow_names: Names of the workflows currently registered.

    Returns:
        The allowed subset of ``all_workflow_names``.
    """
    universe = set(all_workflow_names)
    role_cfg = config.roles.get(role)
    if role_cfg is None:
        return set()
    if "*" in role_cfg.workflows:
        return universe
    return set(role_cfg.workflows) & universe


def check_subagent_permission(
    subagent_type: str,
    allowed: Iterable[str],
) -> str | None:
    """Validate a ``tools.task`` target against the caller's allowlist.

    Returns ``None`` when the call is permitted, or an error message string
    when it is not — callers surface this as ``{"error": ...}`` to the script
    rather than raising into the agent loop.
    """
    allowed_set = set(allowed)
    if "*" in allowed_set or subagent_type in allowed_set:
        return None
    permitted = ", ".join(sorted(n for n in allowed_set if n != "*")) or "(none)"
    return (
        f"Subagent {subagent_type!r} is not permitted for your role. "
        f"Allowed subagents: {permitted}."
    )


def build_subagent_permission_middleware(
    config: PermissionsConfig,
) -> Callable:
    """Return a ``@wrap_tool_call`` middleware enforcing ``RoleConfig.subagents``.

    The deepagents ``task`` tool (subagent delegation) is built by
    ``SubAgentMiddleware`` and never sees langclaw's RBAC on its own — so a
    role's :attr:`~langclaw.config.schema.RoleConfig.subagents` allowlist was
    declared but unenforced.  This middleware closes that gap on the
    *model-invoked* path: it intercepts every ``task`` call, resolves the
    caller's role, and short-circuits a disallowed ``subagent_type`` with an
    error :class:`~langchain_core.messages.ToolMessage` (status ``"error"``)
    instead of letting the subagent run — and without raising into the agent
    loop.

    Per-type granularity lives here.  The *PTC* path (``tools.task(...)`` inside
    an ``eval`` script) bypasses the ``ToolNode``, so it is gated more coarsely
    in :func:`langclaw.interpreter.resolve_ptc_allowlist` (``task`` is dropped
    from the script surface entirely when the role may use zero subagents).
    Both paths read the same :func:`allowed_subagents` /
    :func:`check_subagent_permission` helpers so they cannot drift.  Unifying
    the two enforcement seams is tracked in issue #37.
    """

    @wrap_tool_call
    async def _subagent_gate(
        request: ToolCallRequest,
        handler: Callable,
    ):
        tool_call = request.tool_call
        if tool_call.get("name") != "task":
            return await _maybe_await(handler(request))

        subagent_type = (tool_call.get("args") or {}).get("subagent_type", "")

        runtime = getattr(request, "runtime", None)
        ctx = getattr(runtime, "context", None) if runtime else None
        role = getattr(ctx, "user_role", config.default_role) if ctx else config.default_role

        allowed = allowed_subagents(config, role)
        error = check_subagent_permission(subagent_type, allowed)
        if error is None:
            return await _maybe_await(handler(request))

        logger.warning(
            f"Subagent gate: role={role!r} blocked subagent {subagent_type!r}",
        )
        from langchain_core.messages import ToolMessage

        return ToolMessage(
            content=error,
            tool_call_id=tool_call.get("id", ""),
            status="error",
        )

    return _subagent_gate


async def _maybe_await(value):
    """Await *value* if it is awaitable, else return it.

    ``handler`` may be sync (returning a ``ToolMessage``) or async (returning a
    coroutine); normalise both so the gate works under either driver.
    """
    if hasattr(value, "__await__"):
        return await value
    return value


def build_tool_permission_middleware(
    config: PermissionsConfig,
) -> Callable:
    """Return a ``@wrap_model_call`` middleware closed over *config*.

    Filters the tool list on every model call based on the user's
    role (from ``request.runtime.context.user_role``).
    """

    @wrap_model_call
    async def _tool_permission_filter(
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelResponse:
        runtime = request.runtime
        ctx = getattr(runtime, "context", None) if runtime else None
        if ctx is not None:
            user_role = getattr(ctx, "user_role", config.default_role)
        else:
            user_role = config.default_role

        # ``workflow_<name>`` tools are governed by the *workflow* RBAC axis
        # (build_workflow_permission_middleware), not the tool axis — exclude
        # them here so the two axes don't double-gate or fight each other.
        gated = [t for t in request.tools if not t.name.startswith(_WORKFLOW_TOOL_PREFIX)]
        all_names = [t.name for t in gated]
        allowed = allowed_tool_names(config, user_role, all_names)
        if allowed == set(all_names):
            logger.debug(
                f"Permissions: role={user_role} allowed all tools for this call",
            )
            return await handler(request)

        # Keep allowed tool-axis tools AND all workflow_* tools (passed through).
        filtered = [
            t
            for t in request.tools
            if t.name in allowed or t.name.startswith(_WORKFLOW_TOOL_PREFIX)
        ]
        logger.debug(
            f"Permissions: role={user_role} allowed tools {allowed} for this call",
        )

        return await handler(request.override(tools=filtered))

    return _tool_permission_filter


def build_workflow_permission_middleware(
    config: PermissionsConfig,
) -> Callable:
    """Return a ``@wrap_model_call`` middleware enforcing ``RoleConfig.workflows``.

    The third RBAC axis.  Registered workflows are exposed to the agent as
    ``workflow_<name>`` tools; this filter removes any whose bare name the
    caller's role is not granted via
    :func:`allowed_workflow_names` (**default-deny**).  Non-workflow tools pass
    through untouched — the tool axis owns those.

    Because the interpreter middleware recomputes its PTC surface from the live
    ``request.tools`` on every call, stripping a ``workflow_<name>`` tool here
    also removes it from a script's ``tools.workflow<Name>`` reach (Mode 1) — one
    gate covers both the direct tool call and the PTC call.
    """

    @wrap_model_call
    async def _workflow_permission_filter(
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelResponse:
        runtime = request.runtime
        ctx = getattr(runtime, "context", None) if runtime else None
        user_role = getattr(ctx, "user_role", config.default_role) if ctx else config.default_role

        wf_tools = [t for t in request.tools if t.name.startswith(_WORKFLOW_TOOL_PREFIX)]
        if not wf_tools:
            return await handler(request)

        bare_names = [t.name[len(_WORKFLOW_TOOL_PREFIX) :] for t in wf_tools]
        permitted = allowed_workflow_names(config, user_role, bare_names)
        permitted_full = {f"{_WORKFLOW_TOOL_PREFIX}{n}" for n in permitted}

        kept = [
            t
            for t in request.tools
            if not t.name.startswith(_WORKFLOW_TOOL_PREFIX) or t.name in permitted_full
        ]
        if len(kept) == len(request.tools):
            return await handler(request)

        logger.debug(
            f"Workflow permissions: role={user_role} allowed workflows {permitted}",
        )
        return await handler(request.override(tools=kept))

    return _workflow_permission_filter


__all__ = [
    "allowed_subagents",
    "allowed_tool_names",
    "allowed_workflow_names",
    "build_subagent_permission_middleware",
    "build_tool_permission_middleware",
    "build_workflow_permission_middleware",
    "check_subagent_permission",
]
