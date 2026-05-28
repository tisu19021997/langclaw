"""
Research Workflow — orchestrate named agents with a dynamic workflow.

Demonstrates
------------
- ``app.agent()`` — two specialist named agents (``researcher``, ``writer``)
  that the workflow delegates to.
- ``@app.workflow()`` — a predefined ``digest`` workflow that fans research out
  across several angles in parallel (``ctx.parallel``) and synthesises a brief
  (``ctx.run``), reporting progress with ``ctx.phase`` / ``ctx.log``.
- **Agent-spawned workflows** — registering a workflow automatically gives the
  main agent the ``run_workflow`` and ``orchestrate`` tools, so it can launch
  workflows *from chat*. When an agent-spawned run finishes, the result calls
  back into the agent (it is notified and relays it to you).
- **Cron-scheduled workflows** — the built-in ``cron`` tool gained a
  ``workflow=`` option, so the agent can schedule a workflow to run on a timer.

How a workflow is dynamic
-------------------------
The body is plain ``async`` Python, so loops, conditionals, and fan-out sized at
runtime are free. Every unit of LLM work goes through a *registered* named agent
via ``ctx.run`` — there is no sandbox and no generated code. For truly novel
shapes, the main agent can compose a step DAG on the fly with the ``orchestrate``
tool (a declarative ``WorkflowPlan``), which the built-in ``_interpret`` workflow
runs over these same named agents.

Run
---
1. Copy ``.env.example`` to ``.env`` and fill in at least one LLM provider key
   and one channel token (Telegram, Discord, or WebSocket).
2. To try the *cron-scheduled* path, also set ``LANGCLAW__CRON__ENABLED=true``.
3. ``pip install "langclaw[telegram]"``  (add ``search`` for live web research)
4. ``python examples/research_workflow.py``

Then message the bot
--------------------
- ``/workflow``                       — list registered workflows
- ``/workflow digest quantum networking``
                                      — run the digest workflow yourself; the
                                        result is delivered to the chat
- *"Research the EV battery market for me in the background."*
                                      — the agent calls ``run_workflow`` and
                                        pings you when the digest is ready
- *"Orchestrate a comparison of Postgres vs SQLite for our use case."*
                                      — the agent composes a step DAG with
                                        ``orchestrate`` (researcher → researcher
                                        → writer) and reports back
- *"Every weekday at 8am, run the digest workflow on AI-agent news."*
                                      — the agent schedules it via cron
                                        (requires ``LANGCLAW__CRON__ENABLED=true``)
"""

from __future__ import annotations

from langclaw import Langclaw, WorkflowContext

app = Langclaw(
    system_prompt=(
        "You are a research front-desk. For multi-angle research requests, prefer "
        "launching the 'digest' workflow (run_workflow) or composing a plan "
        "(orchestrate) rather than answering everything yourself. Tell the user "
        "you've started it; you'll be notified when it finishes."
    ),
)

# -- Named agents the workflow delegates to -----------------------------------
# `ctx.run(prompt, agent="researcher")` routes a step to one of these. Each runs
# on its own isolated thread, so workflow steps never pollute one another's
# history. Drop `tools=` (or omit "search" extra) to run without live web access.

app.agent(
    "researcher",
    description="Gathers specific, sourced findings on a topic",
    system_prompt=(
        "You are a meticulous researcher. Return concise, concrete findings. "
        "Cite sources when you have them; never invent citations."
    ),
    tools=["web_search", "web_fetch"],  # built-ins; require `langclaw[search]`
)

app.agent(
    "writer",
    description="Turns raw findings into a tight executive brief",
    system_prompt="You are a sharp editor. Be concise, structured, and concrete.",
)


# -- Predefined workflow: fan out, then synthesise ----------------------------


@app.workflow("digest", description="Research several angles on a topic and synthesise a brief")
async def digest(ctx: WorkflowContext) -> str:
    """Research ``ctx.input`` from a few angles in parallel, then write a brief.

    ``ctx.input`` is the text after ``/workflow digest ...`` (or the input the
    agent / cron passed). The return value is the final user-facing result.
    """
    topic = ctx.input.strip() or "the latest in AI agents"

    await ctx.phase("Plan")
    angles = ["recent news", "key players", "risks and open problems"]
    await ctx.log(f"Researching {len(angles)} angles on: {topic}")

    # No barrier inside a wave: all three research steps run concurrently.
    await ctx.phase("Research")
    findings = await ctx.parallel(
        [
            ctx.run(
                f"Research the '{angle}' angle of: {topic}. Be specific.",
                agent="researcher",
            )
            for angle in angles
        ]
    )

    await ctx.phase("Synthesise")
    notes = "\n\n".join(f"## {angle}\n{found}" for angle, found in zip(angles, findings))
    return await ctx.run(
        f"Write a tight 5-bullet executive brief on '{topic}' from these notes:\n\n{notes}",
        agent="writer",
    )


if __name__ == "__main__":
    app.run()
