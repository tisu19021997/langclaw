"""
Pattern: GENERATE-AND-FILTER  —  make many diverse candidates, keep the few that pass.

Blog: "Generate-and-filter"

Real job: a tagline studio. Generate N candidates from *deliberately different*
angles (bold, playful, technical, benefit-led, contrarian, minimalist) so the pool
is diverse rather than N variations of one idea, then score every candidate in
parallel against a rubric and return only the ones that clear the bar, ranked. The
generator and the judge are separate isolated calls — the judge never sees which
angle produced a line, so it can't play favourites.

    /workflows run tagline_studio {"product": "a durable workflow engine for AI agents",
        "audience": "Python developers", "n": 6, "keep": 3}
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from examples.workflow_patterns._app import make_app

ANGLES = ["bold", "playful", "technical", "benefit-led", "contrarian", "minimalist"]

_WRITE_SYS = (
    "You are a senior copywriter. Given a product, an audience, and a stylistic ANGLE, "
    "write exactly ONE tagline (<=10 words). No quotes, no preamble, just the line."
)
_JUDGE_SYS = "You judge marketing taglines on clarity, memorability, and fit for the audience."


class StudioBrief(BaseModel):
    product: str = Field(description="What you're naming/positioning.")
    audience: str = Field(default="developers", description="Who it's for.")
    n: int = Field(default=6, ge=2, le=6, description="Candidates to generate.")
    keep: int = Field(default=3, ge=1, le=6, description="Top candidates to return.")
    bar: int = Field(default=6, ge=0, le=10, description="Minimum score to keep.")


class Score(BaseModel):
    score: int = Field(ge=0, le=10, description="0–10 rating.")
    why: str = Field(description="One-sentence justification.")


def register(app):
    @app.workflow(
        "tagline_studio",
        input=StudioBrief,
        max_concurrency=6,
        description=(
            "Generate N taglines from diverse angles, score each against a rubric in "
            "parallel, and return the top `keep` that clear the bar, ranked."
        ),
    )
    async def tagline_studio(ctx, inp: StudioBrief) -> str:
        ctx.phase("generate")
        angles = ANGLES[: inp.n]

        def gen(angle: str):
            return lambda c: c.llm(
                f"PRODUCT: {inp.product}\nAUDIENCE: {inp.audience}\nANGLE: {angle}",
                system=_WRITE_SYS,
            )

        raw = await ctx.parallel([gen(a) for a in angles])
        candidates = [(a, r.strip()) for a, r in zip(angles, raw, strict=False) if r and r.strip()]
        ctx.log(f"generated {len(candidates)} candidates")

        ctx.phase("filter")

        def judge(line: str):
            # One structured judgment per candidate → a validated Score, no parsing.
            return lambda c: c.llm(
                f"AUDIENCE: {inp.audience}\nTAGLINE: {line}", schema=Score, system=_JUDGE_SYS
            )

        verdicts = await ctx.parallel([judge(line) for _, line in candidates])
        scored = [
            {"angle": a, "line": line, "score": v.score, "why": v.why}
            for (a, line), v in zip(candidates, verdicts, strict=False)
        ]
        scored.sort(key=lambda s: s["score"], reverse=True)
        kept = [s for s in scored if s["score"] >= inp.bar][: inp.keep]
        if not kept:  # nothing cleared the bar — still return the best, honestly flagged
            kept = scored[: inp.keep]
            ctx.log(f"none cleared the bar ({inp.bar}); returning top {len(kept)} anyway")

        ctx.phase("report")
        out = [f"# Tagline studio — top {len(kept)} of {len(scored)}", ""]
        for s in kept:
            out.append(f"**{s['score']}/10** · _{s['angle']}_ — {s['line']}")
            if s["why"]:
                out.append(f"    {s['why']}")
        return "\n".join(out)

    return app


if __name__ == "__main__":
    app = make_app(
        system_prompt="When asked for taglines or names, run the `tagline_studio` workflow."
    )
    register(app)
    app.run()
