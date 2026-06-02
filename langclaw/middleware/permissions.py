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

# Unified capability resolution — every RBAC axis (tools, subagents, workflows)
# routes through one descriptor-driven resolver so they cannot drift (issue #37).
from langclaw.rbac import CAPABILITY_AXES, SUBAGENTS, TOOLS, WORKFLOWS, resolve_capability

if TYPE_CHECKING:
    from langchain.agents.middleware import ModelRequest, ModelResponse, ToolCallRequest

    from langclaw.config.schema import PermissionsConfig


# ---------------------------------------------------------------------------
# Pure RBAC helpers — named, readable façades over the unified
# :func:`langclaw.rbac.resolve_capability` resolver. Kept as the public API the
# interpreter PTC resolver, the bridge, and the gateway call; each is a thin
# delegation, so a script can never reach a capability the requesting role
# lacks and the per-axis semantics cannot drift from the registry.
# ---------------------------------------------------------------------------


def allowed_tool_names(
    config: PermissionsConfig,
    role: str,
    all_tool_names: Iterable[str],
) -> set[str]:
    """Return the set of tool names *role* may invoke, given the live toolset.

    The tool axis is **pass-through** for unknown roles (an unrecognised role
    sees the full toolset); ``"*"`` grants all; otherwise the role's listed
    names intersected with what is on offer. See
    :func:`langclaw.rbac.resolve_capability`.
    """
    return resolve_capability(TOOLS, config, role, all_tool_names)


def allowed_subagents(config: PermissionsConfig, role: str) -> set[str]:
    """Return the subagent types *role* may invoke via ``tools.task``.

    **Default-deny**: a role that lists no ``subagents`` (or an unknown role)
    yields the empty set, even with ``tools=["*"]``. A ``"*"`` entry is
    preserved verbatim (interpreted as "all subagents" by
    :func:`check_subagent_permission`).
    """
    return resolve_capability(SUBAGENTS, config, role)


def allowed_workflow_names(
    config: PermissionsConfig,
    role: str,
    all_workflow_names: Iterable[str],
) -> set[str]:
    """Return the set of workflow names *role* may invoke, given the registry.

    **Default-deny** (like ``subagents``): an unknown role yields the empty set;
    ``"*"`` grants every registered workflow; otherwise the role's listed names
    intersected with what is registered. Default-deny is deliberate — a workflow
    composes tools and subagents, so ``tools=["*"]`` must not implicitly grant
    it. Shared by the ``workflow_<name>`` tool gate, the ``/workflows`` command,
    cron dispatch, and the PTC workflow-namespace resolver via
    :func:`langclaw.rbac.resolve_capability`.
    """
    return resolve_capability(WORKFLOWS, config, role, all_workflow_names)


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

    The subagent axis is the one capability that maps to a tool *argument*
    (``task``'s ``subagent_type``) rather than a tool *name*, so it keeps this
    dedicated ``wrap_tool_call`` seam instead of riding the
    :func:`build_capability_filter_middleware` list-filter. The *PTC* path
    (``tools.task(...)`` inside an ``eval`` script) bypasses the ``ToolNode``, so
    it is gated more coarsely in
    :func:`langclaw.interpreter.resolve_ptc_allowlist` (``task`` is dropped from
    the script surface entirely when the role may use zero subagents; finer
    per-type PTC gating is a known limitation). All three paths *resolve* through
    the same :func:`langclaw.rbac.resolve_capability` (via :func:`allowed_subagents`)
    plus :func:`check_subagent_permission`, so the axis cannot drift.
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

        role = _resolve_role(request, config.default_role)

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


def _resolve_role(request, default_role: str) -> str:
    """Read the caller's role off the model/tool request runtime context."""
    runtime = getattr(request, "runtime", None)
    ctx = getattr(runtime, "context", None) if runtime else None
    if ctx is None:
        return default_role
    return getattr(ctx, "user_role", default_role)


def build_capability_filter_middleware(
    config: PermissionsConfig,
) -> Callable:
    """Return the single ``@wrap_model_call`` seam enforcing every tool-mapped axis.

    The unified enforcement seam (issue #37). It governs **every** RBAC axis that
    maps to a tool name — the tool axis (residual, un-prefixed names) and the
    workflow axis (``workflow_<name>``) — in one pass, replacing the two separate
    ``wrap_model_call`` filters that previously had to coordinate by hand (the
    tool filter explicitly skipping ``workflow_*``, the workflow filter only
    touching them).

    Each tool in ``request.tools`` is classified to an axis by
    :attr:`~langclaw.rbac.CapabilityAxis.tool_prefix` (longest-match), or to the
    residual tool axis when it matches no prefix. Per axis, the role's allowed
    set is resolved via :func:`~langclaw.rbac.resolve_capability` and the kept
    names unioned; original tool order is preserved.

    A new prefixed capability axis becomes enforced here automatically the moment
    it is declared in :data:`langclaw.rbac.CAPABILITY_AXES` — no edit to this
    function. Because the interpreter middleware recomputes its PTC surface from
    the live ``request.tools`` each call, a tool stripped here is also unreachable
    from a script (one gate covers both the direct call and the PTC call).
    """
    prefix_axes = [a for a in CAPABILITY_AXES if a.tool_prefix is not None]
    # Longest prefix first so nested prefixes classify deterministically.
    prefix_axes.sort(key=lambda a: len(a.tool_prefix or ""), reverse=True)
    residual_axis = next((a for a in CAPABILITY_AXES if a.is_residual_tool_axis), None)

    @wrap_model_call
    async def _capability_filter(
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelResponse:
        role = _resolve_role(request, config.default_role)

        # Bucket tools by axis (residual = matched no prefix).
        buckets: dict[str, list] = {a.name: [] for a in prefix_axes}
        residual_tools: list = []
        for t in request.tools:
            for axis in prefix_axes:
                if t.name.startswith(axis.tool_prefix):
                    buckets[axis.name].append(t)
                    break
            else:
                residual_tools.append(t)

        allowed_names: set[str] = set()
        if residual_axis is not None:
            names = [t.name for t in residual_tools]
            allowed_names |= resolve_capability(residual_axis, config, role, names)
        else:
            allowed_names |= {t.name for t in residual_tools}

        for axis in prefix_axes:
            group = buckets[axis.name]
            plen = len(axis.tool_prefix)
            bare = [t.name[plen:] for t in group]
            permitted = resolve_capability(axis, config, role, bare)
            allowed_names |= {f"{axis.tool_prefix}{n}" for n in permitted}

        kept = [t for t in request.tools if t.name in allowed_names]
        if len(kept) == len(request.tools):
            logger.debug(f"Permissions: role={role} allowed all capabilities for this call")
            return await handler(request)

        logger.debug(
            f"Permissions: role={role} kept {sorted(allowed_names)} for this call",
        )
        return await handler(request.override(tools=kept))

    return _capability_filter


# Back-compat aliases — the per-axis builders were unified into one filter
# (issue #37). Existing imports keep working; both now return the all-axis seam.
build_tool_permission_middleware = build_capability_filter_middleware
build_workflow_permission_middleware = build_capability_filter_middleware


__all__ = [
    "allowed_subagents",
    "allowed_tool_names",
    "allowed_workflow_names",
    "build_capability_filter_middleware",
    "build_subagent_permission_middleware",
    "build_tool_permission_middleware",
    "build_workflow_permission_middleware",
    "check_subagent_permission",
]
