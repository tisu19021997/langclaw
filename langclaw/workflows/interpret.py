"""The built-in ``_interpret`` workflow — runs an agent-composed plan.

When the agent calls the ``orchestrate`` tool, the plan it composed rides in
``metadata["plan"]`` and the trigger targets this workflow. ``interpret_workflow``
deserialises the :class:`WorkflowPlan`, executes it over the registered named
agents via :func:`run_plan` (delegating each step to ``ctx.run``), and returns
the output of the plan's terminal step(s) as the final result.

This is the seam that turns a *declarative* plan into real work: the structure
is data the agent produced at runtime, but every step is an ordinary named-agent
turn — no code execution, full middleware coverage.
"""

from __future__ import annotations

from loguru import logger

from langclaw.workflows.context import WorkflowContext
from langclaw.workflows.plan import WorkflowPlan, run_plan

# Canonical name of the built-in interpreter workflow (single source of truth;
# the orchestrate tool imports it from here).
INTERPRET_WORKFLOW = "_interpret"


async def interpret_workflow(ctx: WorkflowContext) -> str:
    """Execute the :class:`WorkflowPlan` carried in ``ctx.metadata['plan']``.

    Returns the terminal step's output (or, when the DAG has several leaves,
    each leaf's output under a heading). Returns a short notice when no plan is
    present.
    """
    plan_data = (ctx.metadata or {}).get("plan")
    if not plan_data:
        return "No plan was provided to interpret."

    plan = WorkflowPlan.model_validate(plan_data)
    total = len(plan.steps)
    logger.info(f"interpret plan | {total} step(s)")
    await ctx.phase("Orchestrate")

    done = 0

    async def run_step(agent: str, prompt: str) -> str:
        nonlocal done
        result = await ctx.run(prompt, agent=agent)
        done += 1
        await ctx.log(f"✓ {done}/{total} steps complete")
        return result

    # The orchestrate tool already validated the plan against the live agent
    # map, so skip re-validation here (unknown agents fall back to "default").
    outputs = await run_plan(plan, run_step=run_step, input=ctx.input)
    logger.info(f"interpret done | {total} step(s) executed")

    referenced = {dep for step in plan.steps for dep in step.depends_on}
    leaves = [step.id for step in plan.steps if step.id not in referenced]
    if not leaves:  # pragma: no cover - only possible with a cycle (rejected earlier)
        leaves = [step.id for step in plan.steps]
    if len(leaves) == 1:
        return outputs[leaves[0]]
    return "\n\n".join(f"## {sid}\n{outputs[sid]}" for sid in leaves)


# Spec shape the gateway registers into the WorkflowRunner.
INTERPRET_SPEC = {
    "name": INTERPRET_WORKFLOW,
    "description": "Execute an agent-composed workflow plan",
    "fn": interpret_workflow,
}


__all__ = ["INTERPRET_SPEC", "INTERPRET_WORKFLOW", "interpret_workflow"]
