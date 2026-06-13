# langclaw

**Build production-ready, role-gated Claws.**

A **Claw** is a running langclaw agent — the configured, multi-channel, role-gated assistant your `Langclaw` app object produces.

`uv add langclaw` and build on top of it — like FastAPI, but for agents. Define tools, roles, channels, and workflows on one app object. Langclaw wires up the message bus, middleware, state persistence, and channel routing. You write the agent logic.

<div class="grid cards" markdown>

-   :material-flash:{ .lg .middle } **Minimal to start**

    ---

    One file, one object. Channel config lives in `.env`.

    ```python
    from langclaw import Langclaw

    app = Langclaw()

    @app.tool()
    async def greet(name: str) -> str:
        """Say hello."""
        return f"Hello, {name}!"

    app.run()
    ```

-   :material-transit-connection-variant:{ .lg .middle } **Multi-channel from day one**

    ---

    Telegram, Discord, Slack, WebSocket — and custom channels with one subclass.

    [:octicons-arrow-right-24: Channels guide](guides/channels.md)

-   :material-graph:{ .lg .middle } **Durable workflows**

    ---

    Typed, multi-step, crash-resumable. Fan out with `ctx.parallel`, call the model directly with `ctx.llm`, delegate to isolated subagents. Every workflow is also a tool and a cron target.

    [:octicons-arrow-right-24: Workflows guide](guides/workflows.md)

-   :material-shield-lock:{ .lg .middle } **Declarative RBAC**

    ---

    Gate tools, subagents, *and* workflows per role. Default-deny. Middleware runs before the LLM sees anything.

    [:octicons-arrow-right-24: RBAC guide](guides/rbac.md)

-   :material-clock-outline:{ .lg .middle } **Scheduled jobs**

    ---

    Ask the agent to schedule recurring tasks — or fire a saved workflow on cron — both hit the same pipeline as user messages.

    [:octicons-arrow-right-24: Cron guide](guides/cron.md)

-   :material-swap-horizontal:{ .lg .middle } **Pluggable backends**

    ---

    Swap message bus (asyncio → RabbitMQ → Kafka), checkpointer (SQLite → Postgres), and LLM provider via env vars, not code.

</div>

## Installation

```bash
uv add langclaw

# with extras
uv add "langclaw[telegram,postgres]"
uv add "langclaw[all]"
```

Available extras:

- **Channels:** `telegram` · `telegram-e2e` · `discord` · `slack` · `matrix` · `websocket`
- **Backends:** `postgres` · `rabbitmq` · `kafka`
- **Capabilities:** `search` · `mcp` · `gmail` · `interpreter`
- **`all`** — every extra except `telegram-e2e`

## Quick example

```python
from langclaw import Langclaw
from langclaw.gateway.commands import CommandContext

app = Langclaw(
    system_prompt="You are a financial research assistant.",
)

@app.tool()
async def get_stock_price(ticker: str) -> dict:
    """Fetch the latest quote for a US stock ticker."""
    return {"ticker": ticker, "price": 182.52, "change_pct": "+1.23%"}

app.subagent(
    "deep-researcher",
    description="Multi-step research with web search",
    system_prompt="You are a thorough researcher. Search, synthesise, cite.",
    tools=["web_search", "web_fetch"],
    output="channel",
)

app.role("analyst", tools=["*"])
app.role("free", tools=["web_search"])

@app.command("watchlist")
async def watchlist_cmd(ctx: CommandContext) -> str:
    return "AAPL: $182.52 | MSFT: $441.20"

app.run()
```

[:octicons-arrow-right-24: Full getting started guide](getting-started.md){ .md-button .md-button--primary }
[:octicons-arrow-right-24: Examples on GitHub](https://github.com/tisu19021997/langclaw/tree/main/examples){ .md-button }
