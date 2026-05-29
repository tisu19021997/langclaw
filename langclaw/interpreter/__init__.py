"""
Scripted code-interpreter (RLM) support for langclaw — issue #33.

Two deep, isolated modules live here:

- :func:`resolve_ptc_allowlist` — a *pure* function mapping
  ``(available tools, role, interpreter config) -> list[str]``, the set of
  tool names exposed to a script via Programmatic Tool Calling (PTC).  It is
  built on the shared :func:`langclaw.middleware.permissions.allowed_tool_names`
  so the PTC surface can never grant a tool the requesting role lacks.
- :func:`build_interpreter_middleware` — wraps ``langchain-quickjs``'s
  ``CodeInterpreterMiddleware`` with langclaw defaults and the resolved PTC
  allowlist, or returns ``None`` when the interpreter is disabled.

The QuickJS sandbox is capability-scoped (no fs/network/shell); the real blast
radius is the exposed tools, so the allowlist defaults to read-only and
mutating/egress tools require explicit operator opt-in via
``InterpreterConfig.allow_tools``.
"""

from __future__ import annotations

import re
import warnings
from typing import TYPE_CHECKING, Any

from loguru import logger

from langclaw.middleware.permissions import allowed_tool_names

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    from langchain.agents.middleware import AgentMiddleware

    from langclaw.config.schema import InterpreterConfig, LangclawConfig, PermissionsConfig


# Read-only / safe-by-default built-ins exposed to scripts without opt-in.
# Mutating or egress tools (delete_file, move_file, write_file, edit_file,
# gmail send, cron, ...) are intentionally excluded — operators add them
# through ``InterpreterConfig.allow_tools``.
#
# ``task`` (the deepagents subagent-delegation tool) is included so scripts can
# recurse over registered subagents; the *which* subagent is gated separately
# by the per-role subagent allowlist
# (:func:`langclaw.middleware.permissions.allowed_subagents`).
DEFAULT_READONLY_PTC_TOOLS: frozenset[str] = frozenset(
    {
        "web_search",
        "web_fetch",
        "ls",
        "read_file",
        "glob",
        "grep",
        "task",
    }
)

# Tools deepagents injects at runtime (filesystem + subagent delegation) that
# are not present in the build-time toolset but *will* appear in the live
# ``request.tools`` the interpreter middleware filters against.
RUNTIME_INJECTED_TOOLS: frozenset[str] = frozenset(
    {"ls", "read_file", "glob", "grep", "write_file", "edit_file", "task"}
)

_CAMEL_SEP = re.compile(r"[-_]+(\w)")


def _to_camel_case(name: str) -> str:
    """``snake_case`` / ``kebab-case`` → ``camelCase``.

    Prefers ``langchain_quickjs``'s implementation when the extra is installed
    so collision detection matches what the REPL actually does; falls back to a
    local copy so this pure module stays importable without the extra.
    """
    try:
        from langchain_quickjs import _ptc

        return _ptc.to_camel_case(name)
    except Exception:
        return _CAMEL_SEP.sub(lambda m: m.group(1).upper(), name)


def _reject_ptc_name_collisions(names: Iterable[str]) -> None:
    """Raise ``ValueError`` if two distinct names camelCase to the same id.

    Inside a script, tools are reached as ``tools.<camelCaseName>``; if two
    tool names collapse to the same identifier one would silently shadow the
    other, so we fail loudly at resolution time instead.
    """
    by_camel: dict[str, list[str]] = {}
    for name in names:
        by_camel.setdefault(_to_camel_case(name), []).append(name)
    collisions = {camel: src for camel, src in by_camel.items() if len(set(src)) > 1}
    if collisions:
        details = "; ".join(
            f"{sorted(set(src))} → tools.{camel}" for camel, src in sorted(collisions.items())
        )
        raise ValueError(
            "PTC tool name collision — these tools map to the same JavaScript "
            f"identifier and would shadow each other: {details}. "
            "Rename one of the colliding tools."
        )


