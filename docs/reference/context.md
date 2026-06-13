# Context

Langclaw has two distinct context objects — don't confuse them:

- **`LangclawContext`** — per-request channel metadata (who/where a message came from), available to tools and middleware.
- **`WorkflowContext`** — the `ctx` handed to a `@app.workflow()` body; the step surface (`tool`, `llm`, `subagent`, `agent`, `parallel`, `phase`, `log`).

## LangclawContext

::: langclaw.LangclawContext

## WorkflowContext

The object every workflow body receives as its first argument
(`async def (ctx, inp) -> output`). See the [Workflows guide](../guides/workflows.md#workflow-steps) for usage.

::: langclaw.WorkflowContext
