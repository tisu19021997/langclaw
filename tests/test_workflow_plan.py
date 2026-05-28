"""
Tests for the declarative workflow plan — the langclaw-native way an agent
composes a *novel* workflow shape at runtime without code execution.

The main agent emits a :class:`WorkflowPlan` (a DAG of steps, each delegating
to a registered named agent) as structured tool arguments. ``run_plan``
interprets it: topologically ordered, independent steps run concurrently, and
each step's prompt may reference ``{input}`` or a dependency's output via
``{step_id}``.

``validate_plan`` is the pre-flight check the ``orchestrate`` tool runs so the
agent gets immediate feedback on a malformed plan (unknown agent, dangling
dependency, cycle) instead of a late runtime failure.
"""

from __future__ import annotations

import pytest

from langclaw.workflows.plan import WorkflowPlan, WorkflowStep, run_plan, validate_plan


def _recording_run_step():
    """Return ``(run_step, calls)`` where run_step echoes its prompt."""
    calls: list[tuple[str, str]] = []

    async def run_step(agent: str, prompt: str) -> str:
        calls.append((agent, prompt))
        return f"out:{prompt}"

    return run_step, calls


# ---------------------------------------------------------------------------
# validate_plan
# ---------------------------------------------------------------------------


class TestValidatePlan:
    def test_valid_plan_has_no_errors(self):
        plan = WorkflowPlan(
            steps=[
                WorkflowStep(id="a", agent="default", prompt="do {input}"),
                WorkflowStep(id="b", agent="writer", prompt="use {a}", depends_on=["a"]),
            ]
        )
        assert validate_plan(plan, known_agents={"default", "writer"}) == []

    def test_unknown_agent_is_reported(self):
        plan = WorkflowPlan(steps=[WorkflowStep(id="a", agent="ghost", prompt="x")])
        errors = validate_plan(plan, known_agents={"default"})
        assert any("ghost" in e for e in errors)

    def test_dangling_dependency_is_reported(self):
        plan = WorkflowPlan(
            steps=[WorkflowStep(id="a", agent="default", prompt="x", depends_on=["missing"])]
        )
        errors = validate_plan(plan, known_agents={"default"})
        assert any("missing" in e for e in errors)

    def test_duplicate_step_ids_are_reported(self):
        plan = WorkflowPlan(
            steps=[
                WorkflowStep(id="a", agent="default", prompt="x"),
                WorkflowStep(id="a", agent="default", prompt="y"),
            ]
        )
        errors = validate_plan(plan, known_agents={"default"})
        assert any("a" in e for e in errors)

    def test_cycle_is_reported(self):
        plan = WorkflowPlan(
            steps=[
                WorkflowStep(id="a", agent="default", prompt="x", depends_on=["b"]),
                WorkflowStep(id="b", agent="default", prompt="y", depends_on=["a"]),
            ]
        )
        errors = validate_plan(plan, known_agents={"default"})
        assert any("cycle" in e.lower() for e in errors)

    def test_empty_plan_is_reported(self):
        plan = WorkflowPlan(steps=[])
        assert validate_plan(plan) != []

    def test_known_agents_none_skips_agent_check(self):
        plan = WorkflowPlan(steps=[WorkflowStep(id="a", agent="anything", prompt="x")])
        assert validate_plan(plan, known_agents=None) == []


# ---------------------------------------------------------------------------
# run_plan
# ---------------------------------------------------------------------------


class TestRunPlan:
    async def test_single_step_substitutes_input(self):
        run_step, calls = _recording_run_step()
        plan = WorkflowPlan(steps=[WorkflowStep(id="s1", agent="default", prompt="do {input}")])

        results = await run_plan(plan, run_step=run_step, input="the thing")

        assert calls == [("default", "do the thing")]
        assert results == {"s1": "out:do the thing"}

    async def test_dependency_output_threaded_into_prompt(self):
        run_step, _ = _recording_run_step()
        plan = WorkflowPlan(
            steps=[
                WorkflowStep(id="fetch", agent="default", prompt="fetch"),
                WorkflowStep(
                    id="sum", agent="writer", prompt="summarize: {fetch}", depends_on=["fetch"]
                ),
            ]
        )

        results = await run_plan(plan, run_step=run_step)

        assert results["fetch"] == "out:fetch"
        # {fetch} was replaced by fetch's output before the second step ran.
        assert results["sum"] == "out:summarize: out:fetch"

    async def test_independent_steps_all_execute(self):
        run_step, calls = _recording_run_step()
        plan = WorkflowPlan(
            steps=[
                WorkflowStep(id="a", agent="default", prompt="A"),
                WorkflowStep(id="b", agent="default", prompt="B"),
                WorkflowStep(id="c", agent="default", prompt="C"),
            ]
        )

        results = await run_plan(plan, run_step=run_step)

        assert set(results) == {"a", "b", "c"}
        assert {p for _agent, p in calls} == {"A", "B", "C"}

    async def test_diamond_dependency_order(self):
        """a → (b, c) → d: d must see both b and c outputs."""
        run_step, _ = _recording_run_step()
        plan = WorkflowPlan(
            steps=[
                WorkflowStep(id="a", agent="default", prompt="root"),
                WorkflowStep(id="b", agent="default", prompt="from {a} left", depends_on=["a"]),
                WorkflowStep(id="c", agent="default", prompt="from {a} right", depends_on=["a"]),
                WorkflowStep(
                    id="d", agent="default", prompt="join {b} + {c}", depends_on=["b", "c"]
                ),
            ]
        )

        results = await run_plan(plan, run_step=run_step)

        assert results["d"] == "out:join out:from out:root left + out:from out:root right"

    async def test_invalid_plan_raises_before_running_any_step(self):
        run_step, calls = _recording_run_step()
        plan = WorkflowPlan(steps=[WorkflowStep(id="a", agent="ghost", prompt="x")])

        with pytest.raises(ValueError):
            await run_plan(plan, run_step=run_step, known_agents={"default"})

        assert calls == []  # validation fails fast — nothing executed