def resolve_ptc_allowlist(
    available_tools: Sequence[Any],
    *,
    interpreter_config: InterpreterConfig,
    permissions_config: PermissionsConfig | None = None,
    role: str | None = None,
    runtime_tool_names: Iterable[str] = (),
) -> list[str]:
    """Resolve which tool names a script may call via ``tools.<name>``.

    Pure and side-effect free.  Resolution order:

    1. Interpreter disabled → ``[]``.
    2. Build the candidate *universe* = build-time tool names ∪
       ``runtime_tool_names`` (deepagents-injected tools like ``task``).
    3. Operator surface = the read-only default ∩ universe, plus
       ``interpreter_config.allow_tools`` ∩ universe.  ``allow_tools=["*"]``
       exposes the whole universe.
    4. Per-role narrowing (when ``role`` + an enabled ``permissions_config``
       are given): intersect with
       :func:`~langclaw.middleware.permissions.allowed_tool_names` so the PTC
       surface is a subset of the role's permitted tools.
    5. Reject camelCase identifier collisions.

    Args:
        available_tools:    The build-time toolset (objects with ``.name``).
        interpreter_config: The resolved :class:`InterpreterConfig`.
        permissions_config: RBAC config, for per-role narrowing.
        role:               Resolved user role, for per-role narrowing.
        runtime_tool_names: Names of tools injected at runtime (e.g. ``task``).

    Returns:
        A sorted list of tool names to pass as the middleware's ``ptc`` option.
    """
    if not interpreter_config.enabled:
        return []

    universe = {getattr(t, "name", None) for t in available_tools}
    universe.discard(None)
    universe |= set(runtime_tool_names)

    allow_tools = set(interpreter_config.allow_tools)
    if "*" in allow_tools:
        surface = set(universe)
    else:
        surface = (DEFAULT_READONLY_PTC_TOOLS & universe) | (allow_tools & universe)

    if role is not None and permissions_config is not None and permissions_config.enabled:
        surface &= allowed_tool_names(permissions_config, role, universe)

    _reject_ptc_name_collisions(surface)
    return sorted(surface)


def build_interpreter_middleware(
    config: LangclawConfig,
    available_tools: Sequence[Any],
    *,
    role: str | None = None,
) -> AgentMiddleware | None:
    """Build the code-interpreter middleware, or ``None`` when disabled.

    Returns ``None`` when ``config.interpreter.enabled`` is ``False`` so the
    feature is genuinely inert unless opted in.  When enabled, wraps
    ``langchain-quickjs``'s ``CodeInterpreterMiddleware`` with langclaw's
    resource limits and the resolved (read-only-by-default) PTC allowlist.

    Per-call RBAC for the PTC surface is handled by ordering: the interpreter
    middleware is placed *after* ``ToolPermissionMiddleware`` in the stack, so
    it only ever sees the role-filtered live toolset.  ``role`` here is an
    optional extra narrowing applied at build time (defence in depth).

    Args:
        config:          Loaded :class:`LangclawConfig`.
        available_tools: The agent's build-time toolset.
        role:            Optional role for build-time allowlist narrowing.

    Returns:
        A ``CodeInterpreterMiddleware`` instance, or ``None`` when disabled.

    Raises:
        ImportError: If the interpreter is enabled but the ``interpreter``
            extra (``langchain-quickjs``) is not installed.
    """
    icfg = config.interpreter
    if not icfg.enabled:
        return None

    try:
        from langchain_quickjs import CodeInterpreterMiddleware
    except ImportError as exc:
        raise ImportError(
            "The code interpreter is enabled but 'langchain-quickjs' is not "
            "installed. Install the interpreter extra with: "
            "uv add 'langclaw[interpreter]'"
        ) from exc

    ptc = resolve_ptc_allowlist(
        available_tools,
        interpreter_config=icfg,
        permissions_config=config.permissions,
        role=role,
        runtime_tool_names=RUNTIME_INJECTED_TOOLS,
    )

    logger.info(
        "Code interpreter enabled — PTC allowlist: {} (timeout={}s, max_ptc_calls={})",
        ptc or "(none)",
        icfg.timeout,
        icfg.max_ptc_calls,
    )

    # CodeInterpreterMiddleware is marked @beta; suppress the per-instantiation
    # warning so enabling the feature doesn't spam framework logs. The beta
    # status is documented on InterpreterConfig instead.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return CodeInterpreterMiddleware(
            timeout=icfg.timeout,
            memory_limit=icfg.memory_limit,
            max_ptc_calls=icfg.max_ptc_calls,
            max_result_chars=icfg.max_result_chars,
            snapshot_between_turns=icfg.snapshot_between_turns,
            ptc=ptc or None,
        )


__all__ = [
    "DEFAULT_READONLY_PTC_TOOLS",
    "RUNTIME_INJECTED_TOOLS",
    "build_interpreter_middleware",
    "resolve_ptc_allowlist",
]
