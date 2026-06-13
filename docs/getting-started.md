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
# LLM provider — a plain provider env var, NOT a LANGCLAW__ key.
# The default model is Anthropic, so set ANTHROPIC_API_KEY:
ANTHROPIC_API_KEY=sk-ant-...
# (Use OPENAI_API_KEY / GOOGLE_API_KEY / etc. if you pick that provider's model.)

# Telegram channel
LANGCLAW__CHANNELS__TELEGRAM__ENABLED=true
LANGCLAW__CHANNELS__TELEGRAM__TOKEN=123456:ABC-DEF...

# Model (default: anthropic:claude-sonnet-4-5-20250929)
LANGCLAW__AGENTS__MODEL=openai:gpt-4.1

# Persistent state (default: SQLite)
LANGCLAW__CHECKPOINTER__BACKEND=sqlite
```

!!! note "Model and provider key must match"
    `LANGCLAW__AGENTS__MODEL` selects the provider (`anthropic:…`, `openai:…`, `google:…`).
    Set the matching plain env var for that provider's API key. `langclaw status`
    shows which provider keys it can see.

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

Langclaw starts the message bus, connects any enabled channels, and begins listening. With a valid API key and Telegram token set, open Telegram and talk to your bot.

## Talk to it locally — no channel needed

You don't need a chat app to try your agent. The CLI gives you a REPL and a one-shot mode that run your default agent directly:

```bash
langclaw agent                       # interactive REPL
langclaw agent -m "reverse 'hello'"  # single message, print the reply
```

Only `ANTHROPIC_API_KEY` (or your chosen provider's key) is required for these.

## CLI quickstart

```bash
langclaw init      # scaffold ~/.langclaw/ (config.json, AGENTS.md, skills) and install deps
langclaw gateway   # start the built-in agent from ~/.langclaw/ across all enabled channels
langclaw status    # check provider keys + channel + bus health
```

!!! warning "`langclaw gateway` runs the built-in agent, not your `app.py`"
    `langclaw init` + `langclaw gateway` drive a config-only agent rooted at
    `~/.langclaw/` — they do **not** load tools/commands/subagents you registered
    in a Python file. To run *your* app (everything decorated on your `Langclaw`
    object), use `python app.py`. The two paths are separate by design.

## Next steps

<div class="grid cards" markdown>

-   [:octicons-tools-16: **Tools**](guides/tools.md) — register custom tools with `@app.tool()`
-   [:octicons-broadcast-16: **Channels**](guides/channels.md) — add and configure channels
-   [:octicons-workflow-16: **Workflows**](guides/workflows.md) — durable multi-step routines
-   [:octicons-people-16: **RBAC**](guides/rbac.md) — role-based access control
-   [:octicons-clock-16: **Cron**](guides/cron.md) — scheduled jobs

</div>
