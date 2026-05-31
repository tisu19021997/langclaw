"""Prove partial resume: a run that dies after step-1 resumes at step-2.

Same run_id, two attempts:
  attempt 1 — step-1 succeeds, step-2 raises  → only step-1 is persisted
  attempt 2 — step-1 REPLAYS from store, step-2 runs for the first time

    uv run python showboats/prove_partial_resume.py
"""

from __future__ import annotations

import asyncio

from langclaw.config.schema import WorkflowsConfig
from langclaw.workflows import WorkflowRuntime, WorkflowSpec
from langclaw.workflows.step_store import StoreStepStore, make_step_store_backend

RUN_ID = "partial-demo"
fail_step2 = True  # flipped to False before the second attempt


async def body(ctx, inp):
    a = await ctx.tool("step-1")
    b = await ctx.tool("step-2")
    return f"{a}+{b}"


async def main() -> None:
    executed: list[str] = []

    async def executor(req):
        if req.target == "step-2" and fail_step2:
            raise RuntimeError("crash before step-2 finishes")
        executed.append(req.target)
        return req.target.upper()

    spec = WorkflowSpec(name="demo", description="", fn=body)
    # in-memory store kept across both attempts (stands in for a persisted DB)
    backend = make_step_store_backend("memory")
    async with backend:
        runtime = WorkflowRuntime(
            WorkflowsConfig(enabled=True),
            step_store=StoreStepStore(backend.get_store()),
        )

        executed.clear()
        try:
            await runtime.start_run(spec, None, run_id=RUN_ID, executor=executor)
        except RuntimeError as exc:
            print(f"attempt 1: executed={executed}  crashed: {exc}")

        global fail_step2
        fail_step2 = False
        executed.clear()
        out = await runtime.start_run(spec, None, run_id=RUN_ID, executor=executor)
        print(f"attempt 2: executed={executed}  output={out}")
        print("→ step-1 replayed from store; only step-2 ran the 2nd time")


if __name__ == "__main__":
    asyncio.run(main())
