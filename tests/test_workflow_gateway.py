"""Workflows as a bus-native message source (#48) + /workflow commands (#47).

Exercises the gateway dispatch path (origin="workflow") and the /workflow command
closure against a real WorkflowRuntime + registry with a fake channel.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from langclaw.bus.base import InboundMessage
from langclaw.config.schema import WorkflowsConfig
from langclaw.gateway.commands import CommandContext
from langclaw.gateway.manager import GatewayManager
from langclaw.workflows import WorkflowRegistry, WorkflowRuntime, WorkflowSpec


class _FakeChannel:
    name = "websocket"

    def __init__(self) -> None:
        self.sent: list = []

    def is_enabled(self) -> bool:
        return True

    async def send(self, m) -> None:
        self.sent.append(m)


def _make_registry() -> WorkflowRegistry:
    reg = WorkflowRegistry()

    async def echo_body(ctx, inp):
        ctx.phase("run")
        ctx.log("echoing")
        return {"echo": inp}

    async def boom_body(ctx, inp):
        raise RuntimeError("kaboom")

    reg.register(WorkflowSpec(name="echo", description="echo input", fn=echo_body))
    reg.register(WorkflowSpec(name="boom", description="always fails", fn=boom_body))
    return reg


def _make_manager(registry, *, run_store=None):
    runtime = WorkflowRuntime(WorkflowsConfig(enabled=True), run_store=run_store)

    async def _exec(req):  # echo/boom bodies issue no tool steps
        return None

    runtime.set_resume_executor_factory(lambda _rt: _exec)

    config = MagicMock()
    config.permissions.enabled = False
    checkpointer = MagicMock()
    checkpointer.get.return_value = MagicMock()

    mgr = GatewayManager(
        config=config,
        bus=AsyncMock(),
        checkpointer_backend=checkpointer,
        agent=MagicMock(),
        channels=[_FakeChannel()],
        workflow_runtime=runtime,
        workflow_registry=registry,
        workflow_run_store=run_store,
    )
    return mgr


def _wf_msg(name: str, **meta) -> InboundMessage:
    return InboundMessage(
        channel="websocket",
        user_id="u1",
        context_id="c1",
        chat_id="c1",
        content="",
        origin="workflow",
        metadata={"workflow_name": name, **meta},
    )


# --- gateway dispatch -------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_routes_workflow_origin_and_delivers_output():
    registry = _make_registry()
    mgr = _make_manager(registry)
    channel = mgr._channel_map["websocket"]

    await mgr._handle(_wf_msg("echo", workflow_input='{"q": 1}'))

    ai = [m for m in channel.sent if m.type == "ai"]
    assert ai, "expected a final AI message with the workflow output"
    assert '"echo"' in ai[-1].content
    assert '"q": 1' in ai[-1].content
    assert ai[-1].metadata.get("origin") == "workflow"


@pytest.mark.asyncio
async def test_handle_workflow_unknown_name_reports_error():
    mgr = _make_manager(_make_registry())
    channel = mgr._channel_map["websocket"]

    await mgr._handle(_wf_msg("does_not_exist"))

    assert any("Unknown workflow" in m.content for m in channel.sent)


@pytest.mark.asyncio
async def test_handle_workflow_failure_is_delivered_not_raised():
    mgr = _make_manager(_make_registry())
    channel = mgr._channel_map["websocket"]

    await mgr._handle(_wf_msg("boom"))

    assert any("failed" in m.content for m in channel.sent)


@pytest.mark.asyncio
async def test_workflow_progress_projected_to_channel():
    mgr = _make_manager(_make_registry())
    channel = mgr._channel_map["websocket"]

    await mgr._handle(_wf_msg("echo"))
    await asyncio.sleep(0.05)  # progress sink sends are fire-and-forget tasks
    # phase + log events render as tool_progress lines
    progress = [m for m in channel.sent if m.type == "tool_progress"]
    assert any("run" in m.content for m in progress)


# --- /workflow command ------------------------------------------------------


def _ctx(*args) -> CommandContext:
    return CommandContext(
        channel="websocket", user_id="u1", context_id="c1", chat_id="c1", args=list(args)
    )


@pytest.mark.asyncio
async def test_workflow_command_list():
    mgr = _make_manager(_make_registry())
    out = await mgr._command_router.dispatch("workflow", _ctx())
    assert "echo" in out and "boom" in out


@pytest.mark.asyncio
async def test_workflow_command_run_publishes_to_bus():
    mgr = _make_manager(_make_registry())
    out = await mgr._command_router.dispatch("workflow", _ctx("run", "echo", '{"q":2}'))
    assert "Started workflow" in out
    # Published an origin=workflow message
    published = mgr._bus.publish.call_args[0][0]
    assert published.origin == "workflow"
    assert published.metadata["workflow_name"] == "echo"
    assert published.metadata["workflow_input"] == '{"q":2}'


@pytest.mark.asyncio
async def test_workflow_command_run_unknown():
    mgr = _make_manager(_make_registry())
    out = await mgr._command_router.dispatch("workflow", _ctx("run", "nope"))
    assert "Unknown workflow" in out


@pytest.mark.asyncio
async def test_workflow_command_runs_and_status_use_journal():
    from langgraph.store.memory import InMemoryStore

    from langclaw.workflows.run_store import StoreRunStore

    run_store = StoreRunStore(InMemoryStore())
    await run_store.mark_running("echo:abc", "echo", {"q": 1})
    await run_store.mark_completed("echo:abc")

    mgr = _make_manager(_make_registry(), run_store=run_store)

    runs = await mgr._command_router.dispatch("workflow", _ctx("runs"))
    assert "echo:abc" in runs and "completed" in runs

    status = await mgr._command_router.dispatch("workflow", _ctx("status", "echo:abc"))
    assert "completed" in status


@pytest.mark.asyncio
async def test_workflow_command_cancel_no_live_run():
    mgr = _make_manager(_make_registry())
    out = await mgr._command_router.dispatch("workflow", _ctx("cancel", "missing:run"))
    assert "No live run" in out


# --- RBAC + dedup -----------------------------------------------------------


def _make_manager_with_perms(registry, perms):
    runtime = WorkflowRuntime(WorkflowsConfig(enabled=True))

    async def _exec(req):
        return None

    runtime.set_resume_executor_factory(lambda _rt: _exec)

    config = MagicMock()
    config.permissions = perms
    checkpointer = MagicMock()
    checkpointer.get.return_value = MagicMock()

    return GatewayManager(
        config=config,
        bus=AsyncMock(),
        checkpointer_backend=checkpointer,
        agent=MagicMock(),
        channels=[_FakeChannel()],
        workflow_runtime=runtime,
        workflow_registry=registry,
    )


@pytest.mark.asyncio
async def test_handle_workflow_denied_for_role_without_allowlist():
    """A bus-dispatched workflow honours the default-deny workflow allowlist."""
    from langclaw.config.schema import PermissionsConfig, RoleConfig

    perms = PermissionsConfig(
        enabled=True,
        default_role="viewer",
        roles={"viewer": RoleConfig(workflows=[]), "admin": RoleConfig(workflows=["*"])},
    )
    mgr = _make_manager_with_perms(_make_registry(), perms)
    channel = mgr._channel_map["websocket"]

    # viewer (role stamped in metadata) is not allowed any workflow → denied
    await mgr._handle(_wf_msg("echo", user_role="viewer"))
    assert any("not permitted" in m.content.lower() for m in channel.sent)

    # admin has "*" → runs and delivers output
    channel.sent.clear()
    await mgr._handle(_wf_msg("echo", user_role="admin", workflow_input='{"q": 1}'))
    assert any('"echo"' in m.content for m in channel.sent)


@pytest.mark.asyncio
async def test_workflow_command_registered_when_enabled_but_empty():
    """/workflow stays discoverable with zero workflows registered (DX): the
    command is present and `list` reports none, rather than vanishing."""
    empty = WorkflowRegistry()
    config = MagicMock()
    config.permissions.enabled = False
    cp = MagicMock()
    cp.get.return_value = MagicMock()

    mgr = GatewayManager(
        config=config,
        bus=AsyncMock(),
        checkpointer_backend=cp,
        agent=MagicMock(),
        channels=[_FakeChannel()],
        workflow_runtime=None,  # no runtime yet — feature on, nothing registered
        workflow_registry=empty,
    )

    names = [c.name for c in mgr._command_router.list_commands()]
    assert "workflow" in names
    out = await mgr._command_router.dispatch("workflow", _ctx("list"))
    assert "No workflows registered" in out


@pytest.mark.asyncio
async def test_handle_workflow_rejects_duplicate_run_id():
    mgr = _make_manager(_make_registry())
    channel = mgr._channel_map["websocket"]
    mgr._workflow_runs["echo:dup"] = MagicMock()  # pretend a run is live

    await mgr._handle(_wf_msg("echo", run_id="echo:dup"))
    assert any("already in progress" in m.content for m in channel.sent)
