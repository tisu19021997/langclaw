# Workflow Pattern Cookbook

One non-trivial `@app.workflow` for each orchestration pattern in Claude Code's
[*A harness for every task: dynamic workflows*](https://claude.com/blog/a-harness-for-every-task-dynamic-workflows-in-claude-code),
rebuilt on langclaw primitives. Every example here was driven end-to-end through
the real gateway with the probe — the outputs below are from actual runs.

| Pattern | Workflow | Real job |
|---|---|---|
| **Classify-and-act** | `triage` | Route a support ticket by type, then run a type-specific branch (CVE search / similar-issue search / scoping / web answer). |
| **Fan-out-and-synthesize** | `landscape` | Research each contender in its own parallel branch, then synthesise one comparison table. |
| **Adversarial verification** | `fact_check` | Decompose a draft into claims; independent skeptics try to *refute* each against fresh evidence; majority vote. |
| **Generate-and-filter** | `tagline_studio` | Generate candidates from diverse angles, score each in parallel, keep the top few above a bar. |
| **Tournament** | `prioritize` | Rank a list by single-elimination pairwise duels (robust where absolute 1–10 scoring is noisy). |
| **Loop-until-done** | `edge_hunt` | Keep proposing *new* items, deduped, until a target / dry-streak / round cap — with an honest stop reason. |

## Run one

```bash
LANGCLAW__WORKFLOWS__ENABLED=true uv run python -m examples.workflow_patterns.tournament
# in another terminal — drive it through the real pipeline:
uv run langclaw probe '/workflows run prioritize {"items": ["SSO","dark mode","audit export","cold start"], "criterion": "impact per eng-week"}'
```

Or mount all six on one app: `uv run python -m examples.workflow_patterns`, then
`uv run langclaw probe '/workflows'`.

## How they're built

The patterns differ; the plumbing is shared in [`_app.py`](_app.py):

- **`make_app()`** — a WebSocket-only app (the outward channels are forced off so an
  example never hijacks a real bot), with the workflow primitive on.
- **`reasoner(app, name, system=…)`** — registers a focused, single-shot
  **model-backed tool**. A workflow reaches LLM judgment with
  `await ctx.tool("<name>", prompt=…)`, and each call is a *fresh* model context —
  the isolation the dynamic-workflow patterns rely on to fight goal-drift and
  self-preferential bias.
- **tolerant parsers** (`pick_label` / `parse_score` / `parse_winner` /
  `split_items`) — real models don't always honour "reply with only X", so the
  control flow degrades gracefully instead of crashing.

Each workflow is plain langclaw: `ctx.phase` / `ctx.log` for progress, `ctx.parallel`
for bounded fan-out (with `return_exceptions=True` for failure isolation), `ctx.tool`
for tools and reasoners, and ordinary Python for the branching, brackets, dedup, and
loops. Because they're registered workflows, each is also a `workflow_<name>` tool the
agent can call, a `/workflows run` target, and **cron-schedulable**.

## Honest boundary

A registered `@app.workflow` orchestrates **tools** and arbitrary Python — not
deepagents subagents. `ctx.subagent` exists on the API but is currently **inert for
registered workflows** (the `task` delegation tool isn't in the workflow step
executor's toolset), so these examples put LLM judgment in **model-backed tools**
instead — which is the working way to get isolated reasoning into a durable,
schedulable workflow today. Full subagent fan-out (`tools.task({subagent_type})`)
*does* work in the ad-hoc `eval` interpreter path — see
[`../hn_digest_eval.py`](../hn_digest_eval.py).

These workflows make real model calls; the quality of a verdict, score, or
classification is bounded by the model and (for `fact_check` / `triage`) the evidence
retrieved. The examples surface verdicts and source links so a human can audit — they
structure the reasoning, they don't rubber-stamp it.
