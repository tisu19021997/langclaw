# Langclaw

Multi-channel AI agent **framework** (not an app) built on LangChain, LangGraph, and deepagents. Developers `pip install langclaw` and build on top of it — think Flask/FastAPI for agentic systems. Python 3.11+.

## Package Map

| Package | Purpose |
|---|---|
| `app.py` | `Langclaw` class — developer's primary interface (decorators, lifecycle, wiring) |
| `agents/` | LangGraph agent construction, tool wiring, subagent delegation, pluggable deepagents filesystem/shell backend (`backend.py`) |
| `gateway/` | Channel orchestration (`GatewayManager`), command routing, message dispatch |
| `bus/` | Message bus abstraction — asyncio (dev), RabbitMQ, Kafka (prod) |
| `middleware/` | Request pipeline: RBAC, rate limit, content filter, PII redaction |
| `interpreter/` | Opt-in sandboxed code interpreter (RLM) — PTC allowlist resolver + `CodeInterpreterMiddleware` factory |
| `config/` | Pydantic Settings with `LANGCLAW__` env var prefix (nested `__` delimiter) |
| `cron/` | Scheduled jobs via APScheduler v4 |
| `session/` | Maps (channel, user, context) → LangGraph thread IDs |
| `checkpointer/` | Conversation state persistence — SQLite (dev), Postgres (prod) |
| `providers/` | LLM model resolution via `init_chat_model` |
| `cli/` | Typer CLI: `langclaw gateway`, `langclaw agent`, `langclaw cron`, `langclaw status` |

## Development

```bash
uv sync --group dev                    # Install all deps
uv run pytest tests/ -v                # Run tests
uv run ruff check . && uv run ruff format .  # Lint + format
uv run pre-commit run --all-files      # Full pre-commit suite
```

## Architecture

Read `docs/ARCHITECTURE.md` for design principles and rationale. Critical invariants:

- **Message flow:** Channel → `InboundMessage` → Bus → `GatewayManager` → Middleware → Agent → `OutboundMessage` → Channel
- **Commands** (`/start`, `/reset`, `/help`) bypass the bus and LLM — handled by `CommandRouter` in `gateway/commands.py`
- **Cron jobs** publish `InboundMessage` to the same bus, flowing through the identical agent pipeline
- **Pluggable backends** always follow: abstract `base.py` + factory function (`make_message_bus`, `make_checkpointer_backend`, `make_backend` for the agent filesystem/shell backend)
- **Middleware order matters** — see `agents/builder.py` for the stack composition
- **Explicit registration** over auto-discovery — tools, channels, middleware are registered on the `Langclaw` app object

## Conventions

- `from __future__ import annotations` in every module
- `TYPE_CHECKING` guard for import-only types
- Modern type syntax: `list[T]`, `dict[K, V]`, `str | None` — never `typing.List`, `Optional`
- `loguru.logger` for all application logging (not stdlib `logging`)
- Google-style docstrings (Args/Returns/Raises)
- Tools return `{"error": "..."}` dicts on failure — never raise into the agent
- Ruff handles formatting and linting — see `[tool.ruff]` in `pyproject.toml`
