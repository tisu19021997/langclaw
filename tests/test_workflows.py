"""
Tests for the langclaw-native Workflow primitive (issue #38, Phase 1).

Covers the deep, isolation-testable cores, asserting external behaviour:

1. Config + RBAC axis (``WorkflowsConfig`` / ``RoleConfig.workflows`` /
   ``allowed_workflow_names``).
2. Registry (``@app.workflow`` binding, collision detection).
3. ``WorkflowContext`` (phases, parallel fan-out, budget, schema coercion).
4. ``WorkflowRuntime`` (I/O validation, concurrency ceiling, timeout).
5. Durable resume (``StepMemoizer`` replays completed steps).
"""

from __future__ import annotations

import asyncio

import pytest
from pydantic import BaseModel

# ---------------------------------------------------------------------------
# 1 — Config + RBAC axis
# ---------------------------------------------------------------------------


def test_workflows_config_defaults():
    from langclaw.config.schema import WorkflowsConfig

    cfg = WorkflowsConfig()
    assert cfg.enabled is False
    assert cfg.max_concurrent_runs == 16
    assert cfg.max_steps_per_run == 1000
    assert cfg.max_depth == 2
    assert cfg.resume_on_startup is False


def test_workflows_config_on_root_and_env(monkeypatch):
    from langclaw.config.schema import LangclawConfig

    assert LangclawConfig(workflows={"enabled": False}).workflows.enabled is False
    monkeypatch.setenv("LANGCLAW__WORKFLOWS__ENABLED", "true")
    monkeypatch.setenv("LANGCLAW__WORKFLOWS__MAX_CONCURRENT_RUNS", "3")
    cfg = LangclawConfig()
    assert cfg.workflows.enabled is True
    assert cfg.workflows.max_concurrent_runs == 3


def test_role_config_workflows_default_empty():
    from langclaw.config.schema import RoleConfig

    assert RoleConfig().workflows == []
    assert RoleConfig(workflows=["digest"]).workflows == ["digest"]


def test_allowed_workflow_names_default_deny():
    from langclaw.config.schema import PermissionsConfig, RoleConfig
    from langclaw.middleware.permissions import allowed_workflow_names

    cfg = PermissionsConfig(
        enabled=True,
        roles={
            "viewer": RoleConfig(tools=["*"]),  # no workflows → deny
            "power": RoleConfig(workflows=["digest"]),
            "admin": RoleConfig(workflows=["*"]),
        },
    )
    universe = ["digest", "report"]
    assert allowed_workflow_names(cfg, "viewer", universe) == set()
    assert allowed_workflow_names(cfg, "power", universe) == {"digest"}
    assert allowed_workflow_names(cfg, "admin", universe) == set(universe)
    assert allowed_workflow_names(cfg, "ghost", universe) == set()


# ---------------------------------------------------------------------------
# 2 — Registry
# ---------------------------------------------------------------------------


def test_registry_register_and_get():
    from langclaw.workflows import WorkflowRegistry, WorkflowSpec

    reg = WorkflowRegistry()

    async def body(ctx, inp):
        return "ok"

    spec = WorkflowSpec(name="digest", fn=body, description="d")
    reg.register(spec)
    assert "digest" in reg
    assert reg.get("digest") is spec
    assert reg.names() == ["digest"]
    assert len(reg) == 1


def test_registry_rejects_duplicate():
    from langclaw.workflows import WorkflowRegistry, WorkflowSpec

    reg = WorkflowRegistry()

    async def body(ctx, inp):
        return None

    reg.register(WorkflowSpec(name="x", fn=body))
    with pytest.raises(ValueError, match="already registered"):
        reg.register(WorkflowSpec(name="x", fn=body))


def test_registry_rejects_reserved_name():
    from langclaw.workflows import WorkflowRegistry, WorkflowSpec

    reg = WorkflowRegistry()

    async def body(ctx, inp):
        return None

    with pytest.raises(ValueError, match="(?i)collide"):
        reg.register(WorkflowSpec(name="web_search", fn=body), reserved_names={"web_search"})


