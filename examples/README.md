# Examples

Runnable examples that demonstrate the core langclaw API.

## Prerequisites

```bash
pip install langclaw[telegram]   # or langclaw[discord], langclaw[websocket]
langclaw init                    # scaffold ~/.langclaw/ with config + workspace
```

Copy `.env.example` to `.env` in your project root and fill in:

- At least one LLM provider key (e.g. `LANGCLAW__PROVIDERS__ANTHROPIC__API_KEY`)
- At least one channel token (e.g. `LANGCLAW__CHANNELS__TELEGRAM__TOKEN`)

## Echo Bot

The simplest possible langclaw app.

**What it shows:** `Langclaw()` constructor with a `system_prompt`, `@app.tool()`, and `app.run()`.

```bash
python examples/echo_bot.py
```

## Research Assistant

A stock-price bot with a research subagent and scheduled digest reports.

**What it shows:** `@app.tool()`, `app.subagent()`, `@app.command()`, `app.role()`, lifecycle hooks, cron-ready design.

```bash
python examples/research_assistant.py
```

Then message the bot:

- *"What's the price of NVDA?"* — calls the custom `get_stock_price` tool
- *"Compare NVDA and AMD's market position"* — delegates to the `deep-researcher` subagent, which runs multiple web searches in isolated context and returns a synthesised report
- *"Search the web for AI chip market news"* — uses built-in `web_search`
- *"Schedule a daily market summary at 9 AM"* — creates a cron job via the agent
- `/watchlist` — instant price snapshot, no LLM involved

## Workflow Research

A bot that exposes an operator-authored `@app.workflow()` — a durable, typed, multi-step routine the agent invokes as the `workflow_research` tool.

**What it shows:** `@app.workflow()`, Pydantic-typed input, `ctx.phase()` / `ctx.parallel()` / `ctx.tool()`, the default-deny `workflows` RBAC axis, and (with the interpreter on) Mode 1 — calling the workflow from an `eval` script.

Workflows are **off by default** — enable them first:

```bash
export LANGCLAW__WORKFLOWS__ENABLED=true
python examples/workflow_research.py
```

> If you skip the env var, the `workflow_research` tool is never built and the
> agent silently uses `web_search` / the `task` subagent instead. The
> `workflows` RBAC axis is also **default-deny**: defining any `app.role()`
> auto-enables permissions, so a user who resolves to an ungranted role gets the
> workflow tool stripped before the model sees it. This example sets
> `default_role = "analyst"` so a fresh chat user reaches the workflow.

Then message the bot:

- *"Run the research workflow on quantum computing"* — the agent calls `workflow_research` with `{"topic": "quantum computing"}`; the workflow fans out one `web_search` per angle in parallel, then synthesises a brief
- *"Research electric vehicles and solar — use the workflow for each"* — with the interpreter on (`LANGCLAW__INTERPRETER__ENABLED=true`), the agent writes one `eval` script that calls `tools.workflowResearch(...)` per topic (Mode 1)

Unlike a subagent (an LLM improvising in isolated context), a workflow's steps, fan-out, and phases are fixed Python — the LLM only chooses *when* to run it and with *what* typed input.

> Not yet wired (tracked as the live-gateway follow-up): `/workflow` slash commands, cron-fired workflows, live phase/step progress in chat, and durable resume in production. The agent-invoked path above works today.

## Knowledge Base Bot

A support assistant with custom middleware and LangChain community tools.

**What it shows:** `@app.tool()`, `app.register_tool()` (LangChain DuckDuckGo), `app.add_middleware()` (token-usage tracker), `@app.command()`, RBAC.

```bash
pip install duckduckgo-search
python examples/knowledge_base_bot.py
```

Then message the bot:

- *"What is your refund policy?"* — calls the custom `lookup_knowledge_base` tool
- *"Search the web for Python 3.13 release notes"* — uses the registered DuckDuckGo tool
- *"How do I return an item?"* — multi-tool reasoning (KB + web fallback)
- `/usage` — show your token usage stats, no LLM involved

## Gmail Assistant

An agent with full Gmail integration.

**What it shows:** Gmail tools (read, search, send, draft, reply, manage labels), OAuth 2.0 setup, RBAC (admin role can send/draft/reply; viewer role is read-only), `@app.command()`.

```bash
pip install "langclaw[gmail]"
python examples/gmail_assistant.py
```

## Nobel Prize Bot

An agent that answers Nobel Prize questions and schedules recurring trivia via cron.

**What it shows:** `@app.tool()` calling a public REST API, cron scheduling (the built-in `cron` tool lets users schedule recurring tasks naturally), `@app.command()`.

```bash
python examples/nobel_assistant.py
```

## WebSocket Guard

A guarded assistant accessible over WebSocket.

**What it shows:** WebSocket channel configuration, built-in guardrails (Content filtering and PII redaction middleware), `@app.tool()`, `@app.command()`.

```bash
pip install "langclaw[websocket]"
python examples/websocket_guard.py
```
