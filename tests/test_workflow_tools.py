"""
Tests for the agent-facing workflow tools.

These are how the agent spawns a workflow *from chat*:
  - ``run_workflow(name, input)``  — trigger a registered workflow (Flavor A)
  - ``orchestrate(goal, steps)``   — compose a novel DAG over named agents,
                                     run by the built-in ``_interpret`` workflow
                                     (Flavor B)

Both publish an ``InboundMessage`` carrying ``workflow_name`` + ``notify_agent``
(so the result calls back to the agent) and an incremented ``_depth`` (the
recursion guard). The core publish/guard logic lives in ``_spawn_workflow`` and
plan pre-validation in ``_prepare_plan`` so both are unit-testable without the
LangChain tool-runtime machinery.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from langclaw.agents.tools.workflow import (
    _prepare_plan,
    _spawn_workflow,
    build_workflow_tools,
)
from langclaw.bus.base import InboundMessage
from langclaw.context import LangclawContext
from langclaw.workflows.plan import WorkflowStep


def _ctx(metadata=None) -> LangclawContext:
    return LangclawContext(
        channel="telegram",
        user_id="u1",
        context_id="c1",
        chat_id="chat1",
        metadata=metadata or {},
    )


class TestSpawnWorkflow:
    async def test_publishes_trigger_with_notify_and_incremented_depth(self):
        bus = MagicMock()
        bus.publish = AsyncMock()

        out = await _spawn_workflow(
            bus,
            workflow_name="research",
            content="quantum computing",
            ctx=_ctx(),
            max_depth=3,
            valid_names={"research"},
        )

        assert out["status"] == "started"
        bus.publish.assert_awaited_once()
        msg = bus.publish.call_args[0][0]
        assert isinstance(msg, InboundMessage)
        assert msg.content == "quantum computing"
        assert msg.channel == "telegram"
        assert msg.metadata["workflow_name"] == "research"
        assert msg.metadata["notify_agent"] is True
        assert msg.metadata["_depth"] == 1

    async def test_unknown_workflow_errors_without_publishing(self):
        bus = MagicMock()
        bus.publish = AsyncMock()

        out = await _spawn_workflow(
            bus,
            workflow_name="ghost",
            content="x",
            ctx=_ctx(),
            max_depth=3,
            valid_names={"research"},
        )

        assert "error" in out
        assert "ghost" in out["error"]
        bus.publish.assert_not_awaited()

    async def test_depth_cap_errors_without_publishing(self):
        bus = MagicMock()
        bus.publish = AsyncMock()

        out = await _spawn_workflow(
            bus,
            workflow_name="research",
            content="x",
            ctx=_ctx({"_depth": 3}),
            max_depth=3,
            valid_names={"research"},
        )

        assert "error" in out
        bus.publish.assert_not_awaited()

    async def test_extra_metadata_is_merged(self):
        bus = MagicMock()
        bus.publish = AsyncMock()

        await _spawn_workflow(
            bus,
            workflow_name="_interpret",
            content="goal",
            ctx=_ctx(),
            max_depth=3,
            valid_names=None,  # built-in, not in the registered-name set
            extra_metadata={"plan": {"steps": []}},
        )

        msg = bus.publish.call_args[0][0]
        assert msg.metadata["workflow_name"] == "_interpret"
        assert msg.metadata["plan"] == {"steps": []}

    async def test_chat_id_falls_back_to_user_id(self):
        bus = MagicMock()
        bus.publish = AsyncMock()
        ctx = LangclawContext(channel="ws", user_id="u9", context_id="c9", chat_id="")

        await _spawn_workflow(
            bus, workflow_name="r", content="x", ctx=ctx, max_depth=3, valid_names={"r"}
        )

        msg = bus.publish.call_args[0][0]
        assert msg.chat_id == "u9"


class TestPreparePlan:
    def test_valid_plan_returns_plan_and_no_error(self):
        plan, err = _prepare_plan(
            [WorkflowStep(id="a", agent="default", prompt="x")],
            known_agents={"default"},
        )
        assert err is None
        assert plan is not None
        assert plan.steps[0].id == "a"

    def test_invalid_plan_returns_error_and_no_plan(self):
        plan, err = _prepare_plan(
            [WorkflowStep(id="a", agent="ghost", prompt="x")],
            known_agents={"default"},
        )
        assert plan is None
        assert err and "ghost" in err


class TestBuildWorkflowTools:
    def test_includes_both_tools_when_workflows_registered(self):
        tools = build_workflow_tools(
            MagicMock(), workflow_names=["research"], known_agents={"default"}
        )
        names = {t.name for t in tools}
        assert "run_workflow" in names
        assert "orchestrate" in names

    def test_omits_run_workflow_when_no_workflows_registered(self):
        tools = build_workflow_tools(MagicMock(), workflow_names=[], known_agents={"default"})
        names = {t.name for t in tools}
        assert "run_workflow" not in names
        assert "orchestrate" in names

    def test_orchestrate_description_lists_available_agents(self):
        tools = build_workflow_tools(
            MagicMock(), workflow_names=[], known_agents={"default", "researcher"}
        )
        orch = next(t for t in tools if t.name == "orchestrate")
        # The agent must know which agent names are valid, or it guesses
        # (e.g. "general-purpose") and the plan fails validation.
        assert "default" in orch.description
        assert "researcher" in orch.description
