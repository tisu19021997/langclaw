"""What an end-user sees when an agent runs a workflow that uses ctx.log +
ctx.parallel(return_exceptions=True).

These are developer primitives — a chatting user never calls them directly. They
only see (a) the progress lines ctx.log/ctx.phase project to their channel, and
(b) the fact that one failing branch no longer kills the whole run.

This installs the SAME progress sink the gateway installs per message
(render_workflow_progress is exactly what a channel renders), then runs the
workflow through the runtime and prints the resulting "chat feed".

    uv run python showboats/pr-43/demo_parallel_and_log.py
"""

from __future__ import annotations

import asyncio

from langclaw.config.schema import WorkflowsConfig
from langclaw.workflows import WorkflowRuntime, WorkflowSpec
from langclaw.workflows.progress import (
    render_workflow_progress,
    reset_progress_sink,
    set_progress_sink,
)


async def body(ctx, inp):
    ctx.phase("research")
    ctx.log("searching 3 sources")

    async def search(c, source):
        if source == "flaky":
            raise RuntimeError("flaky source timed out")
        return f"hits:{source}"

    results = await ctx.parallel(
        [lambda c, s=s: search(c, s) for s in ("web", "flaky", "docs")],
        return_exceptions=True,
    )
    survivors = [r for r in results if not isinstance(r, Exception)]
    ctx.log(f"{len(survivors)}/3 sources succeeded (1 failed, run survived)")

    ctx.phase("synthesize")
    ctx.log("writing summary")
    return {"sources": survivors}


async def _unused_executor(req):
    # This workflow's parallel branches are plain coroutines, so no step ever
    # reaches the executor — but start_run requires one.
    return None


async def main() -> None:
    spec = WorkflowSpec(name="research", description="", fn=body)

    # The gateway installs a sink that renders each event into a channel line.
    feed: list[str] = []

    def channel_sink(event):
        line = render_workflow_progress(event)
        if line:
            feed.append(line)

    token = set_progress_sink(channel_sink)
    try:
        runtime = WorkflowRuntime(WorkflowsConfig(enabled=True))
        out = await runtime.start_run(spec, None, run_id="demo", executor=_unused_executor)
    finally:
        reset_progress_sink(token)

    print("---- what the user sees in chat ----")
    for line in feed:
        print(line)
    print("------------------------------------")
    print(f"final result: {out}")


if __name__ == "__main__":
    asyncio.run(main())
