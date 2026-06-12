"""
Pattern: CLASSIFY-AND-ACT  —  route work by type, then take a type-specific action.

Blog: "Classify-and-act" — https://claude.com/blog/a-harness-for-every-task-dynamic-workflows-in-claude-code

Real job: triage an inbound support/issue report. One model call decides the
*kind* of ticket; the workflow then runs a different branch per kind — a security
report gets a severity assessment plus a live CVE search; a bug gets a
similar-issues search plus a repro checklist; a feature request gets scoped; a
question gets answered from the web. The classifier never does the work, and the
handlers never re-classify — each step has one job and one isolated context.

    /workflows run triage {"text": "the /login endpoint 500s when the password contains a +"}
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from examples.workflow_patterns._app import make_app, pick_label, reasoner, say

LABELS = ["security", "bug", "feature_request", "question"]


class Ticket(BaseModel):
    text: str = Field(description="The raw inbound ticket / report text.")


def register(app):
    reasoner(
        app,
        "classify_ticket",
        description="Classify a support ticket into one category.",
        system=(
            "You are a precise support-ticket router. Read the ticket and reply with "
            "EXACTLY ONE of these labels, lowercase, nothing else: "
            "security, bug, feature_request, question."
        ),
    )
    reasoner(
        app,
        "assess",
        description="Give a short, structured assessment of a ticket.",
        system=(
            "You are a senior on-call engineer. Given a ticket and its category, "
            "respond with a tight, skimmable assessment: severity (low/med/high), the "
            "single most likely cause, and the one next action. Markdown, <120 words."
        ),
    )

    @app.workflow(
        "triage",
        input=Ticket,
        max_concurrency=4,
        description=(
            "Triage an inbound ticket: classify it (security/bug/feature_request/"
            "question), then run the matching branch — CVE search, similar-issue "
            "search, scoping, or a web-grounded answer — and return a routed brief."
        ),
    )
    async def triage(ctx, inp: Ticket) -> str:
        ctx.phase("classify")
        label = pick_label(
            say(await ctx.tool("classify_ticket", prompt=inp.text)), LABELS, default="question"
        )
        ctx.log(f"classified as {label}")

        ctx.phase("act")
        out = [f"# Triage — `{label}`", "", f"> {inp.text.strip()}", ""]

        # Each branch reaches for different tools — that's the point of the pattern.
        if label == "security":
            hits = await ctx.tool("web_search", query=f"{inp.text} CVE advisory", n=3)
            out.append(say(await ctx.tool("assess", prompt=f"category=security\n{inp.text}")))
            out += ["", "## Related advisories", _links(hits)]
        elif label == "bug":
            hits = await ctx.tool("web_search", query=f"{inp.text} error fix github issue", n=3)
            out.append(say(await ctx.tool("assess", prompt=f"category=bug\n{inp.text}")))
            out += ["", "## Possibly-related reports", _links(hits)]
        elif label == "feature_request":
            out.append(
                say(
                    await ctx.tool(
                        "assess",
                        prompt=(
                            "category=feature_request. Instead of severity, give: user "
                            f"value, rough effort (S/M/L), and one open question.\n{inp.text}"
                        ),
                    )
                )
            )
        else:  # question
            hits = await ctx.tool("web_search", query=inp.text, n=3)
            out.append(say(await ctx.tool("assess", prompt=f"category=question\n{inp.text}")))
            out += ["", "## Sources", _links(hits)]

        return "\n".join(out)

    return app


def _links(hits) -> str:
    if not isinstance(hits, list) or not hits:
        return "_no results_"
    return "\n".join(f"- [{h.get('title') or 'link'}]({h.get('url', '')})" for h in hits[:3])


if __name__ == "__main__":
    app = make_app(system_prompt="When the user reports an issue, run the `triage` workflow.")
    register(app)
    app.run()
