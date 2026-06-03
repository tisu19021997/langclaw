# Probe Harness

Drive features end-to-end through the **real** channel pipeline — without a
human tapping on Telegram.

`langclaw agent -m "…"` builds the agent directly and bypasses the gateway, bus,
command router, channel-context middleware, session/active-agent resolution, and
the workflow→channel progress projection. The **probe** injects a user turn
through the actual `gateway → bus → agent → channel` path and returns the
response as a structured, assertable event list.

```
┌── probe(content, transport) ──────────────────────────────────────────┐
│  ProbeTransport (injected seam)        Probe core (deep module)        │
│  ─────────────────────────────         ──────────────────────────     │
│  WebSocketProbeTransport  ──frames──▶  collect → detect terminal →     │
│  TelegramProbeTransport               normalize → list[ProbeEvent]     │
│  <fake in tests>                                                       │
└────────────────────────────────────────────────────────────────────────┘
```

## Two transports, one interface

- **WebSocket (the workhorse).** Connects to a running gateway as an ordinary WS
  client. Deterministic: the channel emits `{"type":"ai_stream_end"}` on the
  final chunk, so the probe knows exactly when a turn is done — no arbitrary
  sleeps. Reuses the existing WS JSON contract verbatim; **no change to
  `WebSocketChannel`**. Used for ~95% of feature testing.
- **Telegram (narrow smoke).** A Telegram *user* account (Telethon) sends to the
  bot and reads its replies — only to catch Telegram-specific rendering
  (Markdown, chunking, tool-progress formatting). No stream-end signal, so a turn
  completes by *idle-timeout*; the transport synthesises an `ai_stream_end` frame
  at that point so the core's completion logic stays identical. Occasional use.

Both implement the `ProbeTransport` protocol (`open` / `close` / `send_turn` /
`receive`). The transport is the injected, testable seam — mirroring the
injected executor / script-runner of the workflow runtime.

## Three ways in

**1. Run an isolated gateway, then probe it.** `--probe` runs a WebSocket-only
gateway with every other channel forced off (applied at the channel-assembly
seam — your config file is never mutated), so test traffic never reaches a real
Telegram/Discord chat:

```bash
langclaw gateway --probe                       # WS-only on the configured port
langclaw probe "summarize today's news"        # in another terminal
langclaw probe "/reset"                         # command path (bypasses bus+LLM)
langclaw probe "what's X?" --agent researcher --reset
langclaw probe "render this" --transport telegram --bot @my_test_bot
```

**2. Importable function** — for `uv run python -c` or any script:

```python
import asyncio
from langclaw.testing import probe, WebSocketProbeTransport, final_text

async def main():
    events = await probe(
        "Say exactly: HELLO",
        transport=WebSocketProbeTransport("ws://127.0.0.1:18789"),
        reset=True,
    )
    print(final_text(events))   # -> "HELLO"

asyncio.run(main())
```

**3. pytest helper** — inject a fake transport that yields a canned frame
sequence and assert on the normalized events (no live server):

```python
from langclaw.testing import probe

class FakeTransport:
    def __init__(self, frames): self.frames = frames
    async def open(self): ...
    async def close(self): ...
    async def send_turn(self, content, *, agent=None): ...
    async def receive(self):
        for f in self.frames: yield f

events = await probe("hi", transport=FakeTransport([
    {"type": "ai_chunk", "content": "Hel"},
    {"type": "ai_chunk", "content": "lo"},
    {"type": "ai_stream_end"},
]))
assert events[-1].type == "ai" and events[-1].content == "Hello"
```

## ProbeEvent

A normalized record, isolating callers from per-transport frame differences:

| field | meaning |
|---|---|
| `type` | `ai` \| `ai_chunk` \| `tool_progress` \| `tool_result` \| `command` \| `error` |
| `content` | text payload |
| `is_final` | terminal event of the turn (assembled `ai`, `command`, or `error`) |
| `metadata` | passthrough (`tool`, `tool_call_id`, `args`, …) |
| `raw` | the original frame dict |

`ai_chunk` deltas are emitted individually **and** assembled into a final `ai`
event on `ai_stream_end`, so you can assert on either the stream or the answer.
`final_text(events)` returns the assembled answer; `format_events(events)`
renders the labeled lines the CLI prints.

A frame ends the turn when its `type` is `ai`, `ai_stream_end`, `command`, or
`error`. A per-turn `timeout` (default 60s) is a safety net: on expiry the probe
appends a terminal `error` event and returns whatever arrived — it never hangs
and never silently truncates.

## Honest limits

- The WS transport needs the `websocket` extra; the Telegram transport needs the
  `telegram-e2e` extra (`telethon`) plus a one-time human login that mints a
  `TELETHON_SESSION` string (a bot cannot DM another bot — simulating a user
  needs a *user* account via MTProto). Secrets via gitignored env:
  `TELEGRAM_API_ID`, `TELEGRAM_API_HASH`, `TELETHON_SESSION`.
- The Telegram path is bounded by idle-timeout, not a stream-end signal, so it is
  occasional smoke, not per-feature testing.
- The probe does not start or lifecycle-manage the gateway — you start it
  explicitly (e.g. `langclaw gateway --probe`).
