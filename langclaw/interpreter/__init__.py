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

import warnings
from typing import TYPE_CHECKING, Any

from loguru import logger

from langclaw.middleware.permissions import allowed_subagents, allowed_tool_names
from langclaw.naming import reject_camel_collisions, to_camel_case

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

# The snake→camel tool-surface mapping and its collision guard are owned by
# langclaw.naming (the dependency-free naming seam) so the interpreter's PTC
# allowlist and the workflow @uses allowlist compute them identically.
_to_camel_case = to_camel_case
_reject_ptc_name_collisions = reject_camel_collisions


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
       surface is a subset of the role's permitted tools.  As part of this
       step the ``task`` (subagent-delegation) tool is dropped from the script
       surface when the role may use *zero* subagents — PTC calls bypass the
       ``ToolNode``, so this coarse gate is the script-side counterpart to the
       per-type ``task`` gate enforced for model-invoked calls by
       :func:`~langclaw.middleware.permissions.build_subagent_permission_middleware`.
       Both read :func:`~langclaw.middleware.permissions.allowed_subagents` so
       they cannot drift (finer per-type PTC gating is tracked in #37).
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
        # Coarse subagent gate: PTC `tools.task(...)` bypasses the ToolNode, so
        # drop `task` entirely when the role may reach no subagents at all.
        if "task" in surface and not allowed_subagents(permissions_config, role):
            surface = surface - {"task"}

    _reject_ptc_name_collisions(surface)
    return sorted(surface)


def build_interpreter_middleware(
    config: LangclawConfig,
    available_tools: Sequence[Any],
    *,
    role: str | None = None,
    extra_ptc_tool_names: Iterable[str] = (),
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

    # Mode 1: registered workflows are exposed to scripts as
    # ``tools.workflow<Name>``. Their names are pre-gated by the workflow RBAC
    # axis (build_workflow_permission_middleware), and the interpreter recomputes
    # its surface from the live ``request.tools`` each call, so a role-stripped
    # workflow tool is also unreachable from PTC. Union here so the build-time
    # allowlist advertises them; reject any camelCase collision as usual.
    extra = [n for n in extra_ptc_tool_names if n]
    if extra:
        merged = sorted(set(ptc) | set(extra))
        _reject_ptc_name_collisions(merged)
        ptc = merged

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


def ptc_camel_names(
    config: LangclawConfig,
    available_tools: Sequence[Any],
    *,
    role: str | None = None,
) -> list[str]:
    """The camelCased tool names a script may call as ``tools.<name>(...)``.

    PTC exposes each tool under ``tools`` in camelCase, so this is what the
    model must actually write — ``read_file`` is reachable only as
    ``tools.readFile``. Resolves the live allowlist, then camelCases it.
    """
    names = resolve_ptc_allowlist(
        available_tools,
        interpreter_config=config.interpreter,
        permissions_config=config.permissions,
        role=role,
        runtime_tool_names=RUNTIME_INJECTED_TOOLS,
    )
    return sorted(_to_camel_case(n) for n in names)


# Curated contracts for the deepagents-injected runtime tools, which are not in
# the build-time toolset so we cannot introspect their schemas. Bounded (the
# injected set is fixed) and honest: a ``returns`` note is given ONLY for shapes
# we have confirmed; unknowns get none and the model is told to console.log-probe.
RUNTIME_TOOL_CONTRACTS: dict[str, dict[str, Any]] = {
    "task": {
        "args": ["subagent_type", "description"],
        "returns": "a string (the subagent's reply)",
    },
    "read_file": {
        "args": ["file_path"],
        "returns": (
            "a line-numbered string (`<n>\\t<line>` per line), NOT an object — split "
            "on newlines and strip the leading `<n>\\t`"
        ),
    },
    "ls": {"args": ["path"]},
    "glob": {"args": ["pattern"]},
    "grep": {"args": ["pattern"]},
}

# Bounded set of interpreter-ABI rules — properties of the *runtime*, not of any
# one tool, so they are stated once and never grow as the toolset changes.
_RUNTIME_ABI_RULES = (
    "Runtime rules (the sandbox ABI — these never change):\n"
    "- This is a bare QuickJS sandbox with **no ambient host APIs**: there is no "
    "`fetch`, `XMLHttpRequest`, `require`, `import`, `process`, `Buffer`, timers, or "
    "any Node/browser global. The ONLY way to reach the network, filesystem, or "
    "subagents is the `tools.*` functions listed below — e.g. use "
    "`await tools.webFetch({ urls: [...] })`, never `fetch(...)`.\n"
    "- Tools live on `tools` in **camelCase** (`read_file` → `tools.readFile`). Any "
    "other spelling — including snake_case — throws `TypeError: not a function`.\n"
    "- Each `eval` runs in a **fresh context**: variables do NOT persist between "
    "separate `eval` calls. Do the whole job in one script.\n"
    "- The script's result must be **JSON-serializable** (object / array / string / "
    "number / boolean / null). Ending on a Promise (e.g. `run();` where `run` is "
    "async), a function, or a Map/Set returns `[unmarshalable value]` — `await` it "
    "and end on a plain value, or `JSON.stringify(...)` it.\n"
    "- If unsure of a tool's argument or result shape, `console.log` it once first "
    "(stdout is always returned to you, even when the value itself is not)."
)


def tool_signatures(
    config: LangclawConfig,
    available_tools: Sequence[Any],
    *,
    role: str | None = None,
) -> list[str]:
    """Render each callable PTC tool as ``tools.camelName({args}) → returns …``.

    Generated from data: argument keys come from the build-time tool's own input
    schema where available, else from the curated runtime-tool contracts; a
    ``returns`` note is shown only when known. New ``@app.tool()``s self-document
    here with no prose changes, so the reference can't drift from reality.
    """
    names = resolve_ptc_allowlist(
        available_tools,
        interpreter_config=config.interpreter,
        permissions_config=config.permissions,
        role=role,
        runtime_tool_names=RUNTIME_INJECTED_TOOLS,
    )
    by_name = {getattr(t, "name", None): t for t in available_tools}
    sigs: list[str] = []
    for name in names:
        tool = by_name.get(name)
        contract = RUNTIME_TOOL_CONTRACTS.get(name, {})
        if tool is not None and getattr(tool, "args", None):
            arg_names = list(tool.args)
        else:
            arg_names = contract.get("args", [])
        arg_str = "{ " + ", ".join(arg_names) + " }" if arg_names else ""
        line = f"tools.{_to_camel_case(name)}({arg_str})"
        if returns := contract.get("returns"):
            line += f" → returns {returns}"
        sigs.append(line)
    return sorted(sigs)


def interpreter_system_prompt(
    config: LangclawConfig,
    available_tools: Sequence[Any],
    *,
    role: str | None = None,
) -> str:
    """System-prompt nudge for the ``eval`` interpreter.

    Two parts, both correct by construction: a data-generated reference of the
    exact callable tool signatures (so the model never guesses names/args), and
    the bounded set of runtime-ABI rules (camelCase, fresh context, serializable
    result, probe-if-unsure) that subsume the per-footgun lore.
    """
    sigs = tool_signatures(config, available_tools, role=role)
    tool_block = "\n".join(f"  - {s}" for s in sigs) or "  (none available)"
    return (
        "<code_interpreter>\n"
        "Run a sandboxed JavaScript program via the `eval` tool, but only when real "
        "control flow is required — looping until a condition holds, retrying a "
        "failed step, fanning out over a list whose size you learn at runtime, or "
        "branching on an intermediate result. A request to 'run a workflow to …', "
        "'orchestrate …', or otherwise carry out an ad-hoc multi-step job that no "
        "registered `workflow_<name>` tool covers maps to this: write it as one "
        "`eval` program. Keep large intermediate data in script variables and return "
        "only a compact result. For a single delegation use one `tools.task` call; "
        "for a trivial request, answer directly.\n\n"
        f"{_RUNTIME_ABI_RULES}\n\n"
        "Callable tools (camelCase, with argument keys and known return shapes):\n"
        f"{tool_block}\n"
        "</code_interpreter>"
    )


__all__ = [
    "DEFAULT_READONLY_PTC_TOOLS",
    "RUNTIME_INJECTED_TOOLS",
    "build_interpreter_middleware",
    "interpreter_system_prompt",
    "ptc_camel_names",
    "resolve_ptc_allowlist",
    "tool_signatures",
]