def test_registry_rejects_empty_name():
    from langclaw.workflows import WorkflowRegistry, WorkflowSpec

    reg = WorkflowRegistry()

    async def body(ctx, inp):
        return None

    with pytest.raises(ValueError, match="non-empty"):
        reg.register(WorkflowSpec(name="  ", fn=body))


# ---------------------------------------------------------------------------
# 2b — @app.workflow() decorator
# ---------------------------------------------------------------------------


def test_app_workflow_decorator_registers():
    from langclaw import Langclaw

    app = Langclaw()

    @app.workflow("digest", description="PR digest")
    async def digest(ctx, inp):
        return "ok"

    assert "digest" in app._workflows
    spec = app._workflows.get("digest")
    assert spec.description == "PR digest"
    assert spec.fn is digest


def test_app_workflow_collides_with_command():
    from langclaw import Langclaw
    from langclaw.gateway.commands import CommandContext  # noqa: F401

    app = Langclaw()

    @app.command("report")
    async def report_cmd(ctx):
        return "r"

    with pytest.raises(ValueError, match="(?i)collide"):

        @app.workflow("report")
        async def report_wf(ctx, inp):
            return None


def test_app_workflow_collides_with_subagent():
    from langclaw import Langclaw

    app = Langclaw()
    app.subagent("analyst", description="A", system_prompt="A.")

    with pytest.raises(ValueError, match="(?i)collide"):

        @app.workflow("analyst")
        async def analyst_wf(ctx, inp):
            return None


# ---------------------------------------------------------------------------
# 3 — WorkflowContext
# ---------------------------------------------------------------------------


def _recording_executor(log: list):
    async def _exec(request):
        log.append((request.kind, request.target, request.step_id, request.phase))
        return f"{request.kind}:{request.target}"

    return _exec


@pytest.mark.asyncio
async def test_context_sequential_steps_and_phase():
    from langclaw.workflows import WorkflowContext

    log: list = []
    ctx = WorkflowContext(executor=_recording_executor(log))

    ctx.phase("research")
    a = await ctx.agent("planner", "plan it")
    b = await ctx.tool("web_search", query="x")
    assert a == "agent:planner"
    assert b == "tool:web_search"
    # phase recorded on each step; IDs deterministic and distinct
    assert log[0][3] == "research" and log[1][3] == "research"
    assert log[0][2] != log[1][2]


@pytest.mark.asyncio
async def test_context_parallel_runs_all_branches():
    from langclaw.workflows import WorkflowContext

    log: list = []
    ctx = WorkflowContext(executor=_recording_executor(log))
    ctx.phase("fanout")

    async def branch(c):
        return await c.subagent("researcher", "go")

    results = await ctx.parallel([branch, branch, branch])
    assert results == ["subagent:researcher"] * 3
    # three branch step IDs, all distinct (deterministic per-branch prefix)
    step_ids = {entry[2] for entry in log}
    assert len(step_ids) == 3


@pytest.mark.asyncio
async def test_context_parallel_respects_concurrency_limit():
    from langclaw.workflows import WorkflowContext

    active = 0
    peak = 0

    async def _exec(request):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.01)
        active -= 1
        return "done"

    ctx = WorkflowContext(executor=_exec, semaphore=asyncio.Semaphore(2))

    async def branch(c):
        return await c.tool("t")

    await ctx.parallel([branch] * 6)
    assert peak <= 2


@pytest.mark.asyncio
async def test_context_step_budget_enforced():
    from langclaw.workflows import WorkflowBudgetExceeded, WorkflowContext

    async def _exec(request):
        return "ok"

    ctx = WorkflowContext(executor=_exec, max_steps=2)
    await ctx.tool("a")
    await ctx.tool("b")
    with pytest.raises(WorkflowBudgetExceeded):
        await ctx.tool("c")


@pytest.mark.asyncio
async def test_context_schema_coercion_and_error():
    from langclaw.workflows import WorkflowContext, WorkflowStepError

    class Out(BaseModel):
        n: int

    async def _exec(request):
        # return a dict the schema can validate
        return {"n": 5}

    ctx = WorkflowContext(executor=_exec)
    out = await ctx.agent("a", "p", schema=Out)
    assert isinstance(out, Out) and out.n == 5

    async def _bad_exec(request):
        return {"wrong": "shape"}

    ctx2 = WorkflowContext(executor=_bad_exec)
    with pytest.raises(WorkflowStepError):
        await ctx2.agent("a", "p", schema=Out)


