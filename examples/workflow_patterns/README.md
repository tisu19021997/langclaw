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
- two tolerant parsers (`pick_label` / `norm`) for the few spots that read a
  *subagent's* free-text reply — models don't always honour "reply with only X".

Each workflow is plain langclaw: `ctx.phase` / `ctx.log` for progress, `ctx.parallel`
for bounded fan-out (with `return_exceptions=True` for failure isolation), `ctx.tool`
for tools, `ctx.llm` for one-shot judgment, `ctx.subagent` for delegation, and ordinary
Python for the branching, brackets, dedup, and loops. Because they're registered
workflows, each is also a `workflow_<name>` tool the agent can call, a `/workflows run`
target, and **cron-schedulable**.

## Two ways to get LLM work into a workflow

A workflow reaches LLM judgment two ways, and the cookbook uses both **by fit**:

- **`ctx.llm(prompt, schema=Model)`** — one model call, no tools, no agent loop, for a
  *one-shot judgment* (classify, score, compare, extract). With a Pydantic `schema` you
  get a *validated object back from a single call* — no string parsing. Used by
  classify-and-act (`Routing`), generate-and-filter (`Score`), tournament (`Duel`),
  loop-until-done (`Cases`), and the decompose/synthesis steps.
- **`ctx.subagent(type, prompt)`** — when the leaf does *multi-step work with its own
  tools* in an isolated context: `landscape` fans out a `scout` subagent per contender;
  `fact_check` spawns independent `skeptic` subagents that gather their own evidence.
  (Full subagent fan-out is also available in the ad-hoc `eval` path via
  `tools.task({subagent_type})` — see [`../hn_digest_eval.py`](../hn_digest_eval.py).)

These workflows make real model calls; the quality of a verdict, score, or
classification is bounded by the model and the evidence retrieved. The examples surface
verdicts and source links so a human can audit — they structure the reasoning, they
don't rubber-stamp it.
