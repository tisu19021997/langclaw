"""
Pattern: LOOP-UNTIL-DONE  —  keep going until a real stop condition, not a fixed N.

Blog: "Loop-until-done" (a.k.a. loop-until-dry)

Real job: enumerate the edge cases / failure modes of something. You don't know up
front how many there are, so a fixed "give me 10" either pads with junk or stops
short. Instead, loop: each round asks for NEW items the run hasn't seen, dedupe
against everything accumulated, and stop when you hit the target, OR when N
consecutive rounds turn up nothing new (the well is dry), OR at a hard round cap.
The workflow returns an honest reason for *why* it stopped.

    /workflows run edge_hunt {"target": "a function that parses a user's birthday string",
        "target_count": 12, "patience": 2}
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from examples.workflow_patterns._app import make_app, norm

_HUNTER_SYS = (
    "You find edge cases and failure modes. Given a TARGET and a list of cases ALREADY "
    "FOUND, propose up to 5 genuinely NEW ones not already covered. Terse. Return an "
    "empty list if you truly can't think of new ones."
)


class Hunt(BaseModel):
    target: str = Field(description="What to find edge cases / failure modes for.")
    target_count: int = Field(default=12, ge=1, le=40, description="Stop once this many found.")
    patience: int = Field(default=2, ge=1, le=5, description="Dry rounds before giving up.")
    max_rounds: int = Field(default=8, ge=1, le=20, description="Hard cap on rounds.")


class Cases(BaseModel):
    cases: list[str] = Field(description="New edge cases, terse, one per item.")


def register(app):
    @app.workflow(
        "edge_hunt",
        input=Hunt,
        description=(
            "Enumerate edge cases for a target by looping: each round proposes NEW cases, "
            "deduped against all found so far. Stops at the target count, after N dry "
            "rounds, or a round cap — and reports which."
        ),
    )
    async def edge_hunt(ctx, inp: Hunt) -> str:
        found: list[str] = []
        seen: set[str] = set()
        dry = 0
        rnd = 0
        reason = "hit round cap"

        while rnd < inp.max_rounds:
            rnd += 1
            ctx.phase(f"round {rnd}")
            already = "\n".join(f"- {c}" for c in found) or "(nothing yet)"
            # One structured judgment per round → a validated list, no parsing.
            proposed = await ctx.llm(
                f"TARGET: {inp.target}\n\nALREADY FOUND:\n{already}",
                schema=Cases,
                system=_HUNTER_SYS,
            )
            fresh = [c for c in proposed.cases[:5] if norm(c) and norm(c) not in seen]

            if not fresh:
                dry += 1
                ctx.log(f"round {rnd}: nothing new ({dry}/{inp.patience} dry)")
                if dry >= inp.patience:
                    reason = f"dried up after {dry} empty rounds"
                    break
                continue

            dry = 0
            for c in fresh:
                seen.add(norm(c))
                found.append(c)
            ctx.log(f"round {rnd}: +{len(fresh)} → {len(found)}/{inp.target_count}")
            if len(found) >= inp.target_count:
                reason = f"reached target of {inp.target_count}"
                found = found[: inp.target_count]
                break

        ctx.phase("report")
        out = [
            f"# Edge cases — {len(found)} found",
            "",
            f"**Target:** {inp.target}",
            f"**Stopped:** {reason} (after {rnd} round{'s' if rnd != 1 else ''})",
            "",
        ]
        out += [f"{i}. {c}" for i, c in enumerate(found, 1)]
        return "\n".join(out)

    return app


if __name__ == "__main__":
    app = make_app(
        system_prompt="When asked to brainstorm edge cases or risks exhaustively, run `edge_hunt`."
    )
    register(app)
    app.run()