@pytest.mark.asyncio
async def test_context_step_ids_stable_across_reruns():
    """Same body → same step IDs in the same order (resume depends on this)."""
    from langclaw.workflows import WorkflowContext

    async def run_once() -> list:
        log: list = []
        ctx = WorkflowContext(executor=_recording_executor(log))
        ctx.phase("p")
        await ctx.agent("a", "1")

        async def branch(c):
            await c.tool("t1")
            await c.tool("t2")

        await ctx.parallel([branch, branch])
        await ctx.agent("a", "2")
        return [entry[2] for entry in log]

    first = await run_once()
    second = await run_once()
    assert first == second


# ---------------------------------------------------------------------------
# 4 — WorkflowRuntime
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_runtime_validates_input_and_output():
    from langclaw.config.schema import WorkflowsConfig
    from langclaw.workflows import WorkflowRuntime, WorkflowSpec

    class In(BaseModel):
        topic: str

    class Out(BaseModel):
        summary: str

    async def body(ctx, inp):
        assert isinstance(inp, In)
        return {"summary": f"about {inp.topic}"}

    spec = WorkflowSpec(name="w", fn=body, input_model=In, output_model=Out)
    rt = WorkflowRuntime(WorkflowsConfig(enabled=True))

    async def executor(request):
        return "unused"

    out = await rt.start_run(spec, {"topic": "cats"}, run_id="r1", executor=executor)
    assert isinstance(out, Out) and out.summary == "about cats"


@pytest.mark.asyncio
async def test_runtime_timeout():
    from langclaw.config.schema import WorkflowsConfig
    from langclaw.workflows import WorkflowRuntime, WorkflowSpec

    async def body(ctx, inp):
        await ctx.tool("slow")
        return "done"

    spec = WorkflowSpec(name="w", fn=body, timeout_s=0.02)
    rt = WorkflowRuntime(WorkflowsConfig(enabled=True))

    async def slow_executor(request):
        await asyncio.sleep(1)
        return "x"

    with pytest.raises(asyncio.TimeoutError):
        await rt.start_run(spec, None, run_id="r1", executor=slow_executor)


@pytest.mark.asyncio
async def test_runtime_global_concurrency_ceiling():
    from langclaw.config.schema import WorkflowsConfig
    from langclaw.workflows import WorkflowRuntime, WorkflowSpec

    active = 0
    peak = 0

    async def body(ctx, inp):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.01)
        active -= 1
        return "ok"

    spec = WorkflowSpec(name="w", fn=body)
    rt = WorkflowRuntime(WorkflowsConfig(enabled=True, max_concurrent_runs=2))

    async def executor(request):
        return "x"

    await asyncio.gather(
        *(rt.start_run(spec, None, run_id=f"r{i}", executor=executor) for i in range(6))
    )
    assert peak <= 2


# ---------------------------------------------------------------------------
# 5 — Durable resume
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resume_replays_completed_steps():
    from langclaw.config.schema import WorkflowsConfig
    from langclaw.workflows import InMemoryStepStore, WorkflowRuntime, WorkflowSpec

    store = InMemoryStepStore()
    call_count = {"n": 0}

    async def body(ctx, inp):
        a = await ctx.tool("step_a")
        b = await ctx.tool("step_b")
        return {"a": a, "b": b}

    async def executor(request):
        call_count["n"] += 1
        if request.target == "step_b" and call_count["n"] <= 2:
            # Simulate a crash partway through the first attempt.
            raise RuntimeError("boom")
        return f"ran:{request.target}"

    spec = WorkflowSpec(name="w", fn=body, output_model=None)
    rt = WorkflowRuntime(WorkflowsConfig(enabled=True), step_store=store)

    # First attempt crashes after step_a persisted, before step_b.
    with pytest.raises(RuntimeError):
        await rt.start_run(spec, None, run_id="run-xyz", executor=executor)

    calls_before = call_count["n"]

    # Resume: step_a is cached → not re-run; only step_b executes.
    out = await rt.start_run(spec, None, run_id="run-xyz", executor=executor)
    assert out == {"a": "ran:step_a", "b": "ran:step_b"}
    # step_a was NOT re-executed on resume (only step_b ran).
    assert call_count["n"] == calls_before + 1


