# Langclaw

### Multi-channel AI agent framework, the LangChain way

[![PyPI version](https://img.shields.io/pypi/v/langclaw)](https://pypi.org/project/langclaw/)
[![Python versions](https://img.shields.io/pypi/pyversions/langclaw)](https://pypi.org/project/langclaw/)
[![License](https://img.shields.io/github/license/tisu19021997/langclaw)](https://github.com/tisu19021997/langclaw/blob/main/LICENSE)

---

**Repository**: [github.com/tisu19021997/langclaw](https://github.com/tisu19021997/langclaw)

---

**Langclaw is a Python framework for building production-grade, multi-channel AI agent systems — with RBAC, scheduled tasks, persistent memory, subagent delegation, and a pluggable tool ecosystem — on top of [LangChain](https://github.com/langchain-ai/langchain), [LangGraph](https://github.com/langchain-ai/langgraph), and [deepagents](https://github.com/tisu19021997/deepagents).**

FastAPI gave web developers a declarative, decorator-driven way to build APIs. Langclaw brings that same feeling to multi-channel agentic systems. Define tools, roles, subagents, and channels on a single app object — langclaw handles the wiring, middleware, message routing, and state persistence so you can focus on what your agent actually does.

## Why Use Langclaw

1. **Framework, not a fork**: `pip install langclaw` and build on top of it — like Flask/FastAPI for agentic systems. No repo cloning, no boilerplate.
2. **Multi-channel from day one**: Telegram, Discord, WebSocket out of the box. Add custom channels with a single `app.add_channel()` call.
3. **Declarative RBAC**: `app.role("analyst", tools=["*"])` — one line to define who can use what. Permissions are enforced as middleware before the LLM sees anything.
4. **Subagent delegation**: Register specialist subagents that run in isolated contexts. The main agent delegates via a built-in `task` tool; results flow back cleanly or stream directly to the channel.
5. **Scheduled jobs**: Users can ask the agent to schedule recurring tasks. Cron jobs publish to the same message bus and flow through the same pipeline as user messages.
6. **Pluggable everything**: Message bus (asyncio / RabbitMQ / Kafka), checkpointer (SQLite / Postgres), LLM providers — swap backends via config, not code changes.
7. **Middleware pipeline**: Content filtering, PII redaction, rate limiting, and RBAC run as composable middleware before every LLM call.
8. **Built on LangChain + LangGraph**: Not a wrapper — langclaw compiles down to a real LangGraph `CompiledStateGraph`. Bring any LangChain tool, model, or integration.

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

## Message Flow

Every message — whether from a user or a cron job — follows the same path:

```
Channel (Telegram / Discord / WebSocket)
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
pip install langclaw
```

With channel and backend extras:

```bash
pip install "langclaw[telegram,postgres,rabbitmq]"

# Or install everything:
pip install "langclaw[all]"
```

Available extras: `telegram`, `discord`, `websocket`, `postgres`, `rabbitmq`, `kafka`, `mcp`, `search`, `gmail`.

## Packages

| Package | Purpose |
|---|---|
| `app.py` | `Langclaw` class — the developer's primary interface (decorators, lifecycle, wiring) |
| `agents/` | LangGraph agent construction, tool wiring, subagent delegation |
| `gateway/` | Channel orchestration (`GatewayManager`), command routing, message dispatch |
| `bus/` | Message bus abstraction — asyncio (dev), RabbitMQ / Kafka (prod) |
| `middleware/` | Request pipeline: RBAC, rate limit, content filter, PII redaction |
| `config/` | Pydantic Settings with `LANGCLAW__` env prefix (nested `__` delimiter) |
| `cron/` | Scheduled jobs via APScheduler v4 |
| `session/` | Maps (channel, user, context) to LangGraph thread IDs |
| `checkpointer/` | Conversation state persistence — SQLite (dev), Postgres (prod) |
| `providers/` | LLM model resolution via `init_chat_model` |
| `cli/` | Typer CLI: `langclaw gateway`, `langclaw agent`, `langclaw cron`, `langclaw status` |

## Roadmap

### Shipped

- **Subagent delegation** — `app.subagent()` registers child agents with isolated context and per-subagent model/tool sets
- **Channel-routed subagents** — subagents can publish results directly to the originating channel (`output="channel"`)
- **Guardrails middleware** — `ContentFilterMiddleware` (keyword/regex) and `PIIMiddleware` (redaction) in the built-in stack
- **Heartbeat / proactive wake-up** — event-driven condition checks that fire messages through the agent pipeline

### Planned

- **Multi-agent routing** — named agents with distinct models, routed by channel or user intent
- **More channels** — Slack, WhatsApp, REST API gateway
- **Plugin ecosystem** — `langclaw-*` tool packs installable via pip
- **Observability** — OpenTelemetry tracing for the full message flow
- **Test coverage** — comprehensive tests across all modules

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
