"""Declarative workflow plans — runtime-composed structure without code exec.

A :class:`WorkflowPlan` is a DAG of :class:`WorkflowStep` s, each delegating to
a registered named agent. The main agent emits a plan as the structured
arguments of the ``orchestrate`` tool, so it can compose a *novel* workflow
shape from chat — the langclaw analog of a generated script, but data, not code:
the interpreter only ever calls registered agents with declarative wiring, and
every step passes through the same middleware (RBAC, rate limit, …) as any
other agent turn.

``run_plan`` executes the DAG: independent steps run concurrently, dependency
outputs are threaded into later prompts via ``{step_id}`` placeholders (and the
workflow's free-text payload via ``{input}``). ``validate_plan`` is the
pre-flight check the tool runs so a malformed plan fails in the agent's turn
with a clear message rather than late at execution time.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Iterable
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    RunStep = Callable[[str, str], Awaitable[str]]


class WorkflowStep(BaseModel):
    """One node in a workflow plan.

    Attributes:
        id:         Unique identifier, referenced by other steps' ``depends_on``
                    and by ``{id}`` placeholders in their prompts.
        agent:      Registered named-agent key that runs this step.
        prompt:     Instruction for the agent. May contain ``{input}`` (the
                    workflow's payload) and ``{dep_id}`` for any declared
                    dependency, both substituted before the step runs.
        depends_on: IDs of steps that must complete first.
    """

    id: str
    agent: str = "default"
    prompt: str
    depends_on: list[str] = Field(default_factory=list)


class WorkflowPlan(BaseModel):
    """A DAG of steps composed at runtime by the orchestrating agent."""

    steps: list[WorkflowStep] = Field(default_factory=list)


def validate_plan(plan: WorkflowPlan, known_agents: Iterable[str] | None = None) -> list[str]:
    """Return a list of human-readable problems with *plan* (empty = valid).

    Checks: non-empty, unique ids, every dependency resolves, no cycles, and —
    when ``known_agents`` is provided — every step targets a registered agent.
    Passing ``known_agents=None`` skips the agent check (useful in unit tests).
    """
    errors: list[str] = []

    if not plan.steps:
        errors.append("Plan has no steps.")
        return errors

    ids = [s.id for s in plan.steps]
    seen: set[str] = set()
    duplicates = {i for i in ids if i in seen or seen.add(i)}
    for dup in sorted(duplicates):
        errors.append(f"Duplicate step id '{dup}'.")

    id_set = set(ids)
    known = set(known_agents) if known_agents is not None else None
    for step in plan.steps:
        if known is not None and step.agent not in known:
            errors.append(
                f"Step '{step.id}' targets unknown agent '{step.agent}'. "
                f"Known agents: {', '.join(sorted(known)) or '(none)'}."
            )
        for dep in step.depends_on:
            if dep not in id_set:
                errors.append(f"Step '{step.id}' depends on missing step '{dep}'.")

    # Cycle detection only makes sense once dependencies resolve.
    if not any("depends on missing" in e for e in errors) and _has_cycle(plan):
        errors.append("Plan has a dependency cycle.")

    return errors


def _has_cycle(plan: WorkflowPlan) -> bool:
    """Return True if the dependency graph contains a cycle (DFS, 3-colour)."""
    deps = {s.id: list(s.depends_on) for s in plan.steps}
    WHITE, GREY, BLACK = 0, 1, 2
    colour = {sid: WHITE for sid in deps}

    def visit(node: str) -> bool:
        colour[node] = GREY
        for nxt in deps.get(node, []):
            if nxt not in colour:
                continue
            if colour[nxt] == GREY:
                return True
            if colour[nxt] == WHITE and visit(nxt):
                return True
        colour[node] = BLACK
        return False

    return any(colour[sid] == WHITE and visit(sid) for sid in deps)


def _substitute(prompt: str, *, input: str, outputs: dict[str, str], deps: list[str]) -> str:
    """Replace ``{input}`` and each ``{dep_id}`` placeholder with its value.

    Manual replacement (not ``str.format``) so literal braces elsewhere in the
    prompt are left untouched.
    """
    result = prompt.replace("{input}", input)
    for dep in deps:
        result = result.replace(f"{{{dep}}}", outputs.get(dep, ""))
    return result


async def run_plan(
    plan: WorkflowPlan,
    *,
    run_step: RunStep,
    input: str = "",
    known_agents: Iterable[str] | None = None,
) -> dict[str, str]:
    """Execute *plan*, returning ``{step_id: output}`` for every step.

    Steps run in topological waves: each wave holds every not-yet-run step whose
    dependencies are already complete, and all steps in a wave run concurrently
    via :func:`asyncio.gather`. The plan is validated first, so an invalid plan
    raises :class:`ValueError` before any step executes.

    Args:
        plan:         The DAG to run.
        run_step:     ``async (agent, prompt) -> output`` — delegates one step.
        input:        Free-text payload substituted for ``{input}``.
        known_agents: When provided, validation rejects unknown agents.

    Raises:
        ValueError: If the plan fails validation.
    """
    errors = validate_plan(plan, known_agents)
    if errors:
        raise ValueError("Invalid workflow plan: " + "; ".join(errors))

    by_id = {s.id: s for s in plan.steps}
    outputs: dict[str, str] = {}
    pending = set(by_id)

    while pending:
        wave = [sid for sid in pending if all(d in outputs for d in by_id[sid].depends_on)]
        if not wave:  # pragma: no cover - guarded by validate_plan's cycle check
            raise ValueError("Plan stalled — unsatisfiable dependencies.")

        async def _run(sid: str) -> tuple[str, str]:
            step = by_id[sid]
            prompt = _substitute(step.prompt, input=input, outputs=outputs, deps=step.depends_on)
            return sid, await run_step(step.agent, prompt)

        for sid, output in await asyncio.gather(*(_run(s) for s in wave)):
            outputs[sid] = output
            pending.discard(sid)

    return outputs


__all__ = ["WorkflowPlan", "WorkflowStep", "run_plan", "validate_plan"]