@pytest.mark.asyncio
async def test_resume_distinct_runs_do_not_share_cache():
    from langclaw.workflows import InMemoryStepStore, StepMemoizer
    from langclaw.workflows.context import StepRequest

    store = InMemoryStepStore()
    req = StepRequest(kind="tool", target="t", payload={}, step_id="p#0")

    async def runner_a():
        return "A"

    async def runner_b():
        return "B"

    a = await StepMemoizer(store, "run1").wrap(req, runner_a)
    b = await StepMemoizer(store, "run2").wrap(req, runner_b)
    assert a == "A"
    assert b == "B"  # different run_id → not served from run1's cache


# ---------------------------------------------------------------------------
# 6 — Bridge: workflow_<name> tool factory + default toolset executor
# ---------------------------------------------------------------------------


def _fake_tool(name: str, fn):
    """A minimal async tool stand-in exposing ``.name`` and ``.ainvoke``.

    The executor only reads ``.name`` and awaits ``.ainvoke(args_dict)``, so a
    tiny shim avoids langchain's docstring/schema requirements for test fns.
    """

    class _FakeTool:
        def __init__(self) -> None:
            self.name = name

        async def ainvoke(self, args: dict):
            return await fn(**args)

    return _FakeTool()


@pytest.mark.asyncio
async def test_toolset_executor_invokes_live_tool():
    from langclaw.workflows.bridge import build_toolset_executor

    async def echo(text: str) -> str:
        return f"echo:{text}"

    executor = build_toolset_executor([_fake_tool("echo", echo)])

    from langclaw.workflows.context import StepRequest

    req = StepRequest(kind="tool", target="echo", payload={"text": "hi"}, step_id="p#0")
    out = await executor(req)
    assert "echo:hi" in str(out)


@pytest.mark.asyncio
async def test_toolset_executor_unknown_tool_raises_step_error():
    from langclaw.workflows.bridge import build_toolset_executor
    from langclaw.workflows.context import StepRequest, WorkflowStepError

    executor = build_toolset_executor([])
    req = StepRequest(kind="tool", target="nope", payload={}, step_id="p#0")
    with pytest.raises(WorkflowStepError, match="(?i)tool 'nope'"):
        await executor(req)


@pytest.mark.asyncio
async def test_toolset_executor_subagent_routes_through_task():
    from langclaw.workflows.bridge import build_toolset_executor
    from langclaw.workflows.context import StepRequest

    seen = {}

    async def task(subagent_type: str, description: str) -> str:
        seen["type"] = subagent_type
        seen["desc"] = description
        return "delegated"

    executor = build_toolset_executor([_fake_tool("task", task)])
    req = StepRequest(kind="subagent", target="researcher", payload="go deep", step_id="p#0")
    out = await executor(req)
    assert "delegated" in str(out)
    assert seen["type"] == "researcher"
    assert seen["desc"] == "go deep"


def test_make_workflow_tools_names_and_count():
    from langclaw.config.schema import WorkflowsConfig
    from langclaw.workflows import WorkflowRegistry, WorkflowRuntime, WorkflowSpec
    from langclaw.workflows.bridge import make_workflow_tools

    reg = WorkflowRegistry()

    async def body(ctx, inp):
        return "ok"

    reg.register(WorkflowSpec(name="digest", fn=body, description="PR digest"))
    reg.register(WorkflowSpec(name="report", fn=body, description="Report"))

    rt = WorkflowRuntime(WorkflowsConfig(enabled=True))

    async def executor_factory(_runtime):
        async def _exec(_req):
            return "x"

        return _exec

    tools = make_workflow_tools(reg, rt, executor_factory=executor_factory)
    names = sorted(t.name for t in tools)
    assert names == ["workflow_digest", "workflow_report"]


