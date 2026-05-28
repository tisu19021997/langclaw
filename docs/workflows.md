# Dynamic Workflows

Workflows let you orchestrate langclaw's **named agents** from plain `async`
Python. A workflow can run several agents in parallel, chain their outputs, loop,
and branch — structure decided at runtime — and it plugs into the same message
bus, channels, and cron scheduler as everything else.

This is deliberately *not* a port of a code-generating, sandboxed "agent script"
model. In langclaw a workflow is ordinary Python that delegates each unit of LLM
work to a registered agent. "Dynamic" means data-driven control flow, not
generated code. See [ARCHITECTURE.md](ARCHITECTURE.md) for the framework's
design tenets.

> **Quick start:** see [`examples/research_workflow.py`](../examples/research_workflow.py)
> for a complete, runnable app.

---

## A first workflow

Register an `async` function with `@app.workflow()`. It receives a
`WorkflowContext` and returns the final user-facing string (or `None` to stay
silent).

```python
from langclaw import Langclaw, WorkflowContext

app = Langclaw()

app.agent("researcher", description="Gathers sourced findings")
app.agent("writer", description="Writes a tight brief")

@app.workflow("digest", description="Research a topic and synthesise a brief")
async def digest(ctx: WorkflowContext) -> str:
    await ctx.phase("Research")
    findings = await ctx.run(ctx.input, agent="researcher")

    await ctx.phase("Write")
    return await ctx.run(f"Summarise into 5 bullets:\n{findings}", agent="writer")
```

Because the body is just Python, *dynamic* structure — loops, conditionals,
fan-out sized at runtime — is free. Every unit of LLM work goes through a
registered named agent via `ctx.run`; there is no sandbox and no mini-language.

---

## The `WorkflowContext` API

The single argument passed to every workflow.

