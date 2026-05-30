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

from langchain.agents.middleware import wrap_model_call
from loguru import logger

if TYPE_CHECKING:
    from langchain.agents.middleware import ModelRequest, ModelResponse

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

        all_names = [t.name for t in request.tools]
        allowed = allowed_tool_names(config, user_role, all_names)
        if allowed == set(all_names):
            logger.debug(
                f"Permissions: role={user_role} allowed all tools for this call",
            )
            return await handler(request)

        filtered = [t for t in request.tools if t.name in allowed]
        logger.debug(
            f"Permissions: role={user_role} allowed tools {allowed} for this call",
        )

        return await handler(request.override(tools=filtered))

    return _tool_permission_filter


__all__ = [
    "allowed_subagents",
    "allowed_tool_names",
    "build_tool_permission_middleware",
    "check_subagent_permission",
]
