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

This file shows two workflows over the same job:

- ``research``      — ``mode="python"`` (**recommended**): YOU author the steps.
                      Reviewed, typed, unit-testable, deterministic. When the
                      composition varies per call but the steps don't, let the
                      agent compose registered workflows via Mode 1 (PTC) instead.
- ``research_auto`` — ``mode="llm_authored"`` (**Mode 2 — experimental**): you
                      declare only the contract; the LLM authors the JS body,
                      frozen per run and run in the QuickJS sandbox over the
                      declared ``uses_tools`` allowlist. An escape hatch for
                      genuinely-variable, low-stakes, supervised tasks — the body
                      is not unit-testable and re-authors each new run. Prefer the
                      python path or Mode 1 unless you specifically need it.

Then message the bot
--------------------
- *"Run the research workflow on quantum computing"*
      → the agent calls ``workflow_research`` (python mode); web searches run in
        parallel, then a synthesis step.
- *"Use research_auto for electric vehicles"*  (interpreter extra installed)
      → the agent calls ``workflow_research_auto``; the model authors a sandboxed
        JS body that searches each angle and returns a brief (Mode 2).
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
        "THE DEFAULT research workflow — use this for any normal research "
        "request. Runs a fixed pipeline: one parallel web_search per angle, then "
        "a synthesis step. Prefer this over `research_auto`."
    ),
    max_concurrency=4,
)
async def research(ctx, inp: ResearchBrief) -> str:
    # Fan out one search per angle. The factory makes each thunk close over its
    # OWN angle (not the loop var); ctx.parallel runs them, results in order.
    def _search(angle: str):
        return lambda c: c.tool("web_search", query=f"{inp.topic} {angle}")

    ctx.phase("gather")
    findings = await ctx.parallel([_search(angle) for angle in inp.angles])

    # Synthesise. (A ctx.subagent("writer", ...) step would delegate instead.)
    ctx.phase("synthesize")
    lines = [f"# Research brief: {inp.topic}", ""]
    for angle, result in zip(inp.angles, findings, strict=False):
        lines.append(f"## {angle.title()}")
        lines.append(str(result))
        lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Mode 2 (EXPERIMENTAL) — the SAME job, but the LLM authors the body.
#
# Prefer python `research` (or Mode 1) for production: the generated body isn't
# unit-testable, re-authors each new run, and is codegen-without-review (the
# sandbox + uses_tools allowlist bound the blast radius). You declare only the
# contract — input, uses_tools, budget, and the `description` the LLM writes the
# body from; the decorated function is an unused stub. Needs the interpreter extra.
# ---------------------------------------------------------------------------


@app.workflow(
    "research_auto",
    mode="llm_authored",
    input=ResearchBrief,
    uses_tools=["web_search"],
    description=(
        "EXPERIMENTAL variant of `research` whose body is LLM-authored at runtime. "
        "Do NOT use for normal requests — prefer `research`. Only reach for this "
        "when the user explicitly asks for the auto/LLM-authored version. For each "
        "angle it searches and assembles a markdown brief."
    ),
    timeout_s=60,
)
async def research_auto(ctx, inp: ResearchBrief) -> str:  # noqa: ARG001 — body unused
    """Stub: in mode='llm_authored' the LLM writes the body; this never runs."""
    raise NotImplementedError("llm_authored workflow body is authored by the LLM")


# ---------------------------------------------------------------------------
# RBAC (optional) — the `workflows` axis is DEFAULT-DENY, like `subagents`.
#
# IMPORTANT: defining ANY role auto-enables the permissions system. Once on,
# every user resolves to `permissions.default_role` (schema default: "viewer")
# unless mapped via a channel's `user_roles`. A role that does not list the
# workflow — or an undefined role like the default "viewer" — is DENIED, and
# the `workflow_<name>` tool is stripped before the model ever sees it (so the
# agent silently falls back to a plain web_search or the `task` subagent).
#
# `app.role()` takes all three axes — tools / subagents / workflows.
# ---------------------------------------------------------------------------

app.role("analyst", tools=["*"], workflows=["research", "research_auto"])

# Make the granted role the default so a fresh Telegram/Discord user actually
# reaches the workflow. In production you would instead map specific user IDs
# to roles via `channels.<name>.user_roles`. Comment BOTH lines out to leave
# permissions disabled — then every workflow is invocable with no RBAC.
app.config.permissions.default_role = "analyst"


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    app.run()
