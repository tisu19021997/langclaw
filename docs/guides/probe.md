# Probe Harness

The probe harness lets you drive features end-to-end through the **real** `gateway → bus → agent → channel` pipeline — without a human tapping on Telegram and without mocking anything out.

## Why this matters for agentic development

Testing an agent is not like testing a REST endpoint.

A single user turn passes through: the message bus, session lookup, agent-name resolution, every middleware layer (RBAC, rate limit, content filter, PII), the LangGraph agent loop, tool calls, and finally the outbound channel formatter. **The interesting bugs live in that pipeline**, not in the agent in isolation.

The standard alternatives miss most of it:

| Approach | What it covers | What it misses |
|---|---|---|
| `langclaw agent -m "…"` (CLI) | Agent + tools | Bus, session, middleware, command router, workflow→channel progress, active-agent switching |
| Unit tests on individual functions | Function correctness | Integration between components |
| Manual Telegram / Discord | Everything | Can't run in CI, no assertions, no repeatability |

The probe closes that gap: it injects a turn as a real WebSocket message, flows it through the full pipeline, and returns a structured, assertable list of events. **No Telegram credentials. No arbitrary sleeps. Deterministic turn completion.**

This is how langclaw's own workflow patterns cookbook was validated — every example in [`examples/workflow_patterns/`](https://github.com/tisu19021997/langclaw/tree/main/examples/workflow_patterns) was driven end-to-end through the probe before being documented.

---

## Three ways to use it

### 1. CLI — interactive or scripted

Start an isolated gateway (WebSocket only — every other channel is forced off so probe traffic never reaches a real bot):

```bash
langclaw gateway --probe
```

Then in another terminal:

```bash
langclaw probe "summarize today's HN"
langclaw probe "/reset"                                    # command path
langclaw probe "what is X?" --agent researcher --reset    # named agent + fresh thread
langclaw probe "/workflows run landscape {\"subject\": \"agent frameworks\", \"contenders\": [\"LangGraph\", \"CrewAI\"]}"
```

### 2. Script / notebook

```python
import asyncio
from langclaw.testing import probe, WebSocketProbeTransport, final_text

async def main():
    t = WebSocketProbeTransport("ws://127.0.0.1:18789")
    events = await probe("Say exactly: PONG", transport=t, reset=True)
    print(final_text(events))   # "PONG"

asyncio.run(main())
```

### 3. pytest

For CI, run `langclaw gateway --probe` as a fixture and probe it:

```python
import pytest
from langclaw.testing import probe, WebSocketProbeTransport, final_text

@pytest.fixture(scope="session")
async def gateway(langclaw_app):
    # start your app with gateway --probe, yield, then stop
    ...

async def test_greet_tool(gateway):
    events = await probe(
        "greet Alice",
        transport=WebSocketProbeTransport(),
        reset=True,
    )
    assert "Alice" in final_text(events)

async def test_reset_command(gateway):
    events = await probe("/reset", transport=WebSocketProbeTransport())
    assert any(e.type == "command" and e.is_final for e in events)
```

You can also inject a **fake transport** to test the probe's normalization logic without a live server:

```python
import asyncio
from langclaw.testing import probe

class FakeTransport:
    def __init__(self, frames): self.frames = frames
    async def open(self): ...
    async def close(self): ...
    async def send_turn(self, content, *, agent=None): ...
    async def receive(self):
        for f in self.frames: yield f

async def main():
    events = await probe("hi", transport=FakeTransport([
        {"type": "ai_chunk", "content": "Hel"},
        {"type": "ai_chunk", "content": "lo"},
        {"type": "ai_stream_end"},
    ]))
    assert events[-1].type == "ai"
    assert events[-1].content == "Hello"
    assert events[-1].is_final

asyncio.run(main())
```

---

## ProbeEvent

Every turn returns a `list[ProbeEvent]`:

| Field | Values | Notes |
|---|---|---|
| `type` | `ai` · `ai_chunk` · `tool_progress` · `tool_result` · `command` · `error` | |
| `content` | str | |
| `is_final` | bool | True on the terminal event of the turn |
| `metadata` | dict | `tool`, `tool_call_id`, `args`, … for tool events |
| `raw` | dict | The original frame, unmodified |

`ai_chunk` deltas stream individually **and** are assembled into a single final `ai` event on `ai_stream_end`. Assert on the stream or the assembled answer — both are there:

```python
chunks = [e for e in events if e.type == "ai_chunk"]
answer = final_text(events)   # assembled final text
```

`final_text(events)` — assembled answer string.
`format_events(events)` — labeled lines, same format the CLI prints.

---

## What `--probe` does differently from a normal gateway

`langclaw gateway --probe` forces every channel except WebSocket off **at the assembly seam** — your `.env` and config files are never touched. This means:

- No Telegram bot polling, no Discord connection, no Slack socket
- Probe traffic is isolated from live users
- Safe to run in CI alongside a production bot

---

## Transports

| Transport | Install | When to use |
|---|---|---|
| `WebSocketProbeTransport` | `langclaw[websocket]` (included by default) | Feature testing, CI — ~95% of use |
| `TelegramProbeTransport` | `langclaw[telegram-e2e]` | Smoke-test Telegram-specific rendering (Markdown, chunking, tool-progress formatting) |

The Telegram transport needs a **user** account (not a bot) — a bot can't DM another bot. A one-time human login mints a `TELETHON_SESSION` string stored in env. Because there's no stream-end signal over MTProto, turn completion is idle-timeout based — use it for occasional smoke tests, not per-feature CI.

---

## Honest limits

- The probe does not start or manage the gateway lifecycle — you start it explicitly.
- The WebSocket transport needs `langclaw[websocket]` (included in the base install).
- Telegram transport: `langclaw[telegram-e2e]` + `TELEGRAM_API_ID`, `TELEGRAM_API_HASH`, `TELETHON_SESSION` in env.
- Per-turn timeout defaults to 60 s. On expiry the probe appends a terminal `error` event and returns — it never hangs.
