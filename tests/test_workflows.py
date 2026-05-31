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
from types import SimpleNamespace

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


def test_app_role_carries_subagent_and_workflow_axes():
    """``app.role()`` must accept the ``subagents`` and ``workflows`` axes so
    operators never have to reach into ``config.permissions.roles`` (which is
    only populated at build time). The effective config reflects all three."""
    from langclaw import Langclaw

    app = Langclaw()
    app.role("analyst", tools=["*"], subagents=["writer"], workflows=["research"])

    cfg = app._build_effective_config()
    role = cfg.permissions.roles["analyst"]
    assert role.tools == ["*"]
    assert role.subagents == ["writer"]
    assert role.workflows == ["research"]


def test_app_role_merges_axes_across_calls():
    """Repeated ``app.role()`` calls merge each axis, deduping order-stably."""
    from langclaw import Langclaw

    app = Langclaw()
    app.role("analyst", tools=["web_search"], workflows=["research"])
    app.role("analyst", tools=["web_search", "web_fetch"], workflows=["digest"])

    role = app._build_effective_config().permissions.roles["analyst"]
    assert role.tools == ["web_search", "web_fetch"]
    assert role.workflows == ["research", "digest"]


def test_app_create_agent_wires_registered_workflows(monkeypatch):
    """The production agent-build path (run -> create_agent) must expose
    registered workflows as workflow_<name> tools, not just the builder when
    called directly with explicit kwargs."""
    import deepagents

    from langclaw import Langclaw
    from langclaw.config.schema import LangclawConfig

    captured: dict = {}

    def fake_create_deep_agent(**kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(deepagents, "create_deep_agent", fake_create_deep_agent)

    app = Langclaw(config=LangclawConfig(interpreter={"enabled": False}))
    app.config.workflows.enabled = True

    @app.workflow("digest", description="PR digest")
    async def digest(ctx, inp):
        return "ok"

    app.create_agent(model=object())
    tool_names = [getattr(t, "name", "") for t in captured["tools"]]
    assert "workflow_digest" in tool_names


def test_app_create_agent_omits_workflows_when_disabled(monkeypatch):
    import deepagents

    from langclaw import Langclaw
    from langclaw.config.schema import LangclawConfig

    captured: dict = {}

    def fake_create_deep_agent(**kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(deepagents, "create_deep_agent", fake_create_deep_agent)

    # Disable workflows EXPLICITLY — do not rely on the ambient default, which
    # LangclawConfig() reads from ~/.langclaw/config.json (a developer who has
    # enabled workflows there would otherwise break this test).
    app = Langclaw(
        config=LangclawConfig(interpreter={"enabled": False}, workflows={"enabled": False})
    )

    @app.workflow("digest", description="PR digest")
    async def digest(ctx, inp):
        return "ok"

    app.create_agent(model=object())
    tool_names = [getattr(t, "name", "") for t in captured["tools"]]
    assert not any(n.startswith("workflow_") for n in tool_names)


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


# ---------------------------------------------------------------------------
# 8 — Phase 2 / Mode 1: PTC workflow surface + workflow-axis RBAC
# ---------------------------------------------------------------------------


def test_resolve_workflow_ptc_names_disabled_returns_empty():
    from langclaw.config.schema import WorkflowsConfig
    from langclaw.workflows import WorkflowRegistry, WorkflowSpec
    from langclaw.workflows.bridge import resolve_workflow_ptc_names

    reg = WorkflowRegistry()

    async def body(ctx, inp):
        return None

    reg.register(WorkflowSpec(name="digest", fn=body))
    out = resolve_workflow_ptc_names(reg, workflows_config=WorkflowsConfig(enabled=False))
    assert out == []


def test_resolve_workflow_ptc_names_all_when_no_permissions():
    from langclaw.config.schema import WorkflowsConfig
    from langclaw.workflows import WorkflowRegistry, WorkflowSpec
    from langclaw.workflows.bridge import resolve_workflow_ptc_names

    reg = WorkflowRegistry()

    async def body(ctx, inp):
        return None

    reg.register(WorkflowSpec(name="digest", fn=body))
    reg.register(WorkflowSpec(name="report", fn=body))
    out = resolve_workflow_ptc_names(reg, workflows_config=WorkflowsConfig(enabled=True))
    # Returns the actual tool names (workflow_<name>) to merge into the PTC allowlist.
    assert out == ["workflow_digest", "workflow_report"]


def test_resolve_workflow_ptc_names_role_gated_default_deny():
    from langclaw.config.schema import PermissionsConfig, RoleConfig, WorkflowsConfig
    from langclaw.workflows import WorkflowRegistry, WorkflowSpec
    from langclaw.workflows.bridge import resolve_workflow_ptc_names

    reg = WorkflowRegistry()

    async def body(ctx, inp):
        return None

    reg.register(WorkflowSpec(name="digest", fn=body))
    reg.register(WorkflowSpec(name="report", fn=body))

    perms = PermissionsConfig(
        enabled=True,
        roles={
            "viewer": RoleConfig(tools=["*"]),  # no workflows → deny all
            "power": RoleConfig(workflows=["digest"]),
        },
    )
    wcfg = WorkflowsConfig(enabled=True)
    assert (
        resolve_workflow_ptc_names(
            reg, workflows_config=wcfg, permissions_config=perms, role="viewer"
        )
        == []
    )
    assert resolve_workflow_ptc_names(
        reg, workflows_config=wcfg, permissions_config=perms, role="power"
    ) == ["workflow_digest"]


# -- workflow-axis RBAC middleware --------------------------------------------


def _wf_tool(name: str):
    return SimpleNamespace(name=name)


def _run_model_call(mw, tools, user_role):
    """Drive a wrap_model_call middleware once and return the tools the handler saw.

    Mirrors the proven pattern in ``test_interpreter`` (SimpleNamespace request
    with an ``override`` lambda, executed via ``asyncio.run``) so the harness
    matches langchain's handler contract exactly.
    """
    import asyncio

    runtime = SimpleNamespace(context=SimpleNamespace(user_role=user_role))
    request = SimpleNamespace(
        runtime=runtime,
        tools=tools,
        override=lambda **kw: SimpleNamespace(**{"tools": tools, "runtime": runtime, **kw}),
    )
    captured = {}

    async def handler(req):
        captured["tools"] = req.tools
        return "ok"

    asyncio.run(mw.awrap_model_call(request, handler))
    return {t.name for t in captured["tools"]}


def test_workflow_permission_middleware_filters_by_workflow_axis():
    from langclaw.config.schema import PermissionsConfig, RoleConfig
    from langclaw.middleware.permissions import build_workflow_permission_middleware

    cfg = PermissionsConfig(
        enabled=True,
        roles={"power": RoleConfig(tools=["*"], workflows=["digest"])},
    )
    mw = build_workflow_permission_middleware(cfg)

    tools = [_wf_tool("web_search"), _wf_tool("workflow_digest"), _wf_tool("workflow_secret")]
    names = _run_model_call(mw, tools, "power")
    # non-workflow tools untouched; only the permitted workflow remains
    assert "web_search" in names
    assert "workflow_digest" in names
    assert "workflow_secret" not in names


def test_tool_permission_filter_passes_workflow_tools_through():
    """The tool-axis filter must NOT strip workflow_* tools (workflow axis owns them)."""
    from langclaw.config.schema import PermissionsConfig, RoleConfig
    from langclaw.middleware.permissions import build_tool_permission_middleware

    # viewer may use only web_search on the tool axis — but workflow_* tools
    # are governed by the workflow axis, so they must pass through here.
    cfg = PermissionsConfig(enabled=True, roles={"viewer": RoleConfig(tools=["web_search"])})
    mw = build_tool_permission_middleware(cfg)

    tools = [_wf_tool("web_search"), _wf_tool("delete_file"), _wf_tool("workflow_digest")]
    names = _run_model_call(mw, tools, "viewer")
    assert names == {"web_search", "workflow_digest"}  # delete_file stripped, workflow passed


def test_builder_exposes_workflows_to_ptc_when_interpreter_and_workflows_enabled(monkeypatch):
    pytest.importorskip("langchain_quickjs")
    from langclaw.agents.builder import create_claw_agent
    from langclaw.config.schema import InterpreterConfig, LangclawConfig
    from langclaw.workflows import WorkflowRegistry, WorkflowRuntime, WorkflowSpec

    reg = WorkflowRegistry()

    async def body(ctx, inp):
        return "ok"

    reg.register(WorkflowSpec(name="digest", fn=body))

    cfg = LangclawConfig()
    cfg.interpreter = InterpreterConfig(enabled=True)
    cfg.workflows.enabled = True
    rt = WorkflowRuntime(cfg.workflows)

    captured = _capture_deep_agent(monkeypatch)
    create_claw_agent(cfg, model=object(), workflow_registry=reg, workflow_runtime=rt)

    # The interpreter middleware's PTC allowlist must include the workflow tool
    # so a script can reach it as tools.workflowDigest.
    mw_by_name = {type(m).__name__: m for m in captured["middleware"]}
    interp = mw_by_name.get("CodeInterpreterMiddleware")
    assert interp is not None
    assert "workflow_digest" in interp._ptc


def test_builder_wires_workflow_permission_middleware(monkeypatch):
    from langclaw.agents.builder import create_claw_agent
    from langclaw.config.schema import LangclawConfig
    from langclaw.workflows import WorkflowRegistry, WorkflowRuntime, WorkflowSpec

    reg = WorkflowRegistry()

    async def body(ctx, inp):
        return "ok"

    reg.register(WorkflowSpec(name="digest", fn=body))

    cfg = LangclawConfig(interpreter={"enabled": False})
    cfg.workflows.enabled = True
    cfg.permissions.enabled = True
    rt = WorkflowRuntime(cfg.workflows)

    captured = _capture_deep_agent(monkeypatch)
    create_claw_agent(cfg, model=object(), workflow_registry=reg, workflow_runtime=rt)
    names = [type(m).__name__ for m in captured["middleware"]]
    assert "_workflow_permission_filter" in names


# ---------------------------------------------------------------------------
# 6 — Mode 2 (llm_authored): authored-script persistence + replay
# ---------------------------------------------------------------------------


async def test_authored_script_first_run_authors_and_persists():
    """First run of an llm_authored workflow calls the author, persists the
    script, and reports it was freshly authored."""
    from langclaw.workflows.authored import AuthoredScriptResolver, InMemoryScriptStore

    store = InMemoryScriptStore()
    resolver = AuthoredScriptResolver(store)
    calls = 0

    async def author() -> str:
        nonlocal calls
        calls += 1
        return "return tools.webSearch({query: inp.topic});"

    script, freshly_authored = await resolver.resolve("research", "run-1", author)
    assert freshly_authored is True
    assert "webSearch" in script
    assert calls == 1
    assert len(store) == 1


async def test_authored_script_resume_replays_same_script():
    """Resuming the same run returns the persisted script WITHOUT re-authoring
    — the body is deterministic across restarts (US 26)."""
    from langclaw.workflows.authored import AuthoredScriptResolver, InMemoryScriptStore

    store = InMemoryScriptStore()
    resolver = AuthoredScriptResolver(store)
    versions = iter(["FIRST", "SECOND"])

    async def author() -> str:
        return next(versions)

    first, fresh1 = await resolver.resolve("research", "run-1", author)
    second, fresh2 = await resolver.resolve("research", "run-1", author)
    assert (first, fresh1) == ("FIRST", True)
    assert (second, fresh2) == ("FIRST", False)  # replayed, not re-authored


async def test_authored_script_distinct_runs_reauthor():
    """A different run_id re-authors (each run gets its own frozen body)."""
    from langclaw.workflows.authored import AuthoredScriptResolver, InMemoryScriptStore

    store = InMemoryScriptStore()
    resolver = AuthoredScriptResolver(store)
    versions = iter(["A", "B"])

    async def author() -> str:
        return next(versions)

    s1, _ = await resolver.resolve("research", "run-1", author)
    s2, _ = await resolver.resolve("research", "run-2", author)
    assert s1 == "A"
    assert s2 == "B"
    assert len(store) == 2


async def test_authored_script_rejects_empty_body():
    """An author that returns a blank/non-string script raises rather than
    persisting a useless body."""
    import pytest

    from langclaw.workflows.authored import AuthoredScriptResolver, InMemoryScriptStore

    store = InMemoryScriptStore()
    resolver = AuthoredScriptResolver(store)

    async def blank_author() -> str:
        return "   "

    with pytest.raises(ValueError, match="(?i)non-empty"):
        await resolver.resolve("research", "run-1", blank_author)
    assert len(store) == 0  # nothing persisted


async def test_in_memory_script_store_roundtrip():
    from langclaw.workflows.authored import InMemoryScriptStore

    store = InMemoryScriptStore()
    assert await store.get("w", "r") == (False, None)
    await store.put("w", "r", "BODY")
    assert await store.get("w", "r") == (True, "BODY")


# ---------------------------------------------------------------------------
# 6b — Mode 2: registration validation + runtime dispatch (slice A)
# ---------------------------------------------------------------------------


def test_workflow_spec_rejects_unknown_mode():
    from langclaw.workflows import WorkflowSpec

    with pytest.raises(ValueError, match="(?i)mode"):
        WorkflowSpec(name="x", fn=lambda c, i: None, mode="bogus")


def test_workflow_spec_llm_authored_requires_description():
    """llm_authored has no Python body — the description IS the authoring spec
    the LLM writes the body from, so it must be present."""
    from langclaw.workflows import WorkflowSpec

    with pytest.raises(ValueError, match="(?i)description"):
        WorkflowSpec(name="x", fn=lambda c, i: None, mode="llm_authored")


async def test_runtime_mode2_authors_then_runs_script():
    """An llm_authored run authors the body, executes it via the injected
    script_runner, and returns the validated output."""
    from langclaw.config.schema import WorkflowsConfig
    from langclaw.workflows import WorkflowRuntime, WorkflowSpec

    spec = WorkflowSpec(
        name="research",
        fn=lambda c, i: None,  # ignored for llm_authored
        description="Research the topic and return a brief.",
        mode="llm_authored",
    )
    rt = WorkflowRuntime(WorkflowsConfig(enabled=True))
    seen: dict = {}

    async def author(s, inp):
        seen["author"] = (s.name, inp)
        return "return 'BODY';"

    async def script_runner(script, inp):
        seen["run"] = (script, inp)
        return f"ran:{script}"

    out = await rt.start_run(
        spec, {"topic": "AI"}, run_id="r1", author=author, script_runner=script_runner
    )
    assert out == "ran:return 'BODY';"
    assert seen["author"] == ("research", {"topic": "AI"})
    assert seen["run"] == ("return 'BODY';", {"topic": "AI"})


async def test_runtime_mode2_resume_replays_without_reauthoring():
    """With a script_store, resuming the same run replays the frozen body and
    never calls the author again."""
    from langclaw.config.schema import WorkflowsConfig
    from langclaw.workflows import InMemoryScriptStore, WorkflowRuntime, WorkflowSpec

    spec = WorkflowSpec(name="research", fn=lambda c, i: None, description="d", mode="llm_authored")
    store = InMemoryScriptStore()
    rt = WorkflowRuntime(WorkflowsConfig(enabled=True), script_store=store)
    versions = iter(["V1", "V2"])
    author_calls = 0

    async def author(s, inp):
        nonlocal author_calls
        author_calls += 1
        return next(versions)

    async def script_runner(script, inp):
        return script

    o1 = await rt.start_run(spec, None, run_id="r1", author=author, script_runner=script_runner)
    o2 = await rt.start_run(spec, None, run_id="r1", author=author, script_runner=script_runner)
    assert (o1, o2) == ("V1", "V1")
    assert author_calls == 1


async def test_runtime_mode2_requires_author_and_runner():
    from langclaw.config.schema import WorkflowsConfig
    from langclaw.workflows import WorkflowRuntime, WorkflowSpec

    spec = WorkflowSpec(name="research", fn=lambda c, i: None, description="d", mode="llm_authored")
    rt = WorkflowRuntime(WorkflowsConfig(enabled=True))
    with pytest.raises(ValueError, match="(?i)author"):
        await rt.start_run(spec, None, run_id="r1")  # no author/script_runner


async def test_runtime_python_mode_still_requires_executor():
    """The default python path is unchanged and still drives spec.fn via the
    injected step executor."""
    from langclaw.config.schema import WorkflowsConfig
    from langclaw.workflows import WorkflowRuntime, WorkflowSpec

    async def body(ctx, inp):
        return await ctx.tool("web_search", query="x")

    spec = WorkflowSpec(name="py", fn=body, description="d")
    rt = WorkflowRuntime(WorkflowsConfig(enabled=True))

    async def executor(req):
        return f"did:{req.target}"

    out = await rt.start_run(spec, None, run_id="r1", executor=executor)
    assert out == "did:web_search"


# ---------------------------------------------------------------------------
# 6c — Mode 2: the JS script runner (real QuickJS, slice B1)
# ---------------------------------------------------------------------------


def _fake_async_tool(name: str, fn):
    from langchain_core.tools import StructuredTool

    return StructuredTool.from_function(coroutine=fn, name=name, description=f"{name} tool")


async def test_script_runner_executes_authored_body_with_tool_and_input():
    """A built runner injects the typed input as `inp`, exposes the allowlisted
    tools as `tools.<camel>`, runs the authored JS, and returns its JSON output."""
    pytest.importorskip("langchain_quickjs")
    from langclaw.workflows.js_runner import build_workflow_script_runner

    async def web_search(query: str) -> str:
        return f"HIT[{query}]"

    runner = build_workflow_script_runner([_fake_async_tool("web_search", web_search)])
    script = (
        "const hits = [];\n"
        "for (let i = 0; i < inp.n; i++) "
        "{ hits.push(await tools.webSearch({query: inp.topic + ':' + i})); }\n"
        "JSON.stringify({topic: inp.topic, hits});"
    )
    out = await runner(script, {"topic": "AI", "n": 2})
    assert out == {"topic": "AI", "hits": ["HIT[AI:0]", "HIT[AI:1]"]}


async def test_script_runner_accepts_pydantic_input():
    pytest.importorskip("langchain_quickjs")
    from langclaw.workflows.js_runner import build_workflow_script_runner

    class Brief(BaseModel):
        topic: str

    runner = build_workflow_script_runner([])
    out = await runner("inp.topic.toUpperCase();", Brief(topic="solar"))
    assert out == "SOLAR"


async def test_script_runner_raises_workflowsteperror_on_js_error():
    pytest.importorskip("langchain_quickjs")
    from langclaw.workflows.context import WorkflowStepError
    from langclaw.workflows.js_runner import build_workflow_script_runner

    runner = build_workflow_script_runner([])
    with pytest.raises(WorkflowStepError):
        await runner("nonexistentFunction();", None)


async def test_script_runner_disallowed_tool_is_unavailable():
    """A tool NOT in the runner's list is not on `tools` — the list IS the
    capability allowlist (the workflow's uses_tools, role-filtered upstream)."""
    pytest.importorskip("langchain_quickjs")
    from langclaw.workflows.context import WorkflowStepError
    from langclaw.workflows.js_runner import build_workflow_script_runner

    runner = build_workflow_script_runner([])  # no tools exposed
    with pytest.raises(WorkflowStepError):
        await runner("await tools.webSearch({query: 'x'});", None)


# ---------------------------------------------------------------------------
# 6d — Mode 2: the authoring prompt + model call (slice B2)
# ---------------------------------------------------------------------------


class _FakeModel:
    """Records the prompt and returns a canned AIMessage-like response."""

    def __init__(self, content: str) -> None:
        self._content = content
        self.seen_prompt: str | None = None

    async def ainvoke(self, prompt, *_, **__):
        self.seen_prompt = prompt if isinstance(prompt, str) else str(prompt)
        return SimpleNamespace(content=self._content)


async def test_author_extracts_js_from_fenced_response():
    """The author strips a ```js fence and returns the bare script body."""
    from langclaw.workflows.js_runner import build_workflow_author
    from langclaw.workflows.registry import WorkflowSpec

    model = _FakeModel("```javascript\nJSON.stringify({ok: inp.topic});\n```")
    author = build_workflow_author(model)
    spec = WorkflowSpec(
        name="research", fn=lambda c, i: None, description="Research it.", mode="llm_authored"
    )

    script = await author(spec, {"topic": "AI"})
    assert script == "JSON.stringify({ok: inp.topic});"


async def test_author_prompt_carries_contract():
    """The authoring prompt includes the task description, the allowlisted tool
    signatures (camelCase name + args + description), the run input, and the
    defensive result-shape rule so a blind one-shot body extracts correctly."""
    from langchain_core.tools import StructuredTool

    from langclaw.workflows.js_runner import build_workflow_author
    from langclaw.workflows.registry import WorkflowSpec

    async def web_search(query: str, n: int = 5):
        """Search the web for recent information."""
        return []

    model = _FakeModel("inp.topic;")
    author = build_workflow_author(
        model,
        tools=[StructuredTool.from_function(coroutine=web_search, name="web_search")],
    )
    spec = WorkflowSpec(
        name="research",
        fn=lambda c, i: None,
        description="Gather angles and synthesize.",
        mode="llm_authored",
    )

    await author(spec, {"topic": "solar"})
    prompt = model.seen_prompt
    assert "Gather angles and synthesize." in prompt
    assert "tools.webSearch" in prompt  # camelCased signature
    assert "query" in prompt  # rendered arg name
    assert "Search the web" in prompt  # tool description carried through
    assert "solar" in prompt  # the concrete input
    assert "tools.output" in prompt  # the structural output sink
    assert "blind" in prompt.lower()  # the defensive one-shot rule


async def test_author_passes_through_unfenced_response():
    from langclaw.workflows.js_runner import build_workflow_author
    from langclaw.workflows.registry import WorkflowSpec

    model = _FakeModel("  inp.n * 2;  ")
    author = build_workflow_author(model)
    spec = WorkflowSpec(name="x", fn=lambda c, i: None, description="d", mode="llm_authored")
    assert await author(spec, {"n": 3}) == "inp.n * 2;"


# ---------------------------------------------------------------------------
# 6e — Mode 2: bridge dispatch (workflow_<name> tool routes by mode, slice B3)
# ---------------------------------------------------------------------------


async def test_make_workflow_tools_routes_llm_authored_to_author_and_runner():
    """The workflow_<name> tool for an llm_authored spec drives author +
    script_runner (not the step executor)."""
    from langclaw.config.schema import WorkflowsConfig
    from langclaw.workflows import WorkflowRegistry, WorkflowRuntime, WorkflowSpec
    from langclaw.workflows.bridge import make_workflow_tools

    reg = WorkflowRegistry()
    reg.register(
        WorkflowSpec(name="research", fn=lambda c, i: None, description="d", mode="llm_authored")
    )
    rt = WorkflowRuntime(WorkflowsConfig(enabled=True))

    calls: dict = {}

    async def author(spec, inp):
        calls["authored"] = spec.name
        return "SCRIPT"

    async def script_runner(script, inp):
        calls["ran"] = script
        return "DONE"

    async def executor_factory(_runtime):
        raise AssertionError("python executor must NOT be used for llm_authored")

    tools = make_workflow_tools(
        reg,
        rt,
        executor_factory=executor_factory,
        author_factory=lambda spec: author,
        script_runner_factory=lambda spec: script_runner,
    )
    result = await tools[0].coroutine(workflow_input={"topic": "AI"})
    assert calls == {"authored": "research", "ran": "SCRIPT"}
    assert "DONE" in result


async def test_make_workflow_tools_python_still_uses_executor():
    from langclaw.config.schema import WorkflowsConfig
    from langclaw.workflows import WorkflowRegistry, WorkflowRuntime, WorkflowSpec
    from langclaw.workflows.bridge import make_workflow_tools

    reg = WorkflowRegistry()

    async def body(ctx, inp):
        return await ctx.tool("web_search", query="x")

    reg.register(WorkflowSpec(name="py", fn=body, description="d"))
    rt = WorkflowRuntime(WorkflowsConfig(enabled=True))

    async def executor_factory(_runtime):
        async def executor(req):
            return f"did:{req.target}"

        return executor

    tools = make_workflow_tools(reg, rt, executor_factory=executor_factory)
    result = await tools[0].coroutine(workflow_input=None)
    assert "did:web_search" in result


async def test_builder_wires_mode2_end_to_end(monkeypatch):
    """Through create_claw_agent: an llm_authored workflow becomes a tool whose
    invocation has the (fake) model author a JS body and runs it over the
    spec's uses_tools allowlist in real QuickJS."""
    pytest.importorskip("langchain_quickjs")
    import deepagents
    from langchain_core.tools import StructuredTool

    from langclaw.agents.builder import create_claw_agent
    from langclaw.config.schema import LangclawConfig
    from langclaw.workflows import WorkflowRegistry, WorkflowRuntime, WorkflowSpec

    captured: dict = {}

    def fake_create_deep_agent(**kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(deepagents, "create_deep_agent", fake_create_deep_agent)

    async def lookup(query: str) -> str:
        return f"HIT[{query}]"

    lookup_tool = StructuredTool.from_function(coroutine=lookup, name="lookup", description="x")

    class FakeModel:
        async def ainvoke(self, prompt, *_, **__):
            return SimpleNamespace(
                content="JSON.stringify(await tools.lookup({query: inp.topic}));"
            )

    reg = WorkflowRegistry()
    reg.register(
        WorkflowSpec(
            name="research",
            fn=lambda c, i: None,
            description="Look up the topic.",
            mode="llm_authored",
            uses_tools=["lookup"],
        )
    )
    cfg = LangclawConfig(interpreter={"enabled": False})
    cfg.workflows.enabled = True
    rt = WorkflowRuntime(cfg.workflows)

    create_claw_agent(
        cfg,
        model=FakeModel(),
        extra_tools=[lookup_tool],
        workflow_registry=reg,
        workflow_runtime=rt,
    )
    wf_tool = next(t for t in captured["tools"] if getattr(t, "name", "") == "workflow_research")
    out = await wf_tool.coroutine(workflow_input={"topic": "AI"})
    assert "HIT[AI]" in out


async def test_script_runner_rejects_unmarshalable_result():
    """A body that ends on a Promise/function yields QuickJS's
    `[unmarshalable value]`; the runner must raise, not pass garbage through
    (otherwise the agent confabulates a result from nothing)."""
    pytest.importorskip("langchain_quickjs")
    from langclaw.workflows.context import WorkflowStepError
    from langclaw.workflows.js_runner import build_workflow_script_runner

    runner = build_workflow_script_runner([])
    # ends on an un-awaited async IIFE → unmarshalable handle
    with pytest.raises(WorkflowStepError, match="(?i)serializ|json.stringify"):
        await runner("(async () => ({ a: 1 }))();", None)


async def test_script_runner_output_sink_is_canonical_result():
    """Calling tools.output({result: ...}) sets the workflow result; the
    script's final expression is then irrelevant (the structural contract,
    not a prompt plea)."""
    pytest.importorskip("langchain_quickjs")
    from langclaw.workflows.js_runner import build_workflow_script_runner

    runner = build_workflow_script_runner([])
    script = (
        "await tools.output({ result: { topic: inp.topic, hits: 2 } });\n"
        "'this final expression is ignored';"
    )
    out = await runner(script, {"topic": "AI"})
    assert out == {"topic": "AI", "hits": 2}


async def test_script_runner_output_sink_avoids_unmarshalable():
    """Even when the final expression is an un-awaited Promise, calling
    output() first yields a clean result instead of [unmarshalable value]."""
    pytest.importorskip("langchain_quickjs")
    from langclaw.workflows.js_runner import build_workflow_script_runner

    runner = build_workflow_script_runner([])
    script = "await tools.output({ result: 'done' });\n(async () => 1)();"
    assert await runner(script, None) == "done"


# ---------------------------------------------------------------------------
# 7 — Channel progress projection (phase + authored-script events)
# ---------------------------------------------------------------------------


def test_progress_sink_set_emit_reset():
    from langclaw.workflows.progress import emit_progress, reset_progress_sink, set_progress_sink

    got: list = []
    token = set_progress_sink(lambda e: got.append(e))
    try:
        emit_progress({"kind": "phase", "phase": "x"})
    finally:
        reset_progress_sink(token)
    assert got == [{"kind": "phase", "phase": "x"}]
    # after reset there is no sink — emit is a no-op, never raises
    emit_progress({"kind": "phase", "phase": "y"})
    assert got == [{"kind": "phase", "phase": "x"}]


def test_progress_sink_swallows_errors():
    from langclaw.workflows.progress import emit_progress, reset_progress_sink, set_progress_sink

    def boom(_e):
        raise RuntimeError("sink blew up")

    token = set_progress_sink(boom)
    try:
        emit_progress({"k": 1})  # must NOT propagate into the workflow
    finally:
        reset_progress_sink(token)


async def test_runtime_emits_phase_progress():
    """A python workflow's ctx.phase() calls surface as phase progress events."""
    from langclaw.config.schema import WorkflowsConfig
    from langclaw.workflows import WorkflowRuntime, WorkflowSpec
    from langclaw.workflows.progress import reset_progress_sink, set_progress_sink

    events: list = []
    token = set_progress_sink(events.append)
    try:

        async def body(ctx, inp):
            ctx.phase("gather")
            ctx.phase("synthesize")
            return "ok"

        rt = WorkflowRuntime(WorkflowsConfig(enabled=True))

        async def executor(req):
            return None

        await rt.start_run(
            WorkflowSpec(name="r", fn=body, description="d"), None, run_id="r1", executor=executor
        )
    finally:
        reset_progress_sink(token)

    phases = [(e["workflow"], e["phase"]) for e in events if e["kind"] == "phase"]
    assert phases == [("r", "gather"), ("r", "synthesize")]


async def test_runtime_emits_authored_script_progress():
    """A Mode-2 run surfaces the authored body as an 'authored' event (for UI)."""
    from langclaw.config.schema import WorkflowsConfig
    from langclaw.workflows import WorkflowRuntime, WorkflowSpec
    from langclaw.workflows.progress import reset_progress_sink, set_progress_sink

    events: list = []
    token = set_progress_sink(events.append)
    try:
        spec = WorkflowSpec(name="r", fn=lambda c, i: None, description="d", mode="llm_authored")
        rt = WorkflowRuntime(WorkflowsConfig(enabled=True))

        async def author(s, i):
            return "BODY-SCRIPT"

        async def runner(s, i):
            return "done"

        await rt.start_run(spec, None, run_id="r1", author=author, script_runner=runner)
    finally:
        reset_progress_sink(token)

    authored = [e for e in events if e["kind"] == "authored"]
    assert authored and authored[0]["script"] == "BODY-SCRIPT" and authored[0]["workflow"] == "r"


def test_render_workflow_progress():
    from langclaw.workflows.progress import render_workflow_progress

    assert render_workflow_progress({"kind": "phase", "workflow": "r", "phase": "gather"}) == (
        "⚙️ r: gather"
    )
    out = render_workflow_progress({"kind": "authored", "workflow": "r", "script": "X();"})
    assert out is not None and "generated script" in out and "X();" in out
    assert render_workflow_progress({"kind": "other"}) is None


# ---------------------------------------------------------------------------
# 6 — ctx.parallel failure isolation (Tier 1a)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_parallel_fail_fast_by_default():
    """One failing branch propagates out of the whole batch (default)."""
    from langclaw.workflows import WorkflowContext

    async def _exec(request):
        if request.target == "boom":
            raise RuntimeError("kaboom")
        return f"ok:{request.target}"

    ctx = WorkflowContext(executor=_exec)
    ctx.phase("fanout")

    async def good(c):
        return await c.tool("fine")

    async def bad(c):
        return await c.tool("boom")

    with pytest.raises(RuntimeError):
        await ctx.parallel([good, bad, good])


@pytest.mark.asyncio
async def test_parallel_isolates_failures_when_requested():
    """return_exceptions=True: a bad branch yields the exception, others survive."""
    from langclaw.workflows import WorkflowContext

    async def _exec(request):
        if request.target == "boom":
            raise RuntimeError("kaboom")
        return f"ok:{request.target}"

    ctx = WorkflowContext(executor=_exec)
    ctx.phase("fanout")

    async def good(c):
        return await c.tool("fine")

    async def bad(c):
        return await c.tool("boom")

    results = await ctx.parallel([good, bad, good], return_exceptions=True)
    assert results[0] == "ok:fine"
    assert isinstance(results[1], RuntimeError)
    assert results[2] == "ok:fine"
    # the survivors are filterable the obvious way
    survivors = [r for r in results if not isinstance(r, Exception)]
    assert survivors == ["ok:fine", "ok:fine"]


# ---------------------------------------------------------------------------
# 7 — ctx.log narrator (Tier 1c)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ctx_log_invokes_log_callback():
    """ctx.log forwards a free-text line to the injected log callback."""
    from langclaw.workflows import WorkflowContext

    async def _exec(request):
        return "ok"

    lines: list[str] = []
    ctx = WorkflowContext(executor=_exec, log_cb=lines.append)
    ctx.log("3/10 sources fetched")
    assert lines == ["3/10 sources fetched"]


@pytest.mark.asyncio
async def test_ctx_log_propagates_into_parallel_branches():
    """Child contexts spawned by parallel share the parent log callback."""
    from langclaw.workflows import WorkflowContext

    async def _exec(request):
        return "ok"

    lines: list[str] = []
    ctx = WorkflowContext(executor=_exec, log_cb=lines.append)

    async def branch(c):
        c.log("branch ran")
        return await c.tool("t")

    await ctx.parallel([branch, branch])
    assert lines == ["branch ran", "branch ran"]


@pytest.mark.asyncio
async def test_ctx_log_no_callback_is_noop():
    from langclaw.workflows import WorkflowContext

    async def _exec(request):
        return "ok"

    ctx = WorkflowContext(executor=_exec)
    ctx.log("nobody listening")  # must not raise


@pytest.mark.asyncio
async def test_runtime_emits_log_progress_event():
    """The runtime wires ctx.log to a 'log' progress event (workflow + run_id)."""
    from langclaw.config.schema import WorkflowsConfig
    from langclaw.workflows import WorkflowRuntime, WorkflowSpec
    from langclaw.workflows.progress import reset_progress_sink, set_progress_sink

    async def body(ctx, inp):
        ctx.log("halfway")
        return "done"

    spec = WorkflowSpec(name="logger", description="", fn=body)
    rt = WorkflowRuntime(WorkflowsConfig(enabled=True))

    async def _exec(request):
        return "x"

    events: list[dict] = []
    token = set_progress_sink(events.append)
    try:
        await rt.start_run(spec, None, run_id="run-log-1", executor=_exec)
    finally:
        reset_progress_sink(token)

    logs = [e for e in events if e["kind"] == "log"]
    assert logs and logs[0]["message"] == "halfway"
    assert logs[0]["workflow"] == "logger" and logs[0]["run_id"] == "run-log-1"


def test_render_log_progress():
    from langclaw.workflows.progress import render_workflow_progress

    out = render_workflow_progress({"kind": "log", "workflow": "r", "message": "step 2 done"})
    assert out is not None and "step 2 done" in out


# ---------------------------------------------------------------------------
# 8 — Store-backed durable StepStore (#5)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_store_step_store_round_trips_via_basestore():
    """StoreStepStore satisfies the StepStore protocol over a langgraph BaseStore."""
    from langgraph.store.memory import InMemoryStore

    from langclaw.workflows.resume import StepStore
    from langclaw.workflows.step_store import StoreStepStore

    ss = StoreStepStore(InMemoryStore())
    assert isinstance(ss, StepStore)  # structural / runtime_checkable

    hit, _ = await ss.get("run1", "step#0")
    assert hit is False

    await ss.put("run1", "step#0", {"answer": 42})
    hit, value = await ss.get("run1", "step#0")
    assert hit is True and value == {"answer": 42}


@pytest.mark.asyncio
async def test_store_step_store_namespaces_by_run():
    """Distinct run_ids never share cached step results."""
    from langgraph.store.memory import InMemoryStore

    from langclaw.workflows.step_store import StoreStepStore

    ss = StoreStepStore(InMemoryStore())
    await ss.put("runA", "s#0", "A-result")
    await ss.put("runB", "s#0", "B-result")

    assert (await ss.get("runA", "s#0")) == (True, "A-result")
    assert (await ss.get("runB", "s#0")) == (True, "B-result")


@pytest.mark.asyncio
async def test_store_step_store_drives_resume_replay():
    """Wired as the runtime's step_store, completed steps replay (no re-run)."""
    from langgraph.store.memory import InMemoryStore

    from langclaw.config.schema import WorkflowsConfig
    from langclaw.workflows import WorkflowRuntime, WorkflowSpec
    from langclaw.workflows.step_store import StoreStepStore

    calls: list[str] = []

    async def body(ctx, inp):
        a = await ctx.tool("t1")
        b = await ctx.tool("t2")
        return f"{a}+{b}"

    spec = WorkflowSpec(name="resumable", description="", fn=body)

    async def _exec(request):
        calls.append(request.target)
        return request.target.upper()

    store = StoreStepStore(InMemoryStore())
    rt = WorkflowRuntime(WorkflowsConfig(enabled=True), step_store=store)

    out1 = await rt.start_run(spec, None, run_id="dup", executor=_exec)
    out2 = await rt.start_run(spec, None, run_id="dup", executor=_exec)

    assert out1 == out2 == "T1+T2"
    # second run replayed both steps from the store — executor not called again
    assert calls == ["t1", "t2"]


@pytest.mark.asyncio
async def test_make_step_store_backend_sqlite(tmp_path):
    """The factory yields a lifecycle-managed sqlite-backed StepStore."""
    from langclaw.workflows.step_store import StoreStepStore, make_step_store_backend

    db = tmp_path / "steps.db"
    backend = make_step_store_backend("sqlite", db_path=str(db))
    async with backend:
        ss = StoreStepStore(backend.get_store())
        await ss.put("r", "s#0", {"v": 1})
        assert (await ss.get("r", "s#0")) == (True, {"v": 1})


def test_make_step_store_backend_rejects_unknown():
    from langclaw.workflows.step_store import make_step_store_backend

    with pytest.raises(ValueError):
        make_step_store_backend("redis")


def test_workflows_config_durable_steps_flag():
    from langclaw.config.schema import WorkflowsConfig

    assert WorkflowsConfig().durable_steps is False
    assert WorkflowsConfig(durable_steps=True).durable_steps is True


# ---------------------------------------------------------------------------
# 9 — Run journal + resume trigger
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_store_records_lifecycle_and_lists_incomplete():
    from langgraph.store.memory import InMemoryStore

    from langclaw.workflows.run_store import StoreRunStore

    rs = StoreRunStore(InMemoryStore())
    await rs.mark_running("r1", "demo", {"q": 1})
    await rs.mark_running("r2", "demo", {"q": 2})
    await rs.mark_completed("r2")
    await rs.mark_running("r3", "demo", {"q": 3})
    await rs.mark_failed("r3")

    incomplete = await rs.list_incomplete()
    ids = {r["run_id"] for r in incomplete}
    assert ids == {"r1"}  # completed + failed are excluded
    rec = next(r for r in incomplete if r["run_id"] == "r1")
    assert rec["spec_name"] == "demo" and rec["input"] == {"q": 1}


@pytest.mark.asyncio
async def test_runtime_marks_run_completed():
    from langgraph.store.memory import InMemoryStore

    from langclaw.config.schema import WorkflowsConfig
    from langclaw.workflows import WorkflowRuntime, WorkflowSpec
    from langclaw.workflows.run_store import StoreRunStore

    async def body(ctx, inp):
        return await ctx.tool("t")

    async def _exec(req):
        return "ok"

    rs = StoreRunStore(InMemoryStore())
    rt = WorkflowRuntime(WorkflowsConfig(enabled=True), run_store=rs)
    await rt.start_run(
        WorkflowSpec(name="d", description="", fn=body), None, run_id="x", executor=_exec
    )

    assert await rs.list_incomplete() == []  # marked completed, not incomplete


@pytest.mark.asyncio
async def test_runtime_marks_run_failed_on_exception():
    from langgraph.store.memory import InMemoryStore

    from langclaw.config.schema import WorkflowsConfig
    from langclaw.workflows import WorkflowRuntime, WorkflowSpec
    from langclaw.workflows.run_store import StoreRunStore

    async def body(ctx, inp):
        raise RuntimeError("boom")

    async def _exec(req):
        return "ok"

    rs = StoreRunStore(InMemoryStore())
    rt = WorkflowRuntime(WorkflowsConfig(enabled=True), run_store=rs)
    with pytest.raises(RuntimeError):
        await rt.start_run(
            WorkflowSpec(name="d", description="", fn=body), None, run_id="x", executor=_exec
        )

    # a deterministic failure is marked failed, NOT left as resumable
    assert await rs.list_incomplete() == []


@pytest.mark.asyncio
async def test_resume_incomplete_reruns_only_unfinished_tail():
    """A crashed run (step-1 done, never completed) resumes: step-1 replays, step-2 runs."""
    from langgraph.store.memory import InMemoryStore

    from langclaw.config.schema import WorkflowsConfig
    from langclaw.workflows import WorkflowRuntime, WorkflowSpec
    from langclaw.workflows.run_store import StoreRunStore
    from langclaw.workflows.step_store import StoreStepStore

    store = InMemoryStore()
    step_store = StoreStepStore(store)
    run_store = StoreRunStore(store)

    executed: list[str] = []
    fail = {"step-2": True}

    async def body(ctx, inp):
        a = await ctx.tool("step-1")
        b = await ctx.tool("step-2")
        return f"{a}+{b}"

    async def _exec(req):
        if req.target == "step-2" and fail["step-2"]:
            raise RuntimeError("crash")
        executed.append(req.target)
        return req.target.upper()

    spec = WorkflowSpec(name="demo", description="", fn=body)
    rt = WorkflowRuntime(WorkflowsConfig(enabled=True), step_store=step_store, run_store=run_store)

    # Attempt 1 executes step-1 (persisted) then dies in step-2.
    try:
        await rt.start_run(spec, {"in": 1}, run_id="run-1", executor=_exec)
    except RuntimeError:
        pass

    # A real process kill leaves status="running"; the clean exception above
    # marked it failed, so re-set running to model the crash.
    await run_store.mark_running("run-1", "demo", {"in": 1})

    fail["step-2"] = False  # the transient cause is gone on restart
    resumed = await rt.resume_incomplete(
        get_spec=lambda name: spec if name == "demo" else None,
        make_executor=lambda _spec: _exec,
    )

    assert resumed == ["run-1"]
    assert executed == ["step-1", "step-2"]  # step-1 once (1st attempt), step-2 once (resume)
    assert await run_store.list_incomplete() == []  # completed after resume


def test_workflows_config_resume_on_startup_flag():
    from langclaw.config.schema import WorkflowsConfig

    assert WorkflowsConfig().resume_on_startup is False
    assert WorkflowsConfig(resume_on_startup=True).resume_on_startup is True


@pytest.mark.asyncio
async def test_runtime_rejects_non_serializable_input_for_journal():
    """A non-JSON input fails clearly at journaling, not deep inside the store."""
    from langgraph.store.memory import InMemoryStore

    from langclaw.config.schema import WorkflowsConfig
    from langclaw.workflows import WorkflowRuntime, WorkflowSpec
    from langclaw.workflows.run_store import StoreRunStore

    async def body(ctx, inp):
        return "ok"

    async def _exec(req):
        return "ok"

    rt = WorkflowRuntime(WorkflowsConfig(enabled=True), run_store=StoreRunStore(InMemoryStore()))
    with pytest.raises(TypeError, match="not JSON-serializable"):
        await rt.start_run(
            WorkflowSpec(name="d", description="", fn=body),
            {"bad": {1, 2, 3}},  # a set is not JSON-serializable
            run_id="x",
            executor=_exec,
        )


def test_run_store_satisfies_protocol():
    from langgraph.store.memory import InMemoryStore

    from langclaw.workflows.run_store import RunStore, StoreRunStore

    assert isinstance(StoreRunStore(InMemoryStore()), RunStore)


# ---------------------------------------------------------------------------
# 6 — Workflows as a bus-native message source (#48) + run_registered (#46)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_store_list_all_returns_recent_runs():
    """list_all surfaces every run (any status), newest-first, for /workflow runs."""
    from langgraph.store.memory import InMemoryStore

    from langclaw.workflows.run_store import StoreRunStore

    rs = StoreRunStore(InMemoryStore())
    await rs.mark_running("r1", "demo", {"q": 1})
    await rs.mark_completed("r1")
    await rs.mark_running("r2", "demo", {"q": 2})
    await rs.mark_failed("r2")
    await rs.mark_running("r3", "demo", {"q": 3})

    runs = await rs.list_all()
    statuses = {r["run_id"]: r["status"] for r in runs}
    assert statuses == {"r1": "completed", "r2": "failed", "r3": "running"}


@pytest.mark.asyncio
async def test_run_registered_python_uses_registered_executor_factory():
    """run_registered builds the executor from the registered factory (bus dispatch)."""
    from langclaw.config.schema import WorkflowsConfig
    from langclaw.workflows import WorkflowRuntime, WorkflowSpec

    calls: list[str] = []

    async def body(ctx, inp):
        return await ctx.tool("greet")

    async def _exec(req):
        calls.append(req.target)
        return "hi"

    rt = WorkflowRuntime(WorkflowsConfig(enabled=True))
    rt.set_resume_executor_factory(lambda _rt: _exec)

    spec = WorkflowSpec(name="demo", description="", fn=body)
    out = await rt.run_registered(spec, None, run_id="rr-1")
    assert out == "hi"
    assert calls == ["greet"]


@pytest.mark.asyncio
async def test_run_registered_llm_authored_uses_authoring_factories():
    """run_registered routes llm_authored specs through the registered author/runner."""
    from langclaw.config.schema import WorkflowsConfig
    from langclaw.workflows import WorkflowRuntime, WorkflowSpec

    async def _author(spec, inp):
        return "SCRIPT_BODY"

    async def _runner(script, inp):
        return {"ran": script}

    rt = WorkflowRuntime(WorkflowsConfig(enabled=True))
    rt.set_authoring_factories(
        author_factory=lambda _spec: _author,
        script_runner_factory=lambda _spec: _runner,
    )

    spec = WorkflowSpec(
        name="auth", description="author a body", fn=lambda c, i: None, mode="llm_authored"
    )
    out = await rt.run_registered(spec, {"x": 1}, run_id="rr-2")
    assert out == {"ran": "SCRIPT_BODY"}


@pytest.mark.asyncio
async def test_run_registered_without_factory_raises():
    from langclaw.config.schema import WorkflowsConfig
    from langclaw.workflows import WorkflowRuntime, WorkflowSpec

    rt = WorkflowRuntime(WorkflowsConfig(enabled=True))
    spec = WorkflowSpec(name="demo", description="", fn=lambda c, i: None)
    with pytest.raises(RuntimeError, match="no resume executor factory"):
        await rt.run_registered(spec, None, run_id="rr-3")


@pytest.mark.asyncio
async def test_resume_incomplete_resumes_llm_authored_with_script_store():
    """With authoring factories + a script store, a crashed llm_authored run resumes,
    replaying its frozen body rather than re-authoring (#46)."""
    from langgraph.store.memory import InMemoryStore

    from langclaw.config.schema import WorkflowsConfig
    from langclaw.workflows import WorkflowRuntime, WorkflowSpec
    from langclaw.workflows.authored import StoreScriptStore
    from langclaw.workflows.run_store import StoreRunStore

    store = InMemoryStore()
    run_store = StoreRunStore(store)
    script_store = StoreScriptStore(store)

    author_calls: list[str] = []

    async def _author(spec, inp):
        author_calls.append(spec.name)
        return "FROZEN_BODY"

    runs: list[str] = []

    async def _runner(script, inp):
        runs.append(script)
        return {"body": script}

    rt = WorkflowRuntime(
        WorkflowsConfig(enabled=True), run_store=run_store, script_store=script_store
    )
    rt.set_authoring_factories(
        author_factory=lambda _spec: _author,
        script_runner_factory=lambda _spec: _runner,
    )

    spec = WorkflowSpec(
        name="auth", description="author a body", fn=lambda c, i: None, mode="llm_authored"
    )

    # First run authors + freezes the body.
    await rt.run_registered(spec, {"in": 1}, run_id="run-A")
    assert author_calls == ["auth"]

    # Model a crash: mark it running again.
    await run_store.mark_running("run-A", "auth", {"in": 1})

    resumed = await rt.resume_incomplete(get_spec=lambda n: spec if n == "auth" else None)
    assert resumed == ["run-A"]
    # Author NOT called again — frozen body replayed from the script store.
    assert author_calls == ["auth"]
    assert runs == ["FROZEN_BODY", "FROZEN_BODY"]