| Member | Description |
|---|---|
| `ctx.input` | Free-text payload the workflow was triggered with (text after `/workflow <name>`, the agent's input, or a cron message). |
| `ctx.metadata` | Inbound message metadata (`agent_name`, `user_role`, `_depth`, `plan`, …). |
| `ctx.phase_title` | The most recently announced phase (or `""`). |
| `await ctx.phase(title)` | Announce a phase. Streams a progress header to the channel. |
| `await ctx.log(message)` | Emit a freeform progress line under the current phase. |
| `await ctx.run(prompt, *, agent=None)` | Run `prompt` on a named agent (default = the main agent) and return its text reply. Each call runs on a fresh isolated thread. |
| `await ctx.parallel(awaitables)` | Run awaitables concurrently and return results in order. **Barrier** — waits for all. |
| `await ctx.pipeline(items, *stages)` | Stream each item through all stages independently (**no barrier**). |

### `parallel` vs `pipeline`

`parallel` is a barrier — use it when a later step needs *all* results together:

```python
drafts = await ctx.parallel([
    ctx.run(f"Draft section: {s}", agent="writer") for s in sections
])
```

`pipeline` streams each item through every stage with no barrier between stages —
item A can be in stage 2 while item B is still in stage 1. Each stage is
`async (prev_result, original_item, index) -> next`. A stage that raises drops
*that* item to `None` and skips its remaining stages:

```python
results = await ctx.pipeline(
    files,
    lambda prev, item, i: ctx.run(f"Review {item}", agent="reviewer"),
    lambda prev, item, i: ctx.run(f"Verify: {prev}", agent="verifier"),
)
```

---

## How a workflow is triggered

All three paths set `metadata["workflow_name"]` and flow through the bus →
`GatewayManager._handle` → `WorkflowRunner.dispatch`. There is no separate
workflow transport.

### 1. Human — the `/workflow` command

Registered automatically when at least one workflow exists:

```
/workflow                     → list registered workflows
/workflow digest <topic>      → run "digest" with <topic> as ctx.input
```

The result is delivered straight to the channel. (Internal workflows whose name
starts with `_`, like the plan interpreter, are hidden from the listing.)

### 2. The agent — `run_workflow` / `orchestrate` tools

Registering a workflow automatically gives the **main agent** two tools (only the
main agent — named agents do not get them):

- `run_workflow(name, input)` — launch a registered workflow.
- `orchestrate(goal, steps)` — compose a *novel* workflow on the fly (see
  [Composing a workflow](#composing-a-workflow-orchestrate) below).

These run the workflow in the background and **call back** when done (see
[The completion callback](#the-completion-callback)). The agent tells the user
it has started, then relays the result when notified.

### 3. Cron — scheduled workflows

The built-in `cron` tool accepts a `workflow=` option, so the agent can schedule
a workflow on a timer (requires `LANGCLAW__CRON__ENABLED=true`):

> *"Every weekday at 8am, run the digest workflow on AI-agent news."*

The cron job stamps `workflow_name` at fire time; the `message` becomes the
workflow's `ctx.input` verbatim (not wrapped in the agent execution preamble).
The result is delivered to the scheduled chat.

---

## The completion callback

When a workflow is **agent-spawned** (the tools set `metadata["notify_agent"]`),
its result does not go straight to the channel. Instead, on completion the
runner:

1. **Persists** the full result to `<workspace>/workflows/<name>-<id>.md`.
2. **Publishes** an `InboundMessage(origin="workflow", to="agent")` back onto the
   bus — e.g. *"✅ Workflow 'digest' finished. Full result saved to: …"*.

That message re-enters `_handle`, routes to the agent, and the agent relays the
result to the user (or acts on it). It is the cron pattern in reverse: a
background job notifying the conversation that produced it.

**Two invariants keep this safe:**

- The completion message **omits `workflow_name`**, so `_handle` routes it to the
  agent instead of dispatching it as a brand-new workflow (which would loop
  forever).
- A `_depth` counter in metadata is incremented on each spawn and **capped**
  (default `3`), so an agent that spawns a workflow whose agents spawn workflows
  can't recurse without bound.

Human (`/workflow`) and cron triggers are *not* agent-spawned, so their results
are delivered directly to the channel — no callback.

---

## Composing a workflow (`orchestrate`)

For shapes you didn't predefine, the agent composes a **declarative plan** — the
langclaw analog of a generated script, but data, not code. The agent emits a
`WorkflowPlan` (a DAG of steps) as the structured arguments of `orchestrate`;
the built-in `_interpret` workflow runs it over your named agents.

```python
class WorkflowStep(BaseModel):
    id: str
    agent: str = "default"          # must be a registered agent
    prompt: str                     # may contain {input} and {step_id}
    depends_on: list[str] = []

class WorkflowPlan(BaseModel):
    steps: list[WorkflowStep]
```

The interpreter runs steps in **topological waves** — independent steps run
concurrently; a step's `{input}` placeholder is replaced with the goal and each
`{dep_id}` with that dependency's output. Example plan the agent might emit for
*"compare Postgres vs SQLite for our use case"*:

```json
{
  "steps": [
    {"id": "pg",  "agent": "researcher", "prompt": "Strengths/limits of Postgres for {input}"},
    {"id": "lite","agent": "researcher", "prompt": "Strengths/limits of SQLite for {input}"},
    {"id": "rec", "agent": "writer", "prompt": "Recommend one given:\nPG: {pg}\nSQLite: {lite}",
     "depends_on": ["pg", "lite"]}
  ]
}
```

The plan is validated before it runs (`validate_plan`): non-empty, unique ids,
every dependency resolves, no cycles, and every step targets a registered agent.
Invalid plans are rejected in the agent's turn with a clear message — no
execution. The interpreter returns the output of the plan's terminal step(s).

---

## Result delivery, summarised

| Trigger | `notify_agent` | Result goes to |
|---|---|---|
| `/workflow` (human) | no | the channel |
| cron (`workflow=`) | no | the scheduled chat |
| `run_workflow` / `orchestrate` (agent) | yes | persisted to a file + a callback message to the agent, which relays it |

Progress (`ctx.phase` / `ctx.log` / each `ctx.run`) always streams to the channel
as `tool_progress`, regardless of trigger — the same path channels already use
for tool progress, so no channel changes are required.

---

## Internals & file map

| Concern | File |
|---|---|
| `WorkflowContext` (run / parallel / pipeline / phase / log) | `langclaw/workflows/context.py` |
| Dispatch, progress, completion callback, result persistence | `langclaw/workflows/runner.py` |
| Declarative plan + validation + topological execution | `langclaw/workflows/plan.py` |
| Built-in `_interpret` workflow (runs a plan) | `langclaw/workflows/interpret.py` |
| Agent tools (`run_workflow`, `orchestrate`) + depth guard | `langclaw/agents/tools/workflow.py` |
| Dispatch wiring, `/workflow` command, per-step agent runner | `langclaw/gateway/manager.py` |
| Registration (`app.workflow()`) + tool wiring | `langclaw/app.py` |
| Cron `workflow_name` plumbing | `langclaw/cron/scheduler.py` |

The subsystem (runner, `/workflow` command, agent tools) activates only when at
least one workflow is registered via `@app.workflow()`.

Each `ctx.run` step runs on a fresh isolated thread (`workflow:<uuid>`) via
`GatewayManager._run_agent_for_workflow`, so steps never pollute one another's —
or the user's — conversation history.

---

## Testing

Workflows are ordinary `async` functions and the primitives are unit-testable
without a live gateway. See `tests/test_workflows.py`,
`tests/test_workflow_plan.py`, `tests/test_workflow_tools.py`, and
`tests/test_cron_workflow.py` for patterns: drive `WorkflowContext` with
recording `run_agent` / `emit` callbacks, assert on the `WorkflowRunner`'s
outbound messages via a fake channel, and validate plans with `validate_plan`.

```bash
uv run pytest tests/test_workflows.py tests/test_workflow_plan.py \
              tests/test_workflow_tools.py tests/test_cron_workflow.py -v
```
