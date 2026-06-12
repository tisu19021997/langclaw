# Getting Started

## Installation

```bash
uv add langclaw
```

Install channel extras you need:

```bash
uv add "langclaw[telegram]"          # Telegram
uv add "langclaw[discord]"           # Discord
uv add "langclaw[slack]"             # Slack
uv add "langclaw[websocket]"         # WebSocket (included with the base package)
uv add "langclaw[telegram,postgres]" # multiple extras
uv add "langclaw[all]"               # everything
```

## Configuration

Langclaw reads config from environment variables with the `LANGCLAW__` prefix, nested with `__` delimiters. Drop them in a `.env` file:

```env title=".env"
# LLM provider
LANGCLAW__PROVIDERS__OPENAI__API_KEY=sk-...

# Telegram channel
LANGCLAW__CHANNELS__TELEGRAM__ENABLED=true
LANGCLAW__CHANNELS__TELEGRAM__BOT_TOKEN=123456:ABC-DEF...

# Model (default: openai:gpt-4o-mini)
LANGCLAW__AGENTS__MODEL=openai:gpt-4.1

# Persistent state (default: SQLite)
LANGCLAW__CHECKPOINTER__BACKEND=sqlite
```

## Hello world

```python title="app.py"
from langclaw import Langclaw

app = Langclaw(
    system_prompt="You are a helpful assistant. Keep answers short."
)

@app.tool()
async def reverse_text(text: str) -> str:
    """Reverse the given text."""
    return text[::-1]

if __name__ == "__main__":
    app.run()
```

```bash
python app.py
```

Langclaw starts the message bus, connects any enabled channels, and begins listening. Open Telegram and talk to your bot.

## CLI quickstart

```bash
langclaw init      # scaffold app.py + .env in the current directory
langclaw gateway   # start the gateway (equivalent to python app.py)
langclaw status    # check channel + bus health
```

## Next steps

<div class="grid cards" markdown>

-   [:octicons-tools-16: **Tools**](guides/tools.md) — register custom tools with `@app.tool()`
-   [:octicons-broadcast-16: **Channels**](guides/channels.md) — add and configure channels
-   [:octicons-workflow-16: **Workflows**](guides/workflows.md) — durable multi-step routines
-   [:octicons-people-16: **RBAC**](guides/rbac.md) — role-based access control
-   [:octicons-clock-16: **Cron**](guides/cron.md) — scheduled jobs

</div>
