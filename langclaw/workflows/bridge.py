"""
Bridge — make registered workflows invocable (issue #38, Phase 1 integration).

Two pieces turn the inert registry into something the running agent can call:

- :func:`build_toolset_executor` — the *default step executor*.  It maps each
  :class:`~langclaw.workflows.context.StepRequest` a workflow body issues onto
  the live toolset: ``ctx.tool(name, **kw)`` invokes that tool; ``ctx.subagent``
  invokes that subagent's *compiled graph directly* with the prompt as a fresh
  user message and returns its final AI text.  (It deliberately does **not** go
  through the deepagents ``task`` tool: ``task`` needs an injected ``ToolRuntime``
  — state / config / tool_call_id — that only exists inside the agent graph, so
  calling it from the out-of-graph workflow executor fails.  Passing the subagent
  runnables in sidesteps that entirely.)  This is the in-process executor; a
  future slice may publish each step to the bus so it re-enters
  ``GatewayManager._handle`` (full RBAC/rate-limit/checkpointing).
- :func:`make_workflow_tools` — one ``workflow_<name>`` LangChain tool per
  registered workflow.  The tool validates the run input against the spec's
  model, runs the workflow via :class:`~langclaw.workflows.runtime.WorkflowRuntime`,
  and returns the output — or, per the cron-tool convention, an ``"Error: ..."``
  string on failure rather than raising into the agent loop.

The executor is injected via ``executor_factory`` so the bridge is unit-testable
without a live agent: pass a factory returning a canned ``async def (req)``.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from loguru import logger
from pydantic import BaseModel, Field

from langclaw.naming import WORKFLOW_TOOL_PREFIX, workflow_tool_name
from langclaw.workflows.context import StepExecutor, StepRequest, WorkflowStepError

if TYPE_CHECKING:
    from langchain_core.tools import BaseTool

    from langclaw.config.schema import PermissionsConfig, WorkflowsConfig
    from langclaw.workflows.js_runner import ScriptAuthorFn, ScriptRunnerFn
    from langclaw.workflows.registry import WorkflowRegistry, WorkflowSpec
    from langclaw.workflows.runtime import WorkflowRuntime

ExecutorFactory = Callable[[Any], Awaitable[StepExecutor]]
#: Build the Mode-2 author / script-runner for one spec (closing over the model
#: and the live, role-filtered toolset). ``None`` → Mode 2 is unavailable and an
#: llm_authored workflow returns a clean error instead of running.
AuthorFactory = Callable[["WorkflowSpec"], "ScriptAuthorFn"]
ScriptRunnerFactory = Callable[["WorkflowSpec"], "ScriptRunnerFn"]

# The ``workflow_<name>`` tool/PTC prefix is owned by ``langclaw.naming`` (the
# single source of truth shared with the reservation guard and the permission
# middleware). Re-exported here for back-compat with existing imports.


def resolve_workflow_ptc_names(
    registry: WorkflowRegistry,
    *,
    workflows_config: WorkflowsConfig,
    permissions_config: PermissionsConfig | None = None,
    role: str | None = None,
) -> list[str]:
    """Resolve which ``workflow_<name>`` tools a script may call (Mode 1).

    Pure and side-effect free — the workflow-axis analogue of
    :func:`langclaw.interpreter.resolve_ptc_allowlist`.  Resolution:

    1. Workflows disabled → ``[]``.
    2. Otherwise every registered workflow, as ``workflow_<name>``.
    3. When ``role`` + an enabled ``permissions_config`` are given, narrow to the
       role's :func:`~langclaw.middleware.permissions.allowed_workflow_names`
       (**default-deny**) — so a script can never reach a workflow the role lacks
       and the PTC surface cannot drift from the live ``workflow_<name>`` tool
       gate (both resolve through :func:`langclaw.rbac.resolve_capability`).

    Returns:
        Sorted ``workflow_<name>`` tool names to merge into the interpreter's
        PTC allowlist.
    """
    if not workflows_config.enabled:
        return []

    names = registry.names()
    if role is not None and permissions_config is not None and permissions_config.enabled:
        from langclaw.middleware.permissions import allowed_workflow_names

        permitted = allowed_workflow_names(permissions_config, role, names)
        names = [n for n in names if n in permitted]

    return sorted(f"{WORKFLOW_TOOL_PREFIX}{n}" for n in names)


class _WorkflowToolArgs(BaseModel):
    """Argument schema shared by every ``workflow_<name>`` tool.

    Defined once at module scope (not per tool build) so repeated agent
    construction does not register many same-named pydantic models.
    """

    workflow_input: Any = Field(
        default=None,
        description="Input object for the workflow (validated against its schema).",
    )


def build_toolset_executor(
    available_tools: list[Any],
    *,
    subagent_runnables: dict[str, Any] | None = None,
) -> StepExecutor:
    """Return a :class:`StepExecutor` backed by a live toolset.

    Args:
        available_tools: The tools (objects with ``.name`` and ``.ainvoke``)
            the workflow's steps may reach.  Typically the same role-filtered
            toolset the agent itself was built with.
        subagent_runnables: ``{subagent_type: compiled graph}`` the workflow may
            delegate to via ``ctx.subagent``.  Each graph is invoked directly
            (``ainvoke({"messages": [HumanMessage(prompt)]})``) — not via the
            ``task`` tool — so it works from outside the agent graph.  ``None`` ⇒
            no subagent is reachable and ``ctx.subagent`` raises a clear error.

    Returns:
        An async ``(StepRequest) -> result`` callable:

        - ``kind == "tool"``  → ``tool.ainvoke(payload_dict)``.
        - ``kind == "subagent"`` → invoke ``subagent_runnables[target]`` with the
          payload as a user message; return its final AI text.

    Raises (inside the returned callable):
        WorkflowStepError: when a referenced tool is absent, a subagent is not
            registered, or a named-``agent`` step is requested (unsupported).
    """
    by_name: dict[str, Any] = {}
    for t in available_tools:
        name = getattr(t, "name", None)
        if name:
            by_name[name] = t
    runnables: dict[str, Any] = subagent_runnables or {}

    async def _executor(request: StepRequest) -> Any:
        if request.kind == "tool":
            tool = by_name.get(request.target)
            if tool is None:
                raise WorkflowStepError(
                    f"Workflow step referenced tool {request.target!r} which is "
                    "not available to this run."
                )
            args = request.payload if isinstance(request.payload, dict) else {}
            return await tool.ainvoke(args)

        if request.kind == "subagent":
            runnable = runnables.get(request.target)
            if runnable is None:
                available = ", ".join(sorted(runnables)) or "none"
                raise WorkflowStepError(
                    f"Workflow step requested subagent {request.target!r}, which is not "
                    f"a registered subagent (available: {available}). Register it with "
                    "app.subagent(...)."
                )
            from langchain_core.messages import HumanMessage

            result = await runnable.ainvoke(
                {"messages": [HumanMessage(content=str(request.payload))]}
            )
            return _subagent_reply_text(result)

        if request.kind == "agent":
            raise WorkflowStepError(
                "ctx.agent (delegating to a named agent) is not supported from a "
                "workflow yet; use ctx.subagent(<type>, ...) to delegate to a subagent."
            )

        raise WorkflowStepError(f"Unknown workflow step kind: {request.kind!r}")

    return _executor


def _subagent_reply_text(result: Any) -> str:
    """Extract a subagent graph's final reply: the last non-empty AI message text.

    Mirrors how deepagents' ``task`` tool reduces a subagent result — walk back to
    the last :class:`AIMessage` with text (a trailing empty ``end_turn`` message is
    skipped).  Returns ``""`` when the subagent produced no text.
    """
    from langchain_core.messages import AIMessage

    messages = result.get("messages", []) if isinstance(result, dict) else []
    for msg in reversed(messages):
        if not isinstance(msg, AIMessage):
            continue
        content = msg.content
        if isinstance(content, str):
            if content.strip():
                return content.strip()
            continue
        if isinstance(content, list):  # content blocks → join the text parts
            parts = [
                b.get("text", "")
                for b in content
                if isinstance(b, dict) and b.get("type") == "text"
            ]
            text = " ".join(parts).strip()
            if text:
                return text
    return ""


def make_workflow_tools(
    registry: WorkflowRegistry,
    runtime: WorkflowRuntime,
    *,
    executor_factory: ExecutorFactory,
    author_factory: AuthorFactory | None = None,
    script_runner_factory: ScriptRunnerFactory | None = None,
) -> list[BaseTool]:
    """Build one ``workflow_<name>`` LangChain tool per registered workflow.

    Each tool accepts a single ``workflow_input`` argument (validated against the
    workflow's input model by the runtime), runs the workflow, and returns its
    output.  Failures are returned as ``"Error: ..."`` strings rather than raised
    — matching the cron tool's convention so a broken workflow never breaks the
    agent loop.

    Args:
        registry:         The populated :class:`WorkflowRegistry`.
        runtime:          The :class:`WorkflowRuntime` driving runs.
        executor_factory: Async ``(tool_runtime) -> StepExecutor`` producing the
                          step executor for one run (``mode="python"``).  Injected
                          so the bridge is testable; production wiring passes a
                          factory closing over the live, role-filtered toolset.
        author_factory:        Optional ``(spec) -> author`` for ``mode="llm_authored"``
                          (Mode 2). Required to run llm_authored workflows.
        script_runner_factory: Optional ``(spec) -> script_runner`` executing an
                          authored body (Mode 2). Required alongside *author_factory*.

    Returns:
        A list of ``BaseTool`` instances, one per registered workflow.
    """
    from langchain_core.tools import StructuredTool

    tools: list[BaseTool] = []
    for spec in registry.specs():
        tools.append(
            _make_one_workflow_tool(
                spec,
                runtime,
                executor_factory,
                StructuredTool,
                author_factory=author_factory,
                script_runner_factory=script_runner_factory,
            )
        )
    return tools


def _make_one_workflow_tool(
    spec: WorkflowSpec,
    runtime: WorkflowRuntime,
    executor_factory: ExecutorFactory,
    structured_tool_cls: type[BaseTool],
    *,
    author_factory: AuthorFactory | None = None,
    script_runner_factory: ScriptRunnerFactory | None = None,
) -> BaseTool:
    description = spec.description or f"Run the {spec.name!r} workflow."

    async def _run(workflow_input: Any = None) -> str:
        run_id = f"{spec.name}:{uuid.uuid4().hex[:12]}"
        try:
            if spec.mode == "saved":
                if script_runner_factory is None:
                    raise WorkflowStepError(
                        f"workflow {spec.name!r} is mode='saved' but the script "
                        "runner is not wired (needs the interpreter extra)."
                    )
                output = await runtime.start_run(
                    spec,
                    workflow_input,
                    run_id=run_id,
                    script_runner=script_runner_factory(spec),
                )
            elif spec.mode == "llm_authored":
                if author_factory is None or script_runner_factory is None:
                    raise WorkflowStepError(
                        f"workflow {spec.name!r} is mode='llm_authored' but Mode 2 "
                        "is not wired (needs a model + interpreter extra)."
                    )
                output = await runtime.start_run(
                    spec,
                    workflow_input,
                    run_id=run_id,
                    author=author_factory(spec),
                    script_runner=script_runner_factory(spec),
                )
            else:
                executor = await executor_factory(None)
                output = await runtime.start_run(
                    spec, workflow_input, run_id=run_id, executor=executor
                )
            return _stringify(output)
        except Exception as exc:  # noqa: BLE001 — surfaced to the agent as text
            logger.warning(f"Workflow {spec.name!r} run {run_id} failed: {exc}")
            return f"Error: workflow {spec.name!r} failed: {exc}"

    return structured_tool_cls.from_function(
        coroutine=_run,
        name=workflow_tool_name(spec.name),
        description=description,
        args_schema=_WorkflowToolArgs,
    )


def workflow_system_prompt(registry: WorkflowRegistry, *, authoring: bool = False) -> str:
    """System-prompt nudge that makes workflows discoverable to the agent.

    The workflow-axis analogue of
    :func:`langclaw.interpreter.interpreter_system_prompt`'s ``<code_interpreter>``
    block: without it the ``workflow_<name>`` tools are present but unexplained, so
    the model rarely reaches for them. Lists each registered workflow (tool name +
    mode + description).

    Args:
        registry:  The populated :class:`WorkflowRegistry`.
        authoring: When ``True`` the agent can author workflows (by writing a
                   ``workflows/<name>.js`` file), so the nudge teaches the run →
                   save loop ("run an ``eval`` program, then save it as a file")
                   and drops the "cannot create" contract. When ``False`` the
                   agent can only run pre-registered workflows.

    Returns:
        The ``<workflows>`` block, or ``""`` when there is nothing to say
        (no registered workflows and authoring off).
    """
    specs = registry.specs()
    if not specs and not authoring:
        return ""

    lines = []
    for s in specs:
        mode = "" if getattr(s, "mode", "python") == "python" else f" [{s.mode}]"
        desc = f" — {s.description}" if s.description else ""
        lines.append(f"  - {workflow_tool_name(s.name)}{mode}{desc}")
    listing = "\n".join(lines) if lines else "  (none registered yet)"

    intro = (
        "Workflows are durable, typed, multi-step orchestrations exposed as "
        "`workflow_<name>` tools. When a request matches one, run that tool instead "
        "of improvising the same steps yourself — it is budgeted and resumable, so "
        "more reliable than an ad-hoc sequence."
    )
    if authoring:
        authoring_block = (
            "\nYou can also CREATE a workflow at runtime by writing a file — there is no "
            "special tool, just use `write_file`. When the user asks to save, remember, "
            "or 'turn into a workflow' a multi-step task you just ran via `eval`, write "
            "the SAME JavaScript program to `workflows/<name>.js` in your workspace. "
            "Rules:\n"
            "  - `<name>` must be snake_case — letters, digits, and underscores only "
            "(e.g. `hn_ai_digest`), no hyphens or spaces. It becomes the "
            "`workflow_<name>` tool.\n"
            "  - Start the file with metadata comments: `// @description <one line>` and, "
            "if it calls tools, `// @uses tool_a, tool_b`. `@uses` names langclaw tools "
            "only (web_search, web_fetch, cron, …) — NOT the backend file tools "
            "(read_file, write_file, ls, glob, grep, edit_file), which a workflow can't "
            "reach; use web_fetch to read a URL or file.\n"
            "  - The body is the same sandboxed JS as `eval`: it receives the run input "
            "as the global `inp` and must emit its result with "
            "`await tools.output({ result: <value> })`.\n"
            "  - Optional progress: `await tools.phase({ name: 'gather' })` and "
            "`await tools.log({ message: '3/10 done' })` narrate to the user during long "
            "runs (safe no-ops otherwise) — the same phases a code workflow shows.\n"
            "Once written, it is loaded automatically and becomes a `workflow_<name>` "
            "tool you can run later (and after a restart). Use this for repeatable jobs; "
            "for a one-off, just run `eval` and don't save."
        )
    else:
        authoring_block = (
            "\nYou can run the workflows below but cannot create or modify them (that is "
            "done in code by the developer). For ad-hoc control flow that no workflow "
            "covers, use the `eval` interpreter if available."
        )
    return f"<workflows>\n{intro}{authoring_block}\nAvailable workflows:\n{listing}\n</workflows>"


def _stringify(value: Any) -> str:
    if isinstance(value, str):
        return value
    if hasattr(value, "model_dump"):
        value = value.model_dump()
    try:
        return json.dumps(value, default=str)
    except (TypeError, ValueError):
        return str(value)
