"""
Pattern: ADVERSARIAL VERIFICATION  —  independent checkers try to REFUTE each claim.

Blog: "Adversarial verification"

Real job: fact-check a drafted answer before it ships. Decompose the draft into
atomic claims, then for each claim pull fresh evidence and ask N independent
skeptics — each prompted to *refute*, defaulting to REFUTED when unsure — to vote.
A claim survives only if it isn't out-voted. Independence is the whole game: one
self-grading pass rationalises its own draft; separate skeptics with clean contexts
and a refute-by-default bias don't.

    /workflows run fact_check {"question": "Is SQLite a good prod database?",
        "answer": "SQLite supports unlimited concurrent writers; NASA uses it for telemetry.",
        "votes": 2}
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from examples.workflow_patterns._app import make_app, pick_label, reasoner, say, split_items

VERDICTS = ["refuted", "supported", "unverifiable"]


class Draft(BaseModel):
    question: str = Field(description="What the answer was responding to.")
    answer: str = Field(description="The drafted answer to verify, claim by claim.")
    votes: int = Field(default=2, ge=1, le=4, description="Independent skeptics per claim.")


def register(app):
    reasoner(
        app,
        "extract_claims",
        description="Break an answer into atomic, checkable factual claims.",
        system=(
            "Extract the distinct, atomic, checkable factual claims from the answer. "
            "One claim per line, no numbering, no commentary. Skip opinions and hedges. "
            "Return at most 6 of the load-bearing claims."
        ),
    )
    reasoner(
        app,
        "skeptic",
        description="Adversarially judge one claim against evidence.",
        system=(
            "You are a skeptical fact-checker. You are given a CLAIM and EVIDENCE snippets. "
            "Try to REFUTE the claim. If the evidence does not clearly support it, lean "
            "REFUTED. Reply on two lines:\n"
            "VERDICT: <refuted|supported|unverifiable>\n"
            "WHY: <one sentence>"
        ),
    )

    @app.workflow(
        "fact_check",
        input=Draft,
        max_concurrency=6,
        description=(
            "Verify a drafted answer: decompose it into atomic claims, then for each "
            "claim retrieve evidence and have N independent skeptics vote refute/support. "
            "Returns a report of which claims survived."
        ),
    )
    async def fact_check(ctx, inp: Draft) -> str:
        ctx.phase("decompose")
        claims = split_items(say(await ctx.tool("extract_claims", prompt=inp.answer)), limit=6)
        if not claims:
            return "# Fact-check\n\nNo checkable claims found."
        ctx.log(f"{len(claims)} claims to verify")

        ctx.phase("verify")

        async def verify_one(c, claim: str):
            # Each claim: one evidence pull shared by its skeptics, then independent votes.
            hits = await c.tool("web_search", query=claim, n=3)
            evidence = (
                "\n".join(
                    f"- {h.get('title', '')}: {h.get('content', '')[:200]}"
                    for h in (hits if isinstance(hits, list) else [])
                )
                or "(no evidence retrieved)"
            )
            prompt = f"CLAIM: {claim}\n\nEVIDENCE:\n{evidence}"
            votes = await c.parallel(
                [lambda cc: cc.tool("skeptic", prompt=prompt) for _ in range(inp.votes)]
            )
            verdicts = [pick_label(say(v), VERDICTS, default="unverifiable") for v in votes]
            refuted = verdicts.count("refuted")
            supported = verdicts.count("supported")
            survived = supported > refuted  # ties → does not survive (refute-by-default)
            return {
                "claim": claim,
                "verdicts": verdicts,
                "survived": survived,
                "links": [h.get("url", "") for h in (hits if isinstance(hits, list) else [])][:2],
            }

        results = await ctx.parallel([lambda c, cl=cl: verify_one(c, cl) for cl in claims])

        ctx.phase("report")
        kept = [r for r in results if r["survived"]]
        out = [
            f"# Fact-check — {len(kept)}/{len(results)} claims survived",
            "",
            f"**Question:** {inp.question}",
            "",
        ]
        for r in results:
            mark = "✅" if r["survived"] else "❌"
            tally = "/".join(r["verdicts"])
            out.append(f"{mark} {r['claim']}  _( {tally} )_")
            for url in r["links"]:
                if url:
                    out.append(f"    ↳ {url}")
        return "\n".join(out)

    return app


if __name__ == "__main__":
    app = make_app(system_prompt="When asked to fact-check or verify a claim, run `fact_check`.")
    register(app)
    app.run()