@pytest.mark.asyncio
async def test_make_workflow_tool_runs_workflow_and_returns_output():
    from langclaw.config.schema import WorkflowsConfig
    from langclaw.workflows import WorkflowRegistry, WorkflowRuntime, WorkflowSpec
    from langclaw.workflows.bridge import make_workflow_tools

    reg = WorkflowRegistry()

    async def body(ctx, inp):
        a = await ctx.tool("greet", who=inp["who"])
        return {"result": a}

    reg.register(WorkflowSpec(name="hello", fn=body))
    rt = WorkflowRuntime(WorkflowsConfig(enabled=True))

    async def executor_factory(_tool_runtime):
        async def _exec(request):
            return f"hi {request.payload['who']}"

        return _exec

    tools = make_workflow_tools(reg, rt, executor_factory=executor_factory)
    tool = next(t for t in tools if t.name == "workflow_hello")

    result = await tool.ainvoke({"workflow_input": {"who": "sam"}})
    assert "hi sam" in str(result)


@pytest.mark.asyncio
async def test_make_workflow_tool_returns_error_string_on_failure():
    """A failing workflow returns an error string, never raises into the agent."""
    from langclaw.config.schema import WorkflowsConfig
    from langclaw.workflows import WorkflowRegistry, WorkflowRuntime, WorkflowSpec
    from langclaw.workflows.bridge import make_workflow_tools

    reg = WorkflowRegistry()

    async def body(ctx, inp):
        raise RuntimeError("kaboom")

    reg.register(WorkflowSpec(name="boom", fn=body))
    rt = WorkflowRuntime(WorkflowsConfig(enabled=True))

    async def executor_factory(_tool_runtime):
        async def _exec(_req):
            return "x"

        return _exec

    tools = make_workflow_tools(reg, rt, executor_factory=executor_factory)
    tool = next(t for t in tools if t.name == "workflow_boom")

    result = await tool.ainvoke({"workflow_input": {}})
    assert "error" in str(result).lower()
    assert "kaboom" in str(result)


# ---------------------------------------------------------------------------
# 7 — Builder wiring: workflow tools added only when enabled
# ---------------------------------------------------------------------------


def _capture_deep_agent(monkeypatch):
    import deepagents

    captured: dict = {}

    def fake_create_deep_agent(**kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(deepagents, "create_deep_agent", fake_create_deep_agent)
    return captured


def test_builder_adds_workflow_tools_when_enabled(monkeypatch):
    from langclaw.agents.builder import create_claw_agent
    from langclaw.config.schema import LangclawConfig
    from langclaw.workflows import WorkflowRegistry, WorkflowRuntime, WorkflowSpec

    reg = WorkflowRegistry()

    async def body(ctx, inp):
        return "ok"

    reg.register(WorkflowSpec(name="digest", fn=body))

    cfg = LangclawConfig(interpreter={"enabled": False})
    cfg.workflows.enabled = True
    rt = WorkflowRuntime(cfg.workflows)

    captured = _capture_deep_agent(monkeypatch)
    create_claw_agent(cfg, model=object(), workflow_registry=reg, workflow_runtime=rt)
    tool_names = [getattr(t, "name", "") for t in captured["tools"]]
    assert "workflow_digest" in tool_names


def test_builder_omits_workflow_tools_when_disabled(monkeypatch):
    from langclaw.agents.builder import create_claw_agent
    from langclaw.config.schema import LangclawConfig
    from langclaw.workflows import WorkflowRegistry, WorkflowRuntime, WorkflowSpec

    reg = WorkflowRegistry()

    async def body(ctx, inp):
        return "ok"

    reg.register(WorkflowSpec(name="digest", fn=body))

    cfg = LangclawConfig(interpreter={"enabled": False})
    cfg.workflows.enabled = False
    rt = WorkflowRuntime(cfg.workflows)

    captured = _capture_deep_agent(monkeypatch)
    create_claw_agent(cfg, model=object(), workflow_registry=reg, workflow_runtime=rt)
    tool_names = [getattr(t, "name", "") for t in captured["tools"]]
    assert not any(n.startswith("workflow_") for n in tool_names)
