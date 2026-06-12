# Workflows

A **workflow** is a durable, typed, multi-step routine you register once and run many ways.

```bash
LANGCLAW__WORKFLOWS__ENABLED=true   # workflows are off by default
```

## Register a workflow

```python
from pydantic import BaseModel
from langclaw import Langclaw

app = Langclaw()

class Brief(BaseModel):
    topic: str
    angles: list[str] = ["overview", "risks", "recent news"]

@app.workflow(
    "research",
    input=Brief,
    max_concurrency=4,
    description="Search several angles in parallel, then synthesize.",
)
async def research(ctx, inp: Brief) -> str:
    ctx.phase("gather")

    findings = await ctx.parallel([
        lambda c, a=a: c.tool("web_search", query=f"{inp.topic} {a}")
        for a in inp.angles
    ])

    ctx.phase("synthesize")
    return "\n\n".join(f"## {a}\n{r}" for a, r in zip(inp.angles, findings))
```

## Run a workflow

**Via the agent** — the agent sees a `workflow_research` tool and calls it when appropriate.

**Via CLI**:
```
/workflows run research {"topic": "solid-state batteries"}
/workflows runs          # list recent runs
/workflows status <id>   # run details
```

**Via cron** — schedule it once and it fires on the schedule without any LLM overhead. See [Scheduled Jobs](cron.md).

## Workflow steps

Four kinds of step, each durable and resumable:

### `ctx.tool(name, **kwargs)`

Call a registered tool. Deterministic, fast.

```python
result = await ctx.tool("web_search", query="langchain docs")
```

### `ctx.llm(prompt, schema=Model, system=...)`

One model call — no tools, no agent loop. Returns a validated Pydantic object when `schema` is given, plain text otherwise.

```python
from pydantic import BaseModel

class Score(BaseModel):
    score: int
    reason: str

verdict = await ctx.llm(
    f"Score this tagline 1-10: {tagline}",
    schema=Score,
    system="You are a marketing critic.",
)
print(verdict.score)  # int, not a string to parse
```

!!! tip
    `ctx.llm` is a langclaw primitive — neither Claude Code nor deepagents expose a bare one-shot model-call step. Use it for classification, scoring, pairwise comparison, extraction — anywhere you don't need tool calls.

### `ctx.subagent(type, prompt)`

Delegate to a subagent — a full isolated agent with its own tools and context window. Use when a leaf needs multi-step work (search → read → reason).

```python
notes = await ctx.subagent(
    "scout",
    f"Research '{name}' as an agent framework. Focus on strengths and weaknesses.",
)
```

### `ctx.parallel(fns, return_exceptions=False)`

Fan out a list of step lambdas concurrently, bounded by the workflow's `max_concurrency`:

```python
results = await ctx.parallel([
    lambda c, name=name: c.subagent("scout", f"Research {name}")
    for name in competitors
], return_exceptions=True)  # one failure doesn't sink the rest
```

## Orchestration patterns

All six patterns from Anthropic's [dynamic workflows guide](https://claude.com/blog/a-harness-for-every-task-dynamic-workflows-in-claude-code) are implemented as runnable examples:

| Pattern | What it does | Example workflow |
|---|---|---|
| **Classify-and-act** | Route by type, run a specialized branch | `triage` |
| **Fan-out-and-synthesize** | Parallel subagents, isolated contexts, one merged output | `landscape` |
| **Adversarial verification** | Independent skeptics try to refute each claim | `fact_check` |
| **Generate-and-filter** | Candidates in parallel, scored, top survivors kept | `tagline_studio` |
| **Tournament** | Rank by pairwise duels — more stable than 1–10 scoring | `prioritize` |
| **Loop-until-done** | Keep going until dry streak, not a fixed count | `edge_hunt` |

```bash
# run all six patterns locally
LANGCLAW__WORKFLOWS__ENABLED=true uv run python -m examples.workflow_patterns
uv run langclaw probe '/workflows'
```

See [`examples/workflow_patterns/`](https://github.com/tisu19021997/langclaw/tree/main/examples/workflow_patterns) for the full source.

## Progress streaming

Use `ctx.phase` and `ctx.log` to stream live progress to the channel while the workflow runs:

```python
ctx.phase("research")       # named phase header
ctx.log("searching web...")  # inline log line
```

## Saved workflows (agent-authored)

When the code interpreter is enabled (`LANGCLAW__INTERPRETER__ENABLED=true`), the agent can author a workflow by writing a `.js` file to `workflows/`. It loads as a `workflow_<name>` tool without a restart.

See the [Architecture guide](architecture.md) for details.
