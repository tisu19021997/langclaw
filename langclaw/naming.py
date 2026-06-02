"""
Central namespace reservations for framework-generated names.

Several langclaw primitives generate names into namespaces that are otherwise
populated by developer registrations:

- **Workflows** generate a LangChain tool named ``workflow_<name>`` (one per
  ``@app.workflow``), sharing the flat *tool* namespace with ``@app.tool``.
- **Built-in commands** (``/start``, ``/help``, ``/agent``, ``/workflows`` …)
  share the *command* namespace with ``@app.command``.

Left unguarded, a developer tool named ``workflow_x`` silently collides with a
workflow's generated tool, and a developer command named ``workflows`` is silently
shadowed by the built-in. To keep these namespaces collision-free *as new
primitives are added*, every reservation lives here — one declaration per
primitive — and registration sites call the ``check_*`` guards.

This module imports nothing from langclaw (pure constants + functions), so any
layer (app, gateway, workflows, middleware) can depend on it without cycles.

Scaling rule: a new primitive that mints names declares its prefix in
:data:`RESERVED_TOOL_PREFIXES` (or its command in :data:`RESERVED_COMMAND_NAMES`)
and gets enforcement everywhere for free.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

#: Tool-name prefix the Workflow primitive owns: ``@app.workflow("x")`` →
#: tool ``workflow_x``. Single source of truth — the bridge, the permission
#: middleware, and the reservation guard all read it from here.
WORKFLOW_TOOL_PREFIX = "workflow_"

#: Tool-name prefixes reserved for framework-generated tools, mapping each
#: ``prefix`` → the human label of the owning primitive (used in error text).
#: A developer-registered tool may not start with any of these.
RESERVED_TOOL_PREFIXES: dict[str, str] = {
    WORKFLOW_TOOL_PREFIX: "workflow",
}

#: Command names the framework owns (built-ins + primitive control surfaces).
#: A developer ``@app.command`` may not claim one of these — it would be silently
#: shadowed by the built-in handler otherwise. Names registered conditionally
#: (``cron``/``agent``/``workflow`` depend on config) are reserved unconditionally
#: so enabling a feature can never retroactively shadow a developer command.
RESERVED_COMMAND_NAMES: frozenset[str] = frozenset(
    {
        "start",
        "reset",
        "help",
        "cron",
        "agent",
        "agentsmd",
        "logs",
        "file",
        "workflows",
    }
)


def workflow_tool_name(workflow_name: str) -> str:
    """Return the LangChain tool name a workflow is exposed under."""
    return f"{WORKFLOW_TOOL_PREFIX}{workflow_name}"


# --- camelCase tool surface (the JS/PTC namespace) --------------------------
#
# A tool's registered ``.name`` is snake_case (``web_fetch``), but inside the
# QuickJS sandbox the code interpreter (``eval``) and saved/authored workflows
# reach it as ``tools.<camelCase>`` (``tools.webFetch``). That snake→camel
# mapping is the boundary between the Python tool registry and the JS surface,
# and it MUST be computed the same way everywhere a name crosses it — otherwise
# one site (e.g. a workflow's ``@uses`` allowlist) can disagree with what PTC
# actually installs and silently drop a tool. So the mapping lives here, once.

_CAMEL_SEP = re.compile(r"[-_]+(\w)")


def to_camel_case(name: str) -> str:
    """``snake_case`` / ``kebab-case`` → ``camelCase`` — the canonical JS tool name.

    Prefers ``langchain_quickjs``'s own implementation when the extra is installed,
    so the result always matches the identifier PTC actually exposes in the
    sandbox; falls back to a local regex so this dependency-free module stays
    importable without the ``interpreter`` extra.
    """
    try:
        from langchain_quickjs import _ptc

        return _ptc.to_camel_case(name)
    except Exception:
        return _CAMEL_SEP.sub(lambda m: m.group(1).upper(), name)


def reject_camel_collisions(names: Iterable[str]) -> None:
    """Raise ``ValueError`` if two distinct names camelCase to the same identifier.

    Inside a script tools are reached as ``tools.<camelCaseName>``; if two tool
    names collapse to the same identifier one silently shadows the other, so we
    fail loudly at resolution time instead. Shared by the interpreter PTC
    allowlist and the workflow ``@uses`` allowlist.
    """
    by_camel: dict[str, list[str]] = {}
    for name in names:
        by_camel.setdefault(to_camel_case(name), []).append(name)
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


def reserved_prefix_owner(tool_name: str) -> str | None:
    """Return the owning-primitive label if *tool_name* claims a reserved prefix.

    Returns ``None`` when the name is free for developer use.
    """
    for prefix, owner in RESERVED_TOOL_PREFIXES.items():
        if tool_name.startswith(prefix):
            return owner
    return None


def check_tool_name_allowed(tool_name: str) -> None:
    """Raise ``ValueError`` if *tool_name* claims a framework-reserved prefix.

    Called when a developer registers a tool (``@app.tool`` / ``register_tool``)
    so the reserved namespace cannot be silently overwritten.
    """
    owner = reserved_prefix_owner(tool_name)
    if owner is not None:
        prefix = next(p for p, o in RESERVED_TOOL_PREFIXES.items() if o == owner)
        raise ValueError(
            f"Tool name {tool_name!r} uses the reserved {prefix!r} prefix, which "
            f"the langclaw {owner} primitive owns (it generates {prefix}<name> "
            "tools). Choose a name without that prefix."
        )


def check_command_name_allowed(command_name: str) -> None:
    """Raise ``ValueError`` if *command_name* is a framework-reserved command."""
    if command_name in RESERVED_COMMAND_NAMES:
        raise ValueError(
            f"Command name {command_name!r} is reserved by langclaw. "
            f"Reserved: {', '.join(sorted(RESERVED_COMMAND_NAMES))}."
        )


__all__ = [
    "RESERVED_COMMAND_NAMES",
    "RESERVED_TOOL_PREFIXES",
    "WORKFLOW_TOOL_PREFIX",
    "check_command_name_allowed",
    "check_tool_name_allowed",
    "reject_camel_collisions",
    "reserved_prefix_owner",
    "to_camel_case",
    "workflow_tool_name",
]
