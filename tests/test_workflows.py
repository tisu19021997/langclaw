"""
Tests for dynamic workflows — the langclaw-native orchestration feature.

A workflow is an ``async def`` function registered with ``@app.workflow()``.
It receives a :class:`WorkflowContext` (``run`` / ``parallel`` / ``pipeline``
for composition, ``phase`` / ``log`` for progress) and is driven over the
existing bus path: ``/workflow <name>`` (or ``metadata["workflow_name"]`` from
cron) → :meth:`GatewayManager._handle` → :class:`WorkflowRunner.dispatch`.

These tests isolate three layers:
  - ``WorkflowContext`` — pure composition primitives (no gateway needed)
  - ``WorkflowRunner``   — registry + dispatch over a fake channel
  - ``GatewayManager``   — the ``/workflow`` command and ``_handle`` wiring
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from langchain_core.messages import AIMessage, HumanMessage

from langclaw.bus.base import InboundMessage, OutboundMessage
from langclaw.workflows.context import WorkflowContext
from langclaw.workflows.runner import WorkflowRunner

# ---------------------------------------------------------------------------
# Shared fakes
# ---------------------------------------------------------------------------


class FakeChannel:
    """Minimal channel that records every OutboundMessage it is sent."""

    name = "test"

    def __init__(self) -> None:
        self.sent: list[OutboundMessage] = []

    def is_enabled(self) -> bool:
        return True

    def set_command_router(self, router) -> None:  # pragma: no cover - unused
        pass

    async def send(self, msg: OutboundMessage) -> None:
        self.sent.append(msg)


def _make_ctx(run_agent=None, emit=None) -> tuple[WorkflowContext, list, list]:
    """Build a WorkflowContext with recording callbacks.

    Returns ``(ctx, emitted, agent_calls)`` where ``emitted`` collects
    ``(content, phase)`` progress tuples and ``agent_calls`` collects
    ``(agent_name, prompt)`` delegation tuples.
    """
    emitted: list[tuple[str, str]] = []
    agent_calls: list[tuple[str, str]] = []

    async def _default_emit(content: str, phase: str) -> None:
        emitted.append((content, phase))

    async def _default_run_agent(name: str, prompt: str) -> str:
        agent_calls.append((name, prompt))
        return f"reply:{prompt}"

    ctx = WorkflowContext(
        input="hi",
        metadata={},
        run_agent=run_agent or _default_run_agent,
        emit=emit or _default_emit,
    )
    return ctx, emitted, agent_calls


def _inbound(**overrides) -> InboundMessage:
    base = dict(
        channel="test",
        user_id="u1",
        context_id="c1",
        chat_id="chat1",
        content="hello",
    )
    base.update(overrides)
    return InboundMessage(**base)


# ---------------------------------------------------------------------------
# WorkflowContext — progress
# ---------------------------------------------------------------------------


class TestWorkflowContextProgress:
    async def test_phase_sets_title_and_emits_header(self):
        from langclaw.workflows.context import WORKFLOW_ICON

        ctx, emitted, _ = _make_ctx()
        await ctx.phase("Plan")
        assert ctx.phase_title == "Plan"
        assert emitted[-1] == (f"{WORKFLOW_ICON} ▸ Plan", "Plan")

    async def test_log_emits_under_current_phase(self):
        from langclaw.workflows.context import WORKFLOW_ICON

        ctx, emitted, _ = _make_ctx()
        await ctx.phase("Plan")
        await ctx.log("working on it")
        assert emitted[-1] == (f"{WORKFLOW_ICON} working on it", "Plan")

    async def test_log_without_phase_uses_empty_title(self):
        from langclaw.workflows.context import WORKFLOW_ICON

        ctx, emitted, _ = _make_ctx()
        await ctx.log("orphan")
        assert emitted[-1] == (f"{WORKFLOW_ICON} orphan", "")

    async def test_every_progress_line_is_tagged_with_the_workflow_icon(self):
        from langclaw.workflows.context import WORKFLOW_ICON

        ctx, emitted, _ = _make_ctx()
        await ctx.phase("Plan")
        await ctx.log("note")
        await ctx.run("do it", agent="planner")
        # phase + log + run → every emitted line carries the workflow marker.
        assert emitted, "expected progress emissions"
        assert all(content.startswith(WORKFLOW_ICON) for content, _phase in emitted)


# ---------------------------------------------------------------------------
# WorkflowContext — delegation
# ---------------------------------------------------------------------------


class TestWorkflowContextRun:
    async def test_run_uses_default_agent_when_none(self):
        ctx, _, calls = _make_ctx()
        result = await ctx.run("do x")
        assert calls == [("default", "do x")]
        assert result == "reply:do x"

    async def test_run_uses_named_agent(self):
        ctx, _, calls = _make_ctx()
        await ctx.run("do y", agent="planner")
        assert calls[-1] == ("planner", "do y")

    async def test_run_emits_progress_before_delegating(self):
        ctx, emitted, _ = _make_ctx()
        await ctx.run("summarise the report", agent="planner")
        # A progress line naming the target agent is emitted.
        assert any("planner" in content for content, _phase in emitted)


# ---------------------------------------------------------------------------
# WorkflowContext — composition
# ---------------------------------------------------------------------------


class TestWorkflowContextComposition:
    async def test_parallel_preserves_order(self):
        ctx, _, _ = _make_ctx()

        async def make(n: int) -> int:
            return n * 10

        out = await ctx.parallel([make(1), make(2), make(3)])
        assert out == [10, 20, 30]

    async def test_pipeline_streams_each_item_through_all_stages(self):
        ctx, _, _ = _make_ctx()

        async def double(prev, _item, _i):
            return prev * 2

        async def inc(prev, _item, _i):
            return prev + 1

        out = await ctx.pipeline([1, 2, 3], double, inc)
        assert out == [3, 5, 7]

    async def test_pipeline_stage_receives_item_and_index(self):
        ctx, _, _ = _make_ctx()

        async def stage(prev, item, i):
            return (prev, item, i)

        out = await ctx.pipeline(["a", "b"], stage)
        assert out == [("a", "a", 0), ("b", "b", 1)]

    async def test_pipeline_isolates_a_failing_item(self):
        ctx, _, _ = _make_ctx()

        async def stage(prev, item, _i):
            if item == 2:
                raise ValueError("boom")
            return prev

        out = await ctx.pipeline([1, 2, 3], stage)
        assert out == [1, None, 3]


# ---------------------------------------------------------------------------
# WorkflowRunner — registry
# ---------------------------------------------------------------------------


def _runner(specs, run_agent=None) -> WorkflowRunner:
    async def _noop(name, prompt, msg):  # pragma: no cover - default unused
        return ""

    return WorkflowRunner(specs, run_agent=run_agent or _noop)


class TestWorkflowRunnerRegistry:
    def test_contains_names_describe(self):
        async def fn(ctx):
            return "x"

        runner = _runner({"echo": {"name": "echo", "description": "echoes", "fn": fn}})
        assert "echo" in runner
        assert "missing" not in runner
        assert runner.names() == ["echo"]
        assert runner.describe("echo") == "echoes"
        assert runner.describe("missing") == ""


# ---------------------------------------------------------------------------
# WorkflowRunner — dispatch
# ---------------------------------------------------------------------------


class TestWorkflowRunnerDispatch:
    async def test_final_result_delivered_as_ai_message(self):
        async def echo(ctx):
            return f"echo:{ctx.input}"

        runner = _runner({"echo": {"name": "echo", "description": "", "fn": echo}})
        channel = FakeChannel()
        await runner.dispatch("echo", _inbound(content="hello"), channel)

        ai = [m for m in channel.sent if m.type == "ai"]
        assert len(ai) == 1
        assert ai[0].content == "echo:hello"

    async def test_progress_delivered_as_tool_progress(self):
        async def wf(ctx):
            await ctx.phase("Work")
            await ctx.log("step 1")
            return "done"

        runner = _runner({"wf": {"name": "wf", "description": "", "fn": wf}})
        channel = FakeChannel()
        await runner.dispatch("wf", _inbound(), channel)

        progress = [m for m in channel.sent if m.type == "tool_progress"]
        assert any("Work" in m.content for m in progress)
        assert any("step 1" in m.content for m in progress)
        final = [m for m in channel.sent if m.type == "ai"]
        assert final[-1].content == "done"

    async def test_empty_result_sends_no_final_message(self):
        async def wf(ctx):
            await ctx.phase("X")
            return ""

        runner = _runner({"wf": {"name": "wf", "description": "", "fn": wf}})
        channel = FakeChannel()
        await runner.dispatch("wf", _inbound(), channel)

        assert all(m.type != "ai" for m in channel.sent)

    async def test_unknown_workflow_is_noop(self):
        runner = _runner({})
        channel = FakeChannel()
        await runner.dispatch("nope", _inbound(), channel)
        assert channel.sent == []

    async def test_failure_sends_error_and_does_not_raise(self):
        async def boom(ctx):
            raise RuntimeError("kaboom")

        runner = _runner({"boom": {"name": "boom", "description": "", "fn": boom}})
        channel = FakeChannel()
        await runner.dispatch("boom", _inbound(), channel)  # must not raise

        assert len(channel.sent) == 1
        assert channel.sent[0].type == "ai"
        assert "failed" in channel.sent[0].content.lower()

    async def test_run_agent_receives_triggering_message(self):
        received: dict = {}

        async def run_agent(name, prompt, msg):
            received["name"] = name
            received["prompt"] = prompt
            received["msg"] = msg
            return "ok"

        async def wf(ctx):
            return await ctx.run("hello", agent="planner")

        runner = _runner({"wf": {"name": "wf", "description": "", "fn": wf}}, run_agent=run_agent)
        channel = FakeChannel()
        msg = _inbound()
        await runner.dispatch("wf", msg, channel)

        assert received["name"] == "planner"
        assert received["prompt"] == "hello"
        assert received["msg"] is msg
        assert channel.sent[-1].content == "ok"


# ---------------------------------------------------------------------------
# _interpret — the built-in workflow that runs an agent-composed plan
# ---------------------------------------------------------------------------


class TestInterpretWorkflow:
    def _ctx(self, plan, run_agent):
        return WorkflowContext(
            input="X", metadata={"plan": plan}, run_agent=run_agent, emit=AsyncMock()
        )

    async def test_runs_plan_and_returns_single_leaf_output(self):
        from langclaw.workflows.interpret import interpret_workflow

        async def run_agent(name, prompt):
            return f"out:{prompt}"

        plan = {
            "steps": [
                {"id": "a", "agent": "default", "prompt": "fetch {input}"},
                {"id": "b", "agent": "writer", "prompt": "sum {a}", "depends_on": ["a"]},
            ]
        }
        result = await interpret_workflow(self._ctx(plan, run_agent))
        # 'b' is the sole leaf; its prompt saw a's output.
        assert result == "out:sum out:fetch X"

    async def test_multiple_leaves_are_joined(self):
        from langclaw.workflows.interpret import interpret_workflow

        async def run_agent(name, prompt):
            return f"[{prompt}]"

        plan = {
            "steps": [
                {"id": "a", "agent": "default", "prompt": "A"},
                {"id": "b", "agent": "default", "prompt": "B"},
            ]
        }
        result = await interpret_workflow(self._ctx(plan, run_agent))
        assert "[A]" in result
        assert "[B]" in result

    async def test_missing_plan_returns_message(self):
        from langclaw.workflows.interpret import interpret_workflow

        ctx = WorkflowContext(input="", metadata={}, run_agent=AsyncMock(), emit=AsyncMock())
        out = await interpret_workflow(ctx)
        assert "plan" in out.lower()

    async def test_emits_numbered_step_completion(self):
        from langclaw.workflows.interpret import interpret_workflow

        emitted: list[str] = []

        async def emit(content, phase):
            emitted.append(content)

        async def run_agent(name, prompt):
            return "out"

        plan = {
            "steps": [
                {"id": "a", "agent": "default", "prompt": "A"},
                {"id": "b", "agent": "default", "prompt": "B"},
            ]
        }
        ctx = WorkflowContext(input="X", metadata={"plan": plan}, run_agent=run_agent, emit=emit)
        await interpret_workflow(ctx)
        # A "✓ N/2" completion line is emitted as each step finishes.
        assert any("✓" in c and "2/2" in c for c in emitted)


# ---------------------------------------------------------------------------
# WorkflowRunner — completion callback (agent-spawned workflows)
# ---------------------------------------------------------------------------


class TestWorkflowRunnerCallback:
    """When a workflow is spawned by the agent (``metadata['notify_agent']``),
    its completion re-enters the bus as a message *to the agent* rather than
    being delivered straight to the channel — so the agent can relay/act on it.
    """

    def _runner_with_notify(self, result="the result", result_dir=None):
        async def wf(ctx):
            return result

        notify = AsyncMock()
        runner = WorkflowRunner(
            {"wf": {"name": "wf", "description": "", "fn": wf}},
            run_agent=AsyncMock(return_value=""),
            notify=notify,
            result_dir=result_dir,
        )
        return runner, notify

    async def test_agent_spawned_notifies_agent_not_channel(self):
        runner, notify = self._runner_with_notify(result="the result")
        channel = FakeChannel()
        msg = _inbound(metadata={"notify_agent": True})

        await runner.dispatch("wf", msg, channel)

        notify.assert_awaited_once()
        notice = notify.call_args[0][0]
        assert isinstance(notice, InboundMessage)
        assert notice.to == "agent"
        assert notice.origin == "workflow"
        assert "the result" in notice.content
        # No final AI message went to the channel — the agent will relay it.
        assert all(m.type != "ai" for m in channel.sent)

    async def test_completion_message_does_not_re_trigger_dispatch(self):
        runner, notify = self._runner_with_notify()
        await runner.dispatch("wf", _inbound(metadata={"notify_agent": True}), FakeChannel())

        notice = notify.call_args[0][0]
        # Must NOT carry workflow_name, or _handle would dispatch it again → loop.
        assert "workflow_name" not in notice.metadata

    async def test_human_triggered_replies_to_channel_and_does_not_notify(self):
        runner, notify = self._runner_with_notify(result="the result")
        channel = FakeChannel()

        await runner.dispatch("wf", _inbound(metadata={}), channel)

        notify.assert_not_awaited()
        ai = [m for m in channel.sent if m.type == "ai"]
        assert ai and ai[-1].content == "the result"

    async def test_depth_is_propagated_into_completion(self):
        runner, notify = self._runner_with_notify()
        await runner.dispatch(
            "wf", _inbound(metadata={"notify_agent": True, "_depth": 2}), FakeChannel()
        )
        notice = notify.call_args[0][0]
        assert notice.metadata.get("_depth") == 2

    async def test_result_persisted_to_file_and_path_referenced(self, tmp_path):
        runner, notify = self._runner_with_notify(result="the big result", result_dir=tmp_path)
        await runner.dispatch("wf", _inbound(metadata={"notify_agent": True}), FakeChannel())

        files = list(tmp_path.glob("wf-*.md"))
        assert len(files) == 1
        assert files[0].read_text() == "the big result"
        notice = notify.call_args[0][0]
        assert str(files[0]) in notice.content
        assert notice.metadata.get("workflow_result_path") == str(files[0])

    async def test_notify_agent_without_callback_falls_back_to_channel(self):
        async def wf(ctx):
            return "the result"

        runner = WorkflowRunner(
            {"wf": {"name": "wf", "description": "", "fn": wf}},
            run_agent=AsyncMock(return_value=""),
            notify=None,
        )
        channel = FakeChannel()
        await runner.dispatch("wf", _inbound(metadata={"notify_agent": True}), channel)

        ai = [m for m in channel.sent if m.type == "ai"]
        assert ai and ai[-1].content == "the result"


# ---------------------------------------------------------------------------
# GatewayManager — _last_ai_text helper
# ---------------------------------------------------------------------------


class TestLastAiText:
    def test_extracts_last_ai_message_text(self):
        from langclaw.gateway.manager import _last_ai_text

        result = {"messages": [HumanMessage("h"), AIMessage("the answer")]}
        assert _last_ai_text(result) == "the answer"

    def test_handles_block_list_content(self):
        from langclaw.gateway.manager import _last_ai_text

        blocks = [{"type": "text", "text": "hi"}, {"type": "text", "text": "there"}]
        result = {"messages": [AIMessage(content=blocks)]}
        assert _last_ai_text(result) == "hi there"

    def test_returns_empty_when_no_ai_message(self):
        from langclaw.gateway.manager import _last_ai_text

        assert _last_ai_text({"messages": [HumanMessage("h")]}) == ""
        assert _last_ai_text({}) == ""


# ---------------------------------------------------------------------------
# GatewayManager — construction + /workflow command + _handle wiring
# ---------------------------------------------------------------------------


def _make_manager(workflow_specs=None, channels=None, bus=None):
    from langclaw.gateway.manager import GatewayManager

    config = MagicMock()
    config.agents.display_name = ""
    config.permissions.enabled = False
    checkpointer = MagicMock()
    checkpointer.get.return_value = MagicMock()

    return GatewayManager(
        config=config,
        bus=bus or MagicMock(),
        checkpointer_backend=checkpointer,
        agent=MagicMock(),
        channels=channels or [],
        workflow_specs=workflow_specs,
    )


class TestWorkflowCommand:
    def _specs(self):
        async def echo(ctx):
            return f"echo:{ctx.input}"

        return {"echo": {"name": "echo", "description": "echoes input", "fn": echo}}

    def _handler(self, mgr):
        entry = mgr._command_router._commands.get("workflow")
        assert entry is not None, "/workflow command should be registered"
        return entry.handler

    async def test_command_registered_only_with_specs(self):
        mgr = _make_manager(workflow_specs=None)
        assert mgr._command_router._commands.get("workflow") is None
        assert mgr._workflow_runner is None

    async def test_list_workflows(self):
        from langclaw.gateway.commands import CommandContext

        mgr = _make_manager(workflow_specs=self._specs())
        handler = self._handler(mgr)
        ctx = CommandContext(channel="t", user_id="u", context_id="c", chat_id="ch", args=[])
        out = await handler(ctx)
        assert "echo" in out
        assert "echoes input" in out

    async def test_unknown_workflow_returns_error(self):
        from langclaw.gateway.commands import CommandContext

        mgr = _make_manager(workflow_specs=self._specs())
        handler = self._handler(mgr)
        ctx = CommandContext(channel="t", user_id="u", context_id="c", chat_id="ch", args=["nope"])
        out = await handler(ctx)
        assert "Unknown workflow 'nope'" in out
        assert "echo" in out

    async def test_run_publishes_inbound_with_workflow_name(self):
        from langclaw.gateway.commands import CommandContext

        bus = MagicMock()
        bus.publish = AsyncMock()
        mgr = _make_manager(workflow_specs=self._specs(), bus=bus)
        handler = self._handler(mgr)
        ctx = CommandContext(
            channel="telegram",
            user_id="u1",
            context_id="ctx1",
            chat_id="chat1",
            args=["echo", "summarise", "the", "report"],
        )
        out = await handler(ctx)

        assert out == ""  # workflow streams its own output
        bus.publish.assert_called_once()
        msg = bus.publish.call_args[0][0]
        assert isinstance(msg, InboundMessage)
        assert msg.content == "summarise the report"
        assert msg.metadata.get("workflow_name") == "echo"


class TestHandleDispatchesWorkflow:
    async def test_handle_routes_workflow_name_to_runner(self):
        async def echo(ctx):
            return f"echo:{ctx.input}"

        specs = {"echo": {"name": "echo", "description": "", "fn": echo}}
        channel = FakeChannel()
        mgr = _make_manager(workflow_specs=specs, channels=[channel])

        msg = _inbound(channel="test", content="hello", metadata={"workflow_name": "echo"})
        await mgr._handle(msg)

        ai = [m for m in channel.sent if m.type == "ai"]
        assert ai, "workflow should have produced a final AI reply"
        assert ai[-1].content == "echo:hello"

    async def test_handle_without_workflow_name_does_not_dispatch(self):
        """A plain message must not be hijacked by the workflow runner."""

        async def wf(ctx):  # pragma: no cover - must never run
            raise AssertionError("workflow should not run for a plain message")

        specs = {"wf": {"name": "wf", "description": "", "fn": wf}}
        channel = FakeChannel()
        mgr = _make_manager(workflow_specs=specs, channels=[channel])
        mgr._workflow_runner.dispatch = AsyncMock()

        # Stub the agent path so _handle exercises real routing without
        # building a live agent: a no-op agent whose stream yields nothing.
        class _NoOpAgent:
            async def astream(self, *_a, **_k):
                if False:  # pragma: no cover - empty async generator
                    yield

        mgr._ensure_agent_fresh = AsyncMock(return_value=_NoOpAgent())

        await mgr._handle(_inbound(channel="test", content="just chatting"))

        mgr._workflow_runner.dispatch.assert_not_called()
        mgr._ensure_agent_fresh.assert_awaited_once()  # took the agent path


class TestRunAgentForWorkflow:
    async def test_returns_last_ai_text_from_isolated_thread(self):
        specs = {"echo": {"name": "echo", "description": "", "fn": None}}
        mgr = _make_manager(workflow_specs=specs)

        fake_agent = MagicMock()
        fake_agent.ainvoke = AsyncMock(
            return_value={"messages": [HumanMessage("p"), AIMessage("the answer")]}
        )
        mgr._ensure_agent_fresh = AsyncMock(return_value=fake_agent)
        mgr._agent_map["planner"] = fake_agent

        out = await mgr._run_agent_for_workflow("planner", "do it", _inbound())

        assert out == "the answer"
        fake_agent.ainvoke.assert_awaited_once()
        # Each step runs on its own isolated thread, never the user's.
        _args, kwargs = fake_agent.ainvoke.call_args
        thread_id = kwargs["config"]["configurable"]["thread_id"]
        assert thread_id.startswith("workflow:")
