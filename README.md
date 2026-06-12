# Langclaw

### Build production-ready, role-gated Claws.

[![PyPI version](https://img.shields.io/pypi/v/langclaw)](https://pypi.org/project/langclaw/)
[![Python versions](https://img.shields.io/pypi/pyversions/langclaw)](https://pypi.org/project/langclaw/)
[![License](https://img.shields.io/github/license/tisu19021997/langclaw)](https://github.com/tisu19021997/langclaw/blob/main/LICENSE)
[![Docs](https://img.shields.io/badge/docs-langclaw-purple)](https://tisu19021997.github.io/langclaw/)

**[Documentation](https://tisu19021997.github.io/langclaw/)** · **[Getting started](https://tisu19021997.github.io/langclaw/getting-started/)** · **[Blog](https://tisu19021997.github.io/langclaw/blog/)**

---

**Langclaw is a Python framework for building production-grade, multi-channel AI agent systems:**

- **RBAC** — declarative, three-axis (tools · subagents · workflows)
- **Durable workflows** — typed, multi-step, resumable
- **Scheduled jobs** — cron through the same agent pipeline
- **Persistent memory** — conversation state across sessions
- **Subagent delegation** — specialists in isolated contexts
- **Pluggable tool ecosystem** — bring any LangChain tool

Built on [LangChain](https://github.com/langchain-ai/langchain), [LangGraph](https://github.com/langchain-ai/langgraph), and [deepagents](https://github.com/tisu19021997/deepagents).

**Like FastAPI, but for agents** — define everything on one app object (tools, roles, subagents, workflows, channels), and langclaw handles the wiring, middleware, message routing, and state persistence. Bring your own LangChain tools, models, and agents.

Think OpenClaw or Hermes — a personal AI that:

- lives in your chat apps
- remembers across sessions
- runs jobs on a schedule
- respects who's allowed to do what

## Why Use Langclaw

1. **Framework, not a fork**: `uv add langclaw` and build on top of it — like Flask/FastAPI for agentic systems. No repo cloning, no boilerplate.
2. **Multi-channel from day one**: Telegram, Discord, Slack, WebSocket out of the box. Add custom channels with a single `app.add_channel()` call.
3. **Declarative RBAC, three axes**: `app.role("analyst", tools=["*"], subagents=["researcher"], workflows=["digest"])` — gate tools, subagents, *and* workflows per role. Subagents and workflows are default-deny; permissions run as middleware before the LLM sees anything.
4. **Subagent delegation**: Register specialist subagents that run in isolated contexts. The main agent delegates via a built-in `task` tool; results flow back cleanly or stream directly to the channel.
5. **Durable workflows**: Register typed, multi-step routines with `@app.workflow()`. Bounded parallelism, live progress, and crash-resume are handled for you — and the agent, a user, or a cron job can run them. ([jump ↓](#workflows))
6. **Scheduled jobs**: Ask the agent to schedule recurring tasks, or fire a saved workflow on cron — both publish to the same message bus and flow through the same pipeline as user messages.
7. **Guardrails middleware**: Content filtering, PII redaction, and rate limiting run as composable middleware before every LLM call.
8. **Pluggable everything**: Message bus (asyncio / RabbitMQ / Kafka), checkpointer (SQLite / Postgres), agent filesystem/shell backend (local-shell / filesystem / state / store), LLM providers — swap backends via config, not code changes.
9. **Built on LangChain + LangGraph**: Not a wrapper — langclaw compiles down to a real LangGraph `CompiledStateGraph`. Bring any LangChain tool, model, or integration.

## Hello World

```python
from langclaw import Langclaw

app = Langclaw()

@app.tool()
async def greet(name: str) -> str:
    """Say hello to someone."""
    return f"Hello, {name}!"

if __name__ == "__main__":
    app.run()
```

That's it. Langclaw wires up the message bus, checkpointer, channels (from your `.env`), and middleware — then starts listening.

## Real-World Example

Here's a research assistant with custom tools, subagent delegation, RBAC, lifecycle hooks, and a slash command — all on one app object:

```python
from langclaw import Langclaw
from langclaw.gateway.commands import CommandContext

app = Langclaw(
    system_prompt=(
        "## Research Assistant\n"
        "You are a financial research assistant.\n"
        "Check stock prices before answering. For complex questions, "
        "delegate to the deep-researcher subagent."
    ),
)

# -- Custom tool: stock price lookup ------------------------------------------

@app.tool()
async def get_stock_price(ticker: str) -> dict:
    """Fetch the latest quote for a US stock ticker."""
    ...  # httpx call to Yahoo Finance
    return {"ticker": ticker, "price": 182.52, "change_pct": "+1.23%"}

# -- Subagent: deep research in isolated context ------------------------------

app.subagent(
    "deep-researcher",
    description="Multi-step research using web search and synthesis",
    system_prompt="You are a thorough researcher. Search, synthesise, cite.",
    tools=["web_search", "web_fetch"],
    output="channel",  # stream results directly to the user
)

# -- RBAC: who can use what ---------------------------------------------------

app.role("analyst", tools=["*"])
app.role("free", tools=["web_search"])

# -- Command: bypasses the LLM entirely --------------------------------------

@app.command("watchlist", description="show watchlist prices (no AI)")
async def watchlist_cmd(ctx: CommandContext) -> str:
    return "AAPL: $182.52 | MSFT: $441.20 | NVDA: $135.80"

# -- Lifecycle hooks ----------------------------------------------------------

@app.on_startup
async def setup():
    ...  # open DB connections, HTTP clients, etc.

@app.on_shutdown
async def teardown():
    ...  # clean up resources

if __name__ == "__main__":
    app.run()
```

See [`examples/`](examples/) for complete, runnable versions.

## Workflows

A **workflow** is a durable, typed, multi-step routine you register once and run many ways — the agent calls it as a `workflow_<name>` tool, a user runs `/workflows run`, or a cron job fires it. You write the control flow; langclaw validates input and output, bounds parallelism, streams progress to the channel, and resumes after a crash.

```python
from pydantic import BaseModel
from langclaw import Langclaw

app = Langclaw()

class Brief(BaseModel):
    topic: str
    angles: list[str] = ["overview", "risks", "recent news"]

@app.workflow("research", input=Brief, max_concurrency=4,
              description="Search several angles in parallel, then synthesize.")
async def research(ctx, inp: Brief) -> str:
    ctx.phase("gather")                      # named progress, shown live in-channel

    def search(angle: str):                  # one search per angle...
        return lambda c: c.tool("web_search", query=f"{inp.topic} {angle}")

    findings = await ctx.parallel([search(a) for a in inp.angles])   # ...run in parallel

    ctx.phase("synthesize")
    return "\n\n".join(f"## {a}\n{r}" for a, r in zip(inp.angles, findings))
```

A workflow body composes four kinds of step, each a durable, resumable unit:

- `ctx.tool(name, **kw)` — call a registered tool (deterministic capability)
- `ctx.subagent(type, prompt)` — delegate to a subagent (its own tools, isolated context)
- `ctx.llm(prompt, schema=Model)` — **one model call, no tools, no agent loop** — for one-shot judgment (classify / score / extract). With a Pydantic `schema` you get a *validated object back from a single structured call*; without it, plain text. Neither Claude Code nor deepagents exposes a bare model-call step — this is langclaw's, and like every step it's memoized and crash-resumable.
- `ctx.parallel([...])` — fan the above out concurrently, bounded by `max_concurrency`

```bash
LANGCLAW__WORKFLOWS__ENABLED=true    # workflows are off by default
```

Now the agent sees a `workflow_research` tool, and users can drive it directly:

```
/workflows run research {"topic": "solid-state batteries"}
/workflows runs                      # recent runs from the journal
/workflows status <run_id>
```

**Three ways to author the body:**

| Mode | Who writes it | Reach for it when |
|---|---|---|
| `python` *(recommended)* | You | You want typed, testable, deterministic control flow — `ctx.parallel` / `ctx.phase` / `ctx.tool` / `ctx.subagent` / `ctx.llm`. |
| `saved` | The agent, once | The agent invents a repeatable job: it writes a sandboxed JS body via the `eval` interpreter, saves it to `workflows/<name>.js`, and it loads as a tool. |
| `llm_authored` *(experimental)* | The model, per run | Low-stakes, supervised work where you declare only the typed contract and let the model write the body fresh each run. |

**Put it on a schedule.** A saved workflow fired by cron re-runs with **zero LLM cost** — the frozen body executes verbatim — and delivers to the channel that scheduled it (e.g. a daily digest to your Telegram). Saved + cron is validated end-to-end; see [`examples/workflow_research.py`](examples/workflow_research.py) and [`examples/hn_digest_eval.py`](examples/hn_digest_eval.py).

> **How `saved` and `llm_authored` actually run** — programmatic tool calling (PTC):
>
> - The model writes a small JS program in the `langchain-quickjs` code-interpreter sandbox.
> - It calls tools, loops, branches, retries — makes one-shot model calls via `tools.llm({ prompt })` (the JS sibling of `ctx.llm`), and can hand off to deepagents subagents via `tools.task`.
> - Same approach as [Claude Code's dynamic workflows](https://claude.com/blog/introducing-dynamic-workflows-in-claude-code). The honest difference is **scale**: langclaw's sandbox is tuned for scripted control flow and a few subagents, not the tens-to-hundreds-of-agents parallel fan-out Claude's targets.
> - Needs `uv add "langclaw[interpreter]"`; capability-scoped to the tools you allow via `uses_tools` — no filesystem, network, or ambient host APIs.

## Message Flow

Every message — whether from a user or a cron job — follows the same path:

```
Channel (Telegram / Discord / Slack / WebSocket)
    │
    ├── /command ──▶ CommandRouter ──▶ instant response (no LLM)
    │
    └── message ──▶ InboundMessage ──▶ Message Bus
                                          │
                                    GatewayManager
                                          │
                                    SessionManager ──▶ Checkpointer
                                          │
                                    Middleware Pipeline
                                    (RBAC → Rate Limit → Content Filter → PII)
                                          │
                                    LangGraph Agent ──▶ Tools / Subagents
                                          │
                                    OutboundMessage ──▶ Channel
```

Cron jobs publish `InboundMessage` to the same bus, flowing through the identical pipeline. Commands bypass everything — they're fast system operations handled before the bus.

## Installation

```bash
uv add langclaw
```

With channel and backend extras:

```bash
uv add "langclaw[telegram,postgres,rabbitmq]"

# Or install everything:
uv add "langclaw[all]"
```

Available extras: `telegram`, `discord`, `slack`, `websocket`, `postgres`, `rabbitmq`, `kafka`, `mcp`, `search`, `gmail`.

## Quick Start

1. **Install the framework and a channel plugin** (e.g., Telegram):
   ```bash
   uv add "langclaw[telegram]"
   ```

2. **Set your environment variables** in a `.env` file:
   ```env
   LANGCLAW__PROVIDERS__OPENAI__API_KEY=sk-...
   LANGCLAW__CHANNELS__TELEGRAM__BOT_TOKEN=123456:ABC-DEF...
   ```

3. **Run your agent** (Choose one option):

   **Option 1: Using the CLI** (Recommended for new projects)
   ```bash
   langclaw init
   langclaw gateway
   ```

   **Option 2: Programmatically** (For custom setups)
   Create an `app.py` file:
   ```python
   from langclaw import Langclaw

   app = Langclaw(
       system_prompt="You are a friendly assistant. Keep answers short and helpful."
   )

   @app.tool()
   async def reverse_text(text: str) -> str:
       """Reverse the given text. Useful for word games and puzzles."""
       return text[::-1]

   if __name__ == "__main__":
       app.run()
   ```
   Then run it:
   ```bash
   python app.py
   ```

## Packages

| Package | Purpose |
|---|---|
| `app.py` | `Langclaw` class — the developer's primary interface (decorators, lifecycle, wiring) |
| `agents/` | LangGraph agent construction, tool wiring, subagent delegation, pluggable filesystem/shell backend (`backend.py`) |
| `gateway/` | Channel orchestration (`GatewayManager`), command routing, message dispatch |
| `bus/` | Message bus abstraction — asyncio (dev), RabbitMQ / Kafka (prod) |
| `middleware/` | Request pipeline: RBAC, rate limit, content filter, PII redaction |
| `workflows/` | Durable, typed, multi-step routines — registry, runtime, saved-workflow store, crash-resume |
| `interpreter/` | Opt-in sandboxed code interpreter (RLM) — PTC tool allowlist + middleware |
| `config/` | Pydantic Settings with `LANGCLAW__` env prefix (nested `__` delimiter) |
| `cron/` | Scheduled jobs via APScheduler v4 |
| `session/` | Maps (channel, user, context) to LangGraph thread IDs |
| `checkpointer/` | Conversation state persistence — SQLite (dev), Postgres (prod) |
| `providers/` | LLM model resolution via `init_chat_model` |
| `cli/` | Typer CLI: `langclaw init`, `langclaw gateway`, `langclaw agent`, `langclaw cron`, `langclaw status` |

## Business Workspaces (preview)

> [!note] Status: designed, not yet on `main`
> The groundwork — per-request, re-rootable agent backends — is in `main`. The layered prompt assembly lives on `feat/business-workspaces-layered-prompt` and isn't merged yet. Full design: [docs/BUSINESS_WORKSPACES.md](docs/BUSINESS_WORKSPACES.md).

One agent, many employees. A single `marketing` agent should serve every marketer from **one shared playbook**, while each person keeps **their own private memory**. That's two layers with two owners:

- **Org layer** — shared and authoritative: brand voice, operating rules, compliance. The agent reads it but can't edit it from below.
- **Employee layer** — personal memory and preferences, owned by that one person and invisible to everyone else.

What the design gives you:

- **Org is immutable from below** — an employee's agent reads org rules but can't rewrite them through tool use.
- **Promotion path** — a good personal tactic can be *proposed* up into the org playbook, reviewed, then it reaches everyone with no fan-out.
- **Privacy by construction** — one person's memory never enters another's prompt (scoped per `(channel, user_id)`).
- **Zero disruption** — off by default; today's single-workspace setups keep working unchanged.

Target layout — one agent, layered:

```
workspace/marketing/
├── org/         # shared, authoritative, agent-read-only (persona, playbook, policy)
├── teams/<id>/  # optional middle tier
└── users/<id>/  # private per-employee memory, owner read/write
```

This is the open-core path to running claw-like agents across a team or a whole company.

## Roadmap

### Shipped

- **Workflows** — durable, typed, multi-step routines (`@app.workflow()`); python / saved / llm-authored modes; runnable by the agent, a user (`/workflows`), or cron
- **Three-axis RBAC** — `app.role(tools=…, subagents=…, workflows=…)`; subagents and workflows are default-deny; enforced as middleware before the LLM
- **Named agents** — `app.agent()` registers independent agents (own model, tools, and history); switch with `/agent` (auto-routing still planned)
- **Code interpreter (opt-in)** — sandboxed JS `eval` so the agent can script tool loops, branches, and fan-out over an allowlist
- **Subagent delegation** — `app.subagent()` with isolated context and per-subagent model/tool sets; can stream results to the originating channel (`output="channel"`)
- **Guardrails middleware** — `ContentFilterMiddleware` (keyword/regex) and `PIIMiddleware` (redaction) in the built-in stack
- **Heartbeat / proactive wake-up** — event-driven condition checks that fire messages through the agent pipeline
- **Pluggable agent backend** — deepagents filesystem/shell backend selectable via `config.agents.backend` (local-shell / filesystem / state / store); defaults to `local_shell` for the `execute` tool

### In progress

- **Business workspaces** — one agent for many employees: a shared, immutable **org** layer + private per-**employee** memory, with a promotion path for good tactics. Design final; backend groundwork in `main`, layered read-path on a branch ([details ↑](#business-workspaces-preview))

### Planned

- **Multi-agent auto-routing** — route to a named agent automatically by channel or detected intent
- **More channels** — WhatsApp, REST API gateway
- **Plugin ecosystem** — `langclaw-*` tool packs installable via `uv add`
- **Observability** — OpenTelemetry tracing for the full message flow

## Contributing

```bash
git clone https://github.com/tisu19021997/langclaw.git
cd langclaw
uv sync --group dev
uv run pytest tests/ -v
uv run ruff check . && uv run ruff format .
```

## License

MIT — see [LICENSE](LICENSE) for details.
