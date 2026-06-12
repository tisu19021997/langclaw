"""
Pattern: ADVERSARIAL VERIFICATION  —  independent checkers try to REFUTE each claim.

Blog: "Adversarial verification"

Real job: fact-check a drafted answer before it ships. Decompose the draft into
atomic claims, then for each claim spawn N independent skeptic *subagents* — each
gathers its OWN evidence with web_search in an isolated context and tries to refute,
defaulting to REFUTED when unsure. A claim survives only if it isn't out-voted.
Independence is the whole game, and a subagent makes it real: each skeptic searches
on its own and never sees the others' findings — so you get genuinely separate
verdicts, not one context rationalising itself N times.

Decomposition is a one-shot judgment, so it's a model-backed tool; the skeptics do
multi-step work with their own tools, so they're subagents.

    /workflows run fact_check {"question": "Is SQLite a good prod database?",
        "answer": "SQLite supports unlimited concurrent writers; NASA uses it for telemetry.",
        "votes": 2}
"""

from __future__ import annotations

import re

from pydantic import BaseModel, Field

from examples.workflow_patterns._app import make_app, pick_label, reasoner, say, split_items

VERDICTS = ["refuted", "supported", "unverifiable"]
_URL_RE = re.compile(r"https?://\S+")


class Draft(BaseModel):
    question: str = Field(description="What the answer was responding to.")
    answer: str = Field(description="The drafted answer to verify, claim by claim.")
    votes: int = Field(default=2, ge=1, le=4, description="Independent skeptics per claim.")


def register(app):
    # Decomposition: a one-shot judgment over text → a model-backed tool.
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
    # Verification: each skeptic does its OWN multi-step evidence-gathering → a subagent.
    app.subagent(
        "skeptic",
        description="Independently fact-check one claim with web search.",
        system_prompt=(
            "You are a skeptical fact-checker. Given a CLAIM, use web_search to find "
            "evidence, then TRY TO REFUTE it. If the evidence doesn't clearly support "
            "the claim, lean REFUTED. Reply on three lines:\n"
            "VERDICT: <refuted|supported|unverifiable>\n"
            "WHY: <one sentence>\n"
            "SOURCE: <a url you used, or none>"
        ),
        tools=["web_search"],
    )

    @app.workflow(
        "fact_check",
        input=Draft,
        max_concurrency=6,
        description=(
            "Verify a drafted answer: decompose it into atomic claims, then for each "
            "claim spawn N independent skeptic subagents that gather their own evidence "
            "and vote refute/support. Returns a report of which claims survived."
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
            # N independent skeptic subagents, each with its own isolated evidence pull.
            replies = await c.parallel(
                [lambda cc: cc.subagent("skeptic", f"CLAIM: {claim}") for _ in range(inp.votes)]
            )
            texts = [say(r) for r in replies]
            verdicts = [pick_label(t, VERDICTS, default="unverifiable") for t in texts]
            survived = verdicts.count("supported") > verdicts.count("refuted")  # ties lose
            links = [m.group(0).rstrip(".,)") for t in texts if (m := _URL_RE.search(t))]
            return {"claim": claim, "verdicts": verdicts, "survived": survived, "links": links[:2]}

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
                out.append(f"    ↳ {url}")
        return "\n".join(out)

    return app


if __name__ == "__main__":
    app = make_app(system_prompt="When asked to fact-check or verify a claim, run `fact_check`.")
    register(app)
    app.run()
