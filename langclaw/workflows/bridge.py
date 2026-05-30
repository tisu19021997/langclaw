"""
Bridge — make registered workflows invocable (issue #38, Phase 1 integration).

Two pieces turn the inert registry into something the running agent can call:

- :func:`build_toolset_executor` — the *default step executor*.  It maps each
  :class:`~langclaw.workflows.context.StepRequest` a workflow body issues onto
  the live toolset: ``ctx.tool(name, **kw)`` invokes that tool; ``ctx.subagent``
  / ``ctx.agent`` route through the deepagents ``task`` delegation tool.  This is
  the in-process executor; a future slice may publish each step to the bus so it
  re-enters ``GatewayManager._handle`` (full RBAC/rate-limit/checkpointing).
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

from langclaw.workflows.context import StepExecutor, StepRequest, WorkflowStepError

if TYPE_CHECKING:
    from langchain_core.tools import BaseTool

    from langclaw.workflows.registry import WorkflowRegistry
    from langclaw.workflows.runtime import WorkflowRuntime

ExecutorFactory = Callable[[Any], Awaitable[StepExecutor]]


class _WorkflowToolArgs(BaseModel):
    """Argument schema shared by every ``workflow_<name>`` tool.

    Defined once at module scope (not per tool build) so repeated agent
    construction does not register many same-named pydantic models.
    """

    workflow_input: Any = Field(
        default=None,
        description="Input object for the workflow (validated against its schema).",
    )


def build_toolset_executor(available_tools: list[Any]) -> StepExecutor:
    """Return a :class:`StepExecutor` backed by a live toolset.

    Args:
        available_tools: The tools (objects with ``.name`` and ``.ainvoke``)
            the workflow's steps may reach.  Typically the same role-filtered
            toolset the agent itself was built with.

    Returns:
        An async ``(StepRequest) -> result`` callable:

        - ``kind == "tool"``  → ``tool.ainvoke(payload_dict)``.
        - ``kind in {"subagent", "agent"}`` → route through the ``task`` tool as
          ``{"subagent_type": target, "description": payload}``.

    Raises (inside the returned callable):
        WorkflowStepError: when a referenced tool / the ``task`` tool is absent —
            the same tool-absence boundary the interpreter relies on.
    """
    by_name: dict[str, Any] = {}
    for t in available_tools:
        name = getattr(t, "name", None)
        if name:
            by_name[name] = t

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

        if request.kind in ("subagent", "agent"):
            task = by_name.get("task")
            if task is None:
                raise WorkflowStepError(
                    f"Workflow step requested {request.kind} {request.target!r} but "
                    "the delegation tool 'task' is not available to this run."
                )
            return await task.ainvoke(
                {"subagent_type": request.target, "description": request.payload}
            )

        raise WorkflowStepError(f"Unknown workflow step kind: {request.kind!r}")

    return _executor


def make_workflow_tools(
    registry: WorkflowRegistry,
    runtime: WorkflowRuntime,
    *,
    executor_factory: ExecutorFactory,
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
                          step executor for one run.  Injected so the bridge is
                          testable; production wiring passes a factory closing
                          over the live, role-filtered toolset.

    Returns:
        A list of ``BaseTool`` instances, one per registered workflow.
    """
    from langchain_core.tools import StructuredTool

    tools: list[BaseTool] = []
    for spec in registry.specs():
        tools.append(_make_one_workflow_tool(spec, runtime, executor_factory, StructuredTool))
    return tools


def _make_one_workflow_tool(spec, runtime, executor_factory, structured_tool_cls):
    description = spec.description or f"Run the {spec.name!r} workflow."

    async def _run(workflow_input: Any = None) -> str:
        run_id = f"{spec.name}:{uuid.uuid4().hex[:12]}"
        try:
            executor = await executor_factory(None)
            output = await runtime.start_run(spec, workflow_input, run_id=run_id, executor=executor)
            return _stringify(output)
        except Exception as exc:  # noqa: BLE001 — surfaced to the agent as text
            logger.warning(f"Workflow {spec.name!r} run {run_id} failed: {exc}")
            return f"Error: workflow {spec.name!r} failed: {exc}"

    return structured_tool_cls.from_function(
        coroutine=_run,
        name=f"workflow_{spec.name}",
        description=description,
        args_schema=_WorkflowToolArgs,
    )


def _stringify(value: Any) -> str:
    if isinstance(value, str):
        return value
    if hasattr(value, "model_dump"):
        value = value.model_dump()
    try:
        return json.dumps(value, default=str)
    except (TypeError, ValueError):
        return str(value)
