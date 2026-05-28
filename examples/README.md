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

## Research Workflow

Orchestrate named agents with a dynamic workflow.

**What it shows:** `app.agent()` (named specialist agents), `@app.workflow()` with `ctx.phase` / `ctx.parallel` / `ctx.run`, agent-spawned workflows (`run_workflow` / `orchestrate` tools with completion callback), and cron-scheduled workflows.

```bash
python examples/research_workflow.py
```

Then message the bot:

- `/workflow digest quantum networking` — run the digest workflow yourself; result delivered to the chat
- *"Research the EV battery market for me in the background."* — agent calls `run_workflow` and pings you when ready
- *"Orchestrate a comparison of Postgres vs SQLite for our use case."* — agent composes a step DAG with `orchestrate`
- *"Every weekday at 8am, run the digest workflow on AI-agent news."* — agent schedules it via cron (set `LANGCLAW__CRON__ENABLED=true`)

Full guide: [`docs/workflows.md`](../docs/workflows.md).

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
