"""
Workflow Research — a Telegram/Discord bot that exposes an operator-authored
``@app.workflow()`` the agent can invoke as a durable, typed, multi-step routine.

Demonstrates
------------
- ``@app.workflow()``  — register a reusable orchestration (issue #38)
- Pydantic-typed input — the run boundary validates the workflow's arguments
- ``ctx.phase()``      — name the stages of the run
- ``ctx.parallel()``   — fan out over tool calls, bounded by ``max_concurrency``
- ``ctx.tool()``       — call a built-in/registered tool from inside the workflow
- RBAC ``workflows``   — the third permission axis (default-deny), per role
- Mode 1 (optional)    — with the interpreter on, the workflow is also reachable
                         from an ``eval`` script as ``tools.workflowResearch(...)``

How it differs from a subagent
------------------------------
A subagent is an LLM that improvises in an isolated context. A *workflow* is
operator-authored control flow: the steps, fan-out, and phases are fixed Python,
so the orchestration is deterministic, typed, and (in a later slice) durable and
cron-triggerable. The LLM decides *when* to run it and with *what* input — not
*how* it runs.

How the agent invokes it
------------------------
Each registered workflow is surfaced to the agent as a tool named
``workflow_<name>`` — here, ``workflow_research``. The user asks in plain
language; the model calls ``workflow_research({"topic": "..."})``; the runtime
validates the input, runs the body, and returns the result.

Run
---
1. Copy ``.env.example`` to ``.env`` and fill in at least one LLM provider key
   and one channel token (Telegram or Discord).
2. ``pip install langclaw[telegram]``   (or ``langclaw[discord]``, etc.)
3. Enable the primitive — it is **off by default**::

       export LANGCLAW__WORKFLOWS__ENABLED=true

   (Optionally enable the interpreter for the Mode-1 / PTC demo::

       pip install langclaw[interpreter]
       export LANGCLAW__INTERPRETER__ENABLED=true
   )
4. ``python examples/workflow_research.py``

Then message the bot
--------------------
- *"Run the research workflow on quantum computing"*
      → the agent calls ``workflow_research`` with ``{"topic": "quantum computing"}``;
        two web searches run in parallel, then a synthesis step.
- *"Research electric vehicles and also solar — use the workflow for each"*
      (interpreter on) → the agent writes ONE ``eval`` script that calls
      ``tools.workflowResearch(...)`` per topic (Mode 1).
"""

from __future__ import annotations

from pydantic import BaseModel

from langclaw import Langclaw

# Workflows are inert unless enabled. You can flip it here instead of via env:
#   app.config.workflows.enabled = True
# but the env var (LANGCLAW__WORKFLOWS__ENABLED=true) is the documented path.
app = Langclaw(
    system_prompt=(
        "## Research Assistant\n"
        "When the user asks you to research a topic, prefer the `research` "
        "workflow — it gathers multiple angles in parallel and synthesises a "
        "brief. For a one-off lookup, a single web_search is fine."
    ),
)


# ---------------------------------------------------------------------------
# Typed input contract — validated at the run boundary
# ---------------------------------------------------------------------------


class ResearchBrief(BaseModel):
    """Input for the ``research`` workflow."""

    topic: str
    angles: list[str] = ["overview", "risks", "recent developments"]


# ---------------------------------------------------------------------------
# The workflow — operator-authored control flow
# ---------------------------------------------------------------------------


@app.workflow(
    "research",
    input=ResearchBrief,
    description=(
        "Research a topic across several angles in parallel, then synthesise "
        "a short brief. Prefer this over ad-hoc searches for anything that "
        "benefits from multiple perspectives."
    ),
    max_concurrency=4,
)
async def research(ctx, inp: ResearchBrief) -> str:
    # Phase 1 — fan out one web search per angle, bounded by max_concurrency.
    # Each thunk takes a child context `c` and returns an awaitable; ctx.parallel
    # runs them concurrently and returns results in order. We build the thunks
    # with a small factory so each closes over its OWN angle (not the loop var).
    def _search(angle: str):
        return lambda c: c.tool("web_search", query=f"{inp.topic} {angle}")

    ctx.phase("gather")
    findings = await ctx.parallel([_search(angle) for angle in inp.angles])

    # Phase 2 — synthesise. (A ctx.subagent("writer", ...) step would route
    # through the `task` tool if you register a writer subagent; here we just
    # format the gathered findings so the example needs no extra wiring.)
    ctx.phase("synthesize")
    lines = [f"# Research brief: {inp.topic}", ""]
    for angle, result in zip(inp.angles, findings, strict=False):
        lines.append(f"## {angle.title()}")
        lines.append(str(result))
        lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# RBAC (optional) — the `workflows` axis is DEFAULT-DENY, like `subagents`.
# Without these lines and permissions disabled, every workflow is invocable.
# With permissions on, a role must explicitly list the workflow (or "*").
# ---------------------------------------------------------------------------

# app.role("analyst", tools=["*"])              # tool axis
# app.config.permissions.roles["analyst"].workflows = ["research"]  # workflow axis


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    app.run()
