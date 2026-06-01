"""Prove workflows are now a first-class bus message source (#48) + the
/workflows command surface (#47), end-to-end, with no LLM or network.

What it shows:
  1. Publishing an InboundMessage(origin="workflow") runs the named workflow
     through GatewayManager._handle and delivers the output to the channel —
     the same path a cron-fired job or `/workflows run` takes.
  2. A cron job stamped with workflow_name fires origin="workflow" (no agent
     prompt wrapper).
  3. /workflows list | run | runs | status against a live runtime + journal.

    uv run python showboats/workflows-as-source/prove_bus_dispatch.py
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

from langclaw.bus.base import InboundMessage
from langclaw.config.schema import WorkflowsConfig
from langclaw.cron import scheduler as sched
from langclaw.gateway.commands import CommandContext
from langclaw.gateway.manager import GatewayManager
from langclaw.workflows import WorkflowRegistry, WorkflowRuntime, WorkflowSpec
from langclaw.workflows.run_store import StoreRunStore


class Channel:
    name = "websocket"

    def __init__(self) -> None:
        self.sent: list = []

    def is_enabled(self) -> bool:
        return True

    async def send(self, m) -> None:
        self.sent.append(m)
        tag = m.type if m.type != "ai" else "AI →"
        print(f"   [{tag}] {m.content}")


class Bus:
    def __init__(self) -> None:
        self._mgr = None

    def bind(self, mgr) -> None:
        self._mgr = mgr

    async def publish(self, msg) -> None:
        # In the real gateway the bus worker dispatches; here we call directly.
        await self._mgr._handle(msg)


async def main() -> None:
    reg = WorkflowRegistry()

    async def report(ctx, inp):
        ctx.phase("gather")
        ctx.log(f"input = {inp}")
        ctx.phase("write")
        return {"report": f"daily summary for {inp.get('topic', '?')}"}

    reg.register(WorkflowSpec(name="report", description="build a daily report", fn=report))

    from langgraph.store.memory import InMemoryStore

    run_store = StoreRunStore(InMemoryStore())
    runtime = WorkflowRuntime(WorkflowsConfig(enabled=True), run_store=run_store)

    async def _exec(_req):  # this workflow issues no tool steps
        return None

    runtime.set_resume_executor_factory(lambda _rt: _exec)

    bus = Bus()
    config = MagicMock()
    config.permissions.enabled = False
    cp = MagicMock()
    cp.get.return_value = MagicMock()

    mgr = GatewayManager(
        config=config,
        bus=bus,
        checkpointer_backend=cp,
        agent=MagicMock(),
        channels=[Channel()],
        workflow_runtime=runtime,
        workflow_registry=reg,
        workflow_run_store=run_store,
    )
    bus.bind(mgr)

    print("1) Publish InboundMessage(origin='workflow') → gateway runs it:")
    await mgr._handle(
        InboundMessage(
            channel="websocket",
            user_id="u1",
            context_id="c1",
            chat_id="c1",
            content="",
            origin="workflow",
            metadata={"workflow_name": "report", "workflow_input": '{"topic": "AI"}'},
        )
    )
    await asyncio.sleep(0.05)  # let fire-and-forget progress sends flush

    print("\n2) Cron-fired workflow → _fire_job publishes origin='workflow':")

    class _Mgr:
        def __init__(self, b):
            self._bus = b

    captured: list = []

    class _CaptureBus:
        async def publish(self, m):
            captured.append(m)

    sched._MANAGERS["demo"] = _Mgr(_CaptureBus())  # type: ignore[assignment]
    try:
        await sched._fire_job(
            manager_id="demo",
            message="ignored for workflow jobs",
            channel="websocket",
            user_id="u1",
            context_id="c1",
            chat_id="c1",
            job_name="nightly report",
            workflow_name="report",
            workflow_input='{"topic": "markets"}',
        )
    finally:
        sched._MANAGERS.pop("demo", None)
    fired = captured[0]
    print(f"   origin={fired.origin!r}  workflow_name={fired.metadata['workflow_name']!r}")

    print("\n3) /workflows command surface:")

    def ctx(*args):
        return CommandContext(
            channel="websocket", user_id="u1", context_id="c1", chat_id="c1", args=list(args)
        )

    for args in (("list",), ("run", "report", '{"topic":"ops"}'), ("runs",)):
        out = await mgr._command_router.dispatch("workflows", ctx(*args))
        print(f"   /workflows {' '.join(args)}\n     " + out.replace("\n", "\n     "))
    await asyncio.sleep(0.05)

    print("\nDONE — workflows are reachable from the bus, cron, and /workflows.")


if __name__ == "__main__":
    asyncio.run(main())
