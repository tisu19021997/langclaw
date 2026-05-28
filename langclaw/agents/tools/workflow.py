"""Workflow tools — let the agent spawn workflows from chat.

Two tools, one trigger path. Both publish an ``InboundMessage`` to the bus
(which ``GatewayManager._handle`` dispatches to the :class:`WorkflowRunner`),
stamped with:

  - ``workflow_name`` — the workflow to run (a registered name, or the built-in
    ``"_interpret"`` for a composed plan);
  - ``notify_agent`` — so the runner routes the result *back to the agent* on
    completion instead of straight to the channel;
  - ``_depth`` — incremented per spawn and capped, so an agent that spawns a
    workflow whose agents spawn workflows can't recurse without bound.

``run_workflow`` triggers a pre-registered workflow by name. ``orchestrate``
lets the agent compose a *novel* DAG (:class:`WorkflowPlan`) as structured tool
arguments — the agent itself is the planner — which the built-in ``_interpret``
workflow then executes over the registered named agents.

The publish/guard logic (``_spawn_workflow``) and plan pre-validation
(``_prepare_plan``) are separated from the tool wrappers so they unit-test
without the tool-runtime machinery; the wrappers only adapt ``runtime.context``.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING, Any

from langchain.tools import ToolRuntime
from langchain_core.tools import BaseTool, tool
from loguru import logger

from langclaw.bus.base import InboundMessage
from langclaw.context import LangclawContext
from langclaw.workflows.interpret import INTERPRET_WORKFLOW
from langclaw.workflows.plan import WorkflowPlan, WorkflowStep, validate_plan

if TYPE_CHECKING:
    from langclaw.bus.base import BaseMessageBus

DEFAULT_MAX_DEPTH = 3


async def _spawn_workflow(
    bus: BaseMessageBus,
    *,
    workflow_name: str,
    content: str,
    ctx: LangclawContext | None,
    max_depth: int,
    valid_names: Iterable[str] | None,
    extra_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Publish a workflow-trigger message, returning a result/error dict.

    Args:
        bus:            Running message bus to publish onto.
        workflow_name:  Name stamped into ``metadata["workflow_name"]``.
        content:        Free-text payload (the workflow's ``ctx.input``).
        ctx:            The tool's ``runtime.context`` (channel/session + depth).
        max_depth:      Refuse to spawn once ``_depth`` reaches this.
        valid_names:    Allowed workflow names; ``None`` skips the check (used
                        for the built-in ``_interpret``).
        extra_metadata: Extra metadata merged into the trigger (e.g. a plan).
    """
    if ctx is None or not ctx.channel or not ctx.user_id:
        return {"error": "No session context — workflows can only be spawned inside a chat."}

    depth = int((ctx.metadata or {}).get("_depth", 0))
    if depth >= max_depth:
        return {
            "error": (
                f"Workflow nesting limit ({max_depth}) reached — not spawning '{workflow_name}'. "
                "This guards against runaway recursion."
            )
        }

    if valid_names is not None and workflow_name not in set(valid_names):
        available = ", ".join(sorted(valid_names)) or "(none registered)"
        return {"error": f"Unknown workflow '{workflow_name}'. Available: {available}."}

    metadata: dict[str, Any] = {
        "workflow_name": workflow_name,
        "notify_agent": True,
        "_depth": depth + 1,
    }
    metadata.update(extra_metadata or {})

    await bus.publish(
        InboundMessage(
            channel=ctx.channel,
            user_id=ctx.user_id,
            context_id=ctx.context_id,
            chat_id=ctx.chat_id or ctx.user_id,
            content=content,
            metadata=metadata,
        )
    )
    logger.info(f"Workflow spawned by agent | name={workflow_name} depth={depth + 1}")
    return {"status": "started", "workflow": workflow_name}


def _prepare_plan(
    steps: list[WorkflowStep], known_agents: Iterable[str] | None
) -> tuple[WorkflowPlan | None, str | None]:
    """Build and validate a plan, returning ``(plan, None)`` or ``(None, error)``."""
    plan = WorkflowPlan(steps=steps)
    errors = validate_plan(plan, known_agents)
    if errors:
        return None, "Invalid plan: " + "; ".join(errors)
    return plan, None


def make_run_workflow_tool(
    bus: BaseMessageBus, workflow_names: Iterable[str], max_depth: int = DEFAULT_MAX_DEPTH
) -> BaseTool:
    """Return the ``run_workflow`` tool bound to *bus* and the registered names."""
    valid = set(workflow_names)

    async def run_workflow(
        name: str, input: str, *, runtime: ToolRuntime[LangclawContext]
    ) -> dict[str, Any]:
        """Spawn a pre-registered workflow by name.

        The workflow runs in the background; when it finishes you will receive a
        completion message (with the result, or a path to it) and should relay
        it to the user. Returns immediately with ``{"status": "started"}``.

        Args:
            name:  Registered workflow name.
            input: Free-text input passed to the workflow as its payload.
        """
        return await _spawn_workflow(
            bus,
            workflow_name=name,
            content=input,
            ctx=runtime.context,
            max_depth=max_depth,
            valid_names=valid,
        )

    return tool(run_workflow)


def make_orchestrate_tool(
    bus: BaseMessageBus, known_agents: Iterable[str], max_depth: int = DEFAULT_MAX_DEPTH
) -> BaseTool:
    """Return the ``orchestrate`` tool — compose a novel workflow on the fly."""
    known = set(known_agents)

    async def orchestrate(
        goal: str, steps: list[WorkflowStep], *, runtime: ToolRuntime[LangclawContext]
    ) -> dict[str, Any]:
        """Compose and run a multi-step workflow over the available agents.

        Use this when a request needs several delegated steps — some parallel,
        some dependent. Each step names an agent and a prompt; declare
        ``depends_on`` for ordering and reference an upstream step's output in a
        prompt with ``{step_id}`` (and the overall input with ``{input}``).
        The workflow runs in the background and calls back with the result.

        Args:
            goal:  One-line description of the overall objective (the payload).
            steps: The DAG of steps. Each: ``id``, ``agent``, ``prompt``,
                   optional ``depends_on``.
        """
        plan, error = _prepare_plan(steps, known)
        if error is not None:
            return {"error": error}
        return await _spawn_workflow(
            bus,
            workflow_name=INTERPRET_WORKFLOW,
            content=goal,
            ctx=runtime.context,
            max_depth=max_depth,
            valid_names=None,
            extra_metadata={"plan": plan.model_dump()},
        )

    return tool(orchestrate)


def build_workflow_tools(
    bus: BaseMessageBus,
    workflow_names: Iterable[str],
    known_agents: Iterable[str],
    max_depth: int = DEFAULT_MAX_DEPTH,
) -> list[BaseTool]:
    """Return the workflow tools available to the orchestrating agent.

    ``orchestrate`` is always included (it composes plans over named agents);
    ``run_workflow`` is included only when there are registered workflows to run.
    """
    tools: list[BaseTool] = [make_orchestrate_tool(bus, known_agents, max_depth)]
    names = list(workflow_names)
    if names:
        tools.insert(0, make_run_workflow_tool(bus, names, max_depth))
    return tools


__all__ = [
    "INTERPRET_WORKFLOW",
    "build_workflow_tools",
    "make_orchestrate_tool",
    "make_run_workflow_tool",
]
