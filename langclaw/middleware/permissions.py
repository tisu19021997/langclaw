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

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from langchain.agents.middleware import wrap_model_call
from loguru import logger

if TYPE_CHECKING:
    from langchain.agents.middleware import ModelRequest, ModelResponse

    from langclaw.config.schema import PermissionsConfig


@dataclass
class LangclawContext:
    """Runtime context schema for ``create_agent``."""

    user_role: str = field(default="viewer")


def build_tool_permission_middleware(
    config: PermissionsConfig,
) -> Callable:
    """Return a ``@wrap_model_call`` middleware closed over *config*.

    Filters the tool list on every model call based on the user's
    role (from ``request.runtime.context.user_role``).
    """

    @wrap_model_call
    def _tool_permission_filter(
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelResponse:
        runtime = request.runtime
        ctx = getattr(runtime, "context", None) if runtime else None
        if ctx is not None:
            user_role = getattr(ctx, "user_role", config.default_role)
        else:
            user_role = config.default_role

        role_cfg = config.roles.get(user_role)
        if role_cfg is None or "*" in role_cfg.tools:
            return handler(request)

        allowed = set(role_cfg.tools)
        filtered = [t for t in request.tools if t.name in allowed]
        if len(filtered) != len(request.tools):
            removed = {t.name for t in request.tools} - allowed
            logger.debug(
                "Permissions: role={!r} removed tools {}" " for this call",
                user_role,
                removed,
            )

        return handler(request.override(tools=filtered))

    return _tool_permission_filter


__all__ = [
    "LangclawContext",
    "build_tool_permission_middleware",
]
