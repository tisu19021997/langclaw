"""Prove startup resume: a run killed mid-flight resumes on the next boot.

Run it twice in separate processes (the "restart"):

    uv run python showboats/pr-44/prove_startup_resume.py   # boot 1: crashes in step-2
    uv run python showboats/pr-44/prove_startup_resume.py   # boot 2: resumes, runs only step-2

Boot 1 leaves the run "running" in the journal with step-1 persisted. Boot 2
calls resume_incomplete() — step-1 replays from the store, only step-2 executes.
Delete the DB to reset.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from langclaw.config.schema import WorkflowsConfig
from langclaw.workflows import WorkflowRuntime, WorkflowSpec
from langclaw.workflows.run_store import StoreRunStore
from langclaw.workflows.step_store import StoreStepStore, make_step_store_backend

DB_PATH = "/tmp/langclaw_resume_demo.db"
RUN_ID = "boot-demo"


async def body(ctx, inp):
    a = await ctx.tool("step-1")
    b = await ctx.tool("step-2")
    return f"{a}+{b}"


async def main() -> None:
    spec = WorkflowSpec(name="demo", description="", fn=body)
    backend = make_step_store_backend("sqlite", db_path=DB_PATH)
    executed: list[str] = []

    async with backend:
        store = backend.get_store()
        run_store = StoreRunStore(store)
        runtime = WorkflowRuntime(
            WorkflowsConfig(enabled=True),
            step_store=StoreStepStore(store),
            run_store=run_store,
        )

        incomplete = await run_store.list_incomplete()
        first_boot = not incomplete

        async def executor(req):
            # On boot 1, step-2 "crashes". On boot 2 (resume), it succeeds.
            if req.target == "step-2" and first_boot:
                raise RuntimeError("process killed during step-2")
            executed.append(req.target)
            return req.target.upper()

        if first_boot:
            print("boot 1: fresh run")
            try:
                await runtime.start_run(spec, {"in": 1}, run_id=RUN_ID, executor=executor)
            except RuntimeError as exc:
                print(f"  crashed: {exc}")
            # A real kill leaves it 'running'; the clean exception marked it
            # failed, so re-set running to model the crash.
            await run_store.mark_running(RUN_ID, "demo", {"in": 1})
            print(f"  executed={executed}  (step-1 persisted, run left running)")
        else:
            print(f"boot 2: found {len(incomplete)} incomplete run(s) — resuming")
            resumed = await runtime.resume_incomplete(
                get_spec=lambda name: spec if name == "demo" else None,
                make_executor=lambda _spec: executor,
            )
            print(f"  resumed={resumed}  executed={executed}  (only the tail ran)")
            print(f"  incomplete now: {await run_store.list_incomplete()}")


if __name__ == "__main__":
    asyncio.run(main())
    print(f"\nReset with:  rm {Path(DB_PATH)}")
