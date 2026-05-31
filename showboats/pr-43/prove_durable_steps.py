"""Prove the durable workflow step store survives a process restart.

The resume *trigger* isn't wired yet (run_id is freshly minted per call), so to
observe replay we drive the runtime with a FIXED run_id ourselves.

Run it twice in separate processes — that's the "restart":

    uv run python showboats/prove_durable_steps.py   # executed_steps=['t1', 't2']
    uv run python showboats/prove_durable_steps.py   # executed_steps=[]  (replayed)

Empty executed_steps on the second run means both steps came from the SQLite
store instead of re-executing. Delete the DB to reset.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from langclaw.config.schema import WorkflowsConfig
from langclaw.workflows import WorkflowRuntime, WorkflowSpec
from langclaw.workflows.step_store import StoreStepStore, make_step_store_backend

DB_PATH = "/tmp/langclaw_durable_steps_demo.db"
RUN_ID = "fixed-demo-run"


async def body(ctx, inp):
    a = await ctx.tool("t1")
    b = await ctx.tool("t2")
    return f"{a}+{b}"


async def main() -> None:
    executed: list[str] = []

    async def executor(req):  # records every step that actually runs
        executed.append(req.target)
        return req.target.upper()

    spec = WorkflowSpec(name="demo", description="", fn=body)
    backend = make_step_store_backend("sqlite", db_path=DB_PATH)
    async with backend:
        runtime = WorkflowRuntime(
            WorkflowsConfig(enabled=True),
            step_store=StoreStepStore(backend.get_store()),
        )
        output = await runtime.start_run(spec, None, run_id=RUN_ID, executor=executor)

    first_run = not executed or len(executed) == 2
    print(f"db={DB_PATH}")
    print(f"output={output}  executed_steps={executed}")
    print(
        "→ fresh run (steps executed)"
        if first_run and executed
        else "→ replayed from store (no re-execution)"
        if not executed
        else "→ partial replay"
    )


if __name__ == "__main__":
    asyncio.run(main())
    print(f"\nReset with:  rm {Path(DB_PATH)}")
