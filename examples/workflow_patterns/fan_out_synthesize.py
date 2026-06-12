"""
Pattern: FAN-OUT-AND-SYNTHESIZE  —  split work across parallel branches, then merge.

Blog: "Fan-out-and-synthesize"

Real job: a competitive-landscape brief. Research each contender in its OWN subagent
— an isolated context with its own web_search — so no contender's findings colour
another's (the blog's defence against self-preferential bias). Then a single synthesis
step folds the per-contender notes into one comparison across the dimensions you care
about. Branch failures are isolated: one scout erroring doesn't sink the brief.

This is the pattern where a subagent genuinely earns its keep: each leaf does
multi-step work (search → read → summarise) with its own tools. The synthesis is a
one-shot judgment over text, so it stays a lightweight model-backed tool.

    /workflows run landscape {"subject": "agent framework",
        "contenders": ["LangGraph", "CrewAI", "AutoGen"],
        "dimensions": ["control", "durability", "learning curve"]}
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from examples.workflow_patterns._app import make_app, reasoner, say


class Landscape(BaseModel):
    subject: str = Field(description="What the contenders are competing to be.")
    contenders: list[str] = Field(description="The things to compare (2–6).")
    dimensions: list[str] = Field(
        default=["strengths", "weaknesses", "best for"],
        description="The axes to compare on.",
    )


def register(app):
    # A real subagent: isolated context, its own web_search. ctx.subagent delegates
    # to it and returns its final text.
    app.subagent(
        "scout",
        description="Research one option and report tight, sourced notes.",
        system_prompt=(
            "You research ONE option. Use web_search, then report 3–4 terse bullet "
            "findings about it — strengths, weaknesses, and what it's best for — each "
            "grounded in a source. No preamble. Do not invent facts."
        ),
        tools=["web_search"],
    )
    # The synthesis is a single judgment over the gathered notes — a model-backed
    # tool (one isolated model call), not a subagent.
    reasoner(
        app,
        "compare",
        description="Synthesise per-item research into one comparison table.",
        system=(
            "You are an industry analyst. You are given research notes for several "
            "competing options. Produce ONE markdown comparison table: a row per option, "
            "a column per requested dimension, terse cells grounded ONLY in the notes. "
            "Add a one-line 'Bottom line' under the table. Do not invent facts."
        ),
    )

    @app.workflow(
        "landscape",
        input=Landscape,
        max_concurrency=5,
        description=(
            "Competitive-landscape brief: research each contender in its own parallel "
            "subagent, then synthesise one comparison table across the given dimensions."
        ),
    )
    async def landscape(ctx, inp: Landscape) -> str:
        ctx.phase("research")

        def scout(name: str):
            # Each branch is an isolated subagent: its own search, its own context.
            return lambda c: c.subagent(
                "scout",
                f"Research '{name}' as a {inp.subject}. Focus: {', '.join(inp.dimensions)}.",
            )

        # return_exceptions=True → one failing scout yields an Exception in place
        # instead of sinking the whole fan-out.
        findings = await ctx.parallel(
            [scout(name) for name in inp.contenders], return_exceptions=True
        )

        ctx.phase("synthesize")
        blocks = []
        for name, notes in zip(inp.contenders, findings, strict=False):
            if isinstance(notes, Exception) or not say(notes):
                ctx.log(f"{name}: research failed, noting as unknown")
                blocks.append(f"### {name}\n(no usable findings)")
                continue
            ctx.log(f"{name}: scouted")
            blocks.append(f"### {name}\n{say(notes)}")

        prompt = (
            f"Subject: {inp.subject}\n"
            f"Dimensions: {', '.join(inp.dimensions)}\n\n"
            f"Notes:\n\n" + "\n\n".join(blocks)
        )
        table = say(await ctx.tool("compare", prompt=prompt))
        return f"# {inp.subject.title()} — landscape\n\n{table}"

    return app


if __name__ == "__main__":
    app = make_app(system_prompt="When asked to compare options, run the `landscape` workflow.")
    register(app)
    app.run()
