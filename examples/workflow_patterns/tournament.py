"""
Pattern: TOURNAMENT  —  rank by pairwise duels when absolute scoring is unreliable.

Blog: "Tournament" (pairwise comparative judgment)

Real job: prioritize a backlog. Asking a model to score ten items 1–10 in the
abstract is noisy; asking "which of these two better satisfies X" is far more
stable. So run a single-elimination bracket: pair items up, a referee judges each
duel (all duels in a round run in parallel), winners advance, repeat until one
champion remains. Odd rounds get a bye. You get a ranking you can trust the top of.

    /workflows run prioritize {"criterion": "impact per engineering-week",
        "items": ["SSO login", "dark mode", "audit log export", "faster cold start",
                  "Slack notifications", "CSV import"]}
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from examples.workflow_patterns._app import make_app

_REFEREE_SYS = (
    "You are an impartial referee. Given a CRITERION and two options A and B, decide "
    "which one better satisfies the criterion."
)


class Bracket(BaseModel):
    items: list[str] = Field(description="The things to rank (2–16).")
    criterion: str = Field(description="What 'better' means in each duel.")


class Duel(BaseModel):
    winner: Literal["A", "B"]
    why: str = Field(description="One-sentence justification.")


def register(app):
    @app.workflow(
        "prioritize",
        input=Bracket,
        max_concurrency=8,
        description=(
            "Rank a list by single-elimination pairwise duels: a referee judges each "
            "pair (rounds run in parallel), winners advance until one champion remains. "
            "More robust than absolute 1–10 scoring."
        ),
    )
    async def prioritize(ctx, inp: Bracket) -> str:
        current = [x.strip() for x in inp.items if x and x.strip()]
        if len(current) < 2:
            return f"# Priority\n\nNeed at least two items; got {len(current)}."

        async def duel(c, a: str, b: str | None) -> str:
            if b is None:  # bye — advances for free
                return a
            # One structured judgment per duel → a validated winner, no parsing.
            verdict = await c.llm(
                f"CRITERION: {inp.criterion}\nA: {a}\nB: {b}", schema=Duel, system=_REFEREE_SYS
            )
            return b if verdict.winner == "B" else a

        rounds: list[str] = []
        rnd = 0
        while len(current) > 1:
            rnd += 1
            ctx.phase(f"round {rnd}")
            pairs = [
                (current[i], current[i + 1] if i + 1 < len(current) else None)
                for i in range(0, len(current), 2)
            ]
            winners = await ctx.parallel([lambda c, a=a, b=b: duel(c, a, b) for a, b in pairs])
            lines = [
                f"- {a} _(bye)_" if b is None else f"- {a} vs {b} → **{w}**"
                for (a, b), w in zip(pairs, winners, strict=False)
            ]
            rounds.append(f"### Round {rnd}\n" + "\n".join(lines))
            ctx.log(f"round {rnd}: {len(winners)} advance")
            current = winners

        out = [f"# Priority by duel — 🏆 **{current[0]}**", "", f"_Criterion: {inp.criterion}_", ""]
        out += rounds
        return "\n".join(out)

    return app


if __name__ == "__main__":
    app = make_app(system_prompt="When asked to rank or prioritize a list, run `prioritize`.")
    register(app)
    app.run()
