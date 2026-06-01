"""
Central namespace reservations for framework-generated names.

Several langclaw primitives generate names into namespaces that are otherwise
populated by developer registrations:

- **Workflows** generate a LangChain tool named ``workflow_<name>`` (one per
  ``@app.workflow``), sharing the flat *tool* namespace with ``@app.tool``.
- **Built-in commands** (``/start``, ``/help``, ``/agent``, ``/workflow`` …)
  share the *command* namespace with ``@app.command``.

Left unguarded, a developer tool named ``workflow_x`` silently collides with a
workflow's generated tool, and a developer command named ``workflow`` is silently
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
        "workflow",
    }
)


def workflow_tool_name(workflow_name: str) -> str:
    """Return the LangChain tool name a workflow is exposed under."""
    return f"{WORKFLOW_TOOL_PREFIX}{workflow_name}"


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
    "reserved_prefix_owner",
    "workflow_tool_name",
]
