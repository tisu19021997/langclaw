# Subagents

Subagents are specialist agents that run in **isolated contexts** — their own tools, their own conversation thread, no shared memory with the main agent. Use them when a subtask needs multi-step tool use (search → read → synthesize) that would pollute the main context if run inline.

## Register a subagent

```python
app.subagent(
    "researcher",
    description="Deep research with web search and synthesis",
    system_prompt=(
        "You are a thorough researcher. Search multiple sources, "
        "synthesize the findings, and always cite your sources."
    ),
    tools=["web_search", "web_fetch"],
    output="channel",   # stream results directly to the user
)
```

The main agent delegates via the built-in `task` tool.

## Delegation from a workflow

```python
@app.workflow("landscape", input=Landscape, max_concurrency=5)
async def landscape(ctx, inp: Landscape) -> str:
    ctx.phase("research")

    # Each contender gets its own isolated researcher — no context cross-contamination.
    findings = await ctx.parallel([
        lambda c, name=name: c.subagent(
            "researcher",
            f"Research '{name}' as an agent framework.",
        )
        for name in inp.contenders
    ], return_exceptions=True)

    ctx.phase("synthesize")
    ...
```

## Output modes

| `output` | Behaviour |
|---|---|
| `"main_agent"` (default) | Returns the final text back to the calling agent or workflow |
| `"channel"` | Streams directly to the originating channel as it runs |

The literal value is `"main_agent"` — passing any other string (e.g. `"agent"`) raises a `ValueError` at registration.

## Named agents

Named agents are top-level agents — not subagents — with their own persistent conversation threads. Users switch between them with `/agent`:

```python
app.agent(
    "analyst",
    description="Financial analysis with data tools",
    system_prompt="You are a financial analyst. Always cite sources.",
    tools=["web_search", "get_stock_price"],
    model="openai:gpt-4.1",
)
```

```
/agent              → list all agents
/agent analyst      → switch to the analyst (persistent)
/agent default      → back to the main agent
/agent analyst What's NVDA's P/E ratio?   → one-off, no session change
```
