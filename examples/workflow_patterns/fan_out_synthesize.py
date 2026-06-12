"""
Pattern: FAN-OUT-AND-SYNTHESIZE  —  split work across parallel branches, then merge.

Blog: "Fan-out-and-synthesize"

Real job: a competitive-landscape brief. Research each contender in its own
parallel branch (one isolated web search per contender, so no contender's findings
colour another's), then a single synthesis step folds everything into one comparison
across the dimensions you care about. Branch failures are isolated — one contender
404-ing doesn't sink the brief.

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
    reasoner(
        app,
        "compare",
        description="Synthesise per-item research into one comparison table.",
        system=(
            "You are an industry analyst. You are given raw web findings for several "
            "competing options. Produce ONE markdown comparison table: a row per option, "
            "a column per requested dimension, terse cells grounded ONLY in the findings. "
            "Add a one-line 'Bottom line' under the table. Do not invent facts."
        ),
    )

    @app.workflow(
        "landscape",
        input=Landscape,
        max_concurrency=5,
        description=(
            "Competitive-landscape brief: research each contender in parallel, then "
            "synthesise one comparison table across the given dimensions."
        ),
    )
    async def landscape(ctx, inp: Landscape) -> str:
        ctx.phase("research")

        def scout(name: str):
            # Each branch is isolated: its own search, its own slice of the brief.
            return lambda c: c.tool("web_search", query=f"{name} {inp.subject} review", n=4)

        # return_exceptions=True → one failing branch yields an Exception in place
        # instead of sinking the whole fan-out.
        findings = await ctx.parallel(
            [scout(name) for name in inp.contenders], return_exceptions=True
        )

        ctx.phase("synthesize")
        blocks = []
        for name, hits in zip(inp.contenders, findings, strict=False):
            if isinstance(hits, Exception) or not isinstance(hits, list):
                ctx.log(f"{name}: research failed, noting as unknown")
                blocks.append(f"### {name}\n(no usable findings)")
                continue
            ctx.log(f"{name}: {len(hits)} sources")
            snippets = "\n".join(
                f"- {h.get('title', '')}: {h.get('content', '')[:200]}" for h in hits[:4]
            )
            blocks.append(f"### {name}\n{snippets}")

        prompt = (
            f"Subject: {inp.subject}\n"
            f"Dimensions: {', '.join(inp.dimensions)}\n\n"
            f"Findings:\n\n" + "\n\n".join(blocks)
        )
        table = say(await ctx.tool("compare", prompt=prompt))
        return f"# {inp.subject.title()} — landscape\n\n{table}"

    return app


if __name__ == "__main__":
    app = make_app(system_prompt="When asked to compare options, run the `landscape` workflow.")
    register(app)
    app.run()
