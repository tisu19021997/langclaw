"""
Tests for the probe harness (``langclaw.testing``).

The probe core is the primary unit-test target: an injected fake transport
yields a canned frame sequence, and we assert the normalised, ordered
:class:`ProbeEvent` list plus correct turn-completion and timeout behaviour —
without a live server. The WS driver is covered by a round-trip against a stub
server; the Telegram idle-completion logic against a fake message source.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

import pytest

from langclaw.testing import (
    ProbeEvent,
    WebSocketProbeTransport,
    final_text,
    format_events,
    normalize,
    probe,
)
from langclaw.testing.telegram import stream_until_idle

# ---------------------------------------------------------------------------
# Fake transports
# ---------------------------------------------------------------------------


class _FakeTransport:
    """Emits a canned frame sequence; records what was sent."""

    def __init__(self, frames: list[dict[str, Any]]) -> None:
        self._frames = frames
        self.opened = False
        self.closed = False
        self.sent: list[tuple[str, str | None]] = []

    async def open(self) -> None:
        self.opened = True

    async def close(self) -> None:
        self.closed = True

    async def send_turn(self, content: str, *, agent: str | None = None) -> None:
        self.sent.append((content, agent))

    async def receive(self) -> AsyncIterator[dict[str, Any]]:
        for frame in self._frames:
            yield frame


class _HangingTransport:
    """Yields one interstitial frame, then never completes the turn."""

    def __init__(self) -> None:
        self.closed = False

    async def open(self) -> None:
        pass

    async def close(self) -> None:
        self.closed = True

    async def send_turn(self, content: str, *, agent: str | None = None) -> None:
        pass

    async def receive(self) -> AsyncIterator[dict[str, Any]]:
        yield {"type": "tool_progress", "content": "working"}
        await asyncio.Event().wait()  # hang


# ---------------------------------------------------------------------------
# Probe core (primary)
# ---------------------------------------------------------------------------


async def test_streaming_turn_normalizes_and_terminates():
    frames = [
        {"type": "tool_progress", "content": "searching", "metadata": {"tool": "web_search"}},
        {"type": "ai_chunk", "content": "Hel"},
        {"type": "ai_chunk", "content": "lo"},
        {"type": "ai_stream_end"},
        {"type": "ai_chunk", "content": "SHOULD NOT APPEAR"},  # past terminal
    ]
    transport = _FakeTransport(frames)

    events = await probe("hi", transport=transport)

    assert [e.type for e in events] == ["tool_progress", "ai_chunk", "ai_chunk", "ai"]
    # ai_stream_end assembles the chunk deltas into the final answer
    assert events[-1].is_final
    assert events[-1].content == "Hello"
    assert final_text(events) == "Hello"
    # tool metadata passes through
    assert events[0].metadata["tool"] == "web_search"
    # lifecycle + send
    assert transport.opened and transport.closed
    assert transport.sent == [("hi", None)]


async def test_non_streaming_single_ai():
    transport = _FakeTransport([{"type": "ai", "content": "full answer"}])

    events = await probe("hi", transport=transport)

    assert [e.type for e in events] == ["ai"]
    assert events[0].is_final and events[0].content == "full answer"


async def test_timeout_fires_when_no_completion():
    transport = _HangingTransport()

    events = await probe("hi", transport=transport, timeout=0.05)

    # partial frame preserved, then a terminal error appended
    assert events[0].type == "tool_progress"
    assert events[-1].type == "error"
    assert events[-1].is_final
    assert "timed out" in events[-1].content.lower()
    assert transport.closed  # cleanup still ran


async def test_reset_sends_reset_first_and_discards_its_events():
    # one shared frame stream: the /reset turn (command), then the real turn (ai)
    frames = [
        {"type": "command", "content": "Conversation reset."},
        {"type": "ai", "content": "hi back"},
    ]
    transport = _FakeTransport(frames)

    events = await probe("hello", transport=transport, reset=True)

    assert transport.sent == [("/reset", None), ("hello", None)]
    # only the real turn's events come back
    assert [e.type for e in events] == ["ai"]
    assert events[0].content == "hi back"


async def test_agent_name_passthrough():
    transport = _FakeTransport([{"type": "ai", "content": "ok"}])

    await probe("hi", transport=transport, agent="researcher")

    assert transport.sent[-1] == ("hi", "researcher")


async def test_command_and_error_frames_are_terminal():
    cmd = await probe("/agent", transport=_FakeTransport([{"type": "command", "content": "x"}]))
    assert cmd[-1].type == "command" and cmd[-1].is_final

    err = await probe("hi", transport=_FakeTransport([{"type": "error", "content": "boom"}]))
    assert err[-1].type == "error" and err[-1].is_final


# ---------------------------------------------------------------------------
# Normalization helpers
# ---------------------------------------------------------------------------


def test_normalize_tool_result_passthrough():
    events = normalize(
        [{"type": "tool_result", "content": "done", "metadata": {"tool_call_id": "c1"}}]
    )
    assert events[0].type == "tool_result"
    assert events[0].metadata["tool_call_id"] == "c1"
    assert not events[0].is_final


def test_format_events_is_readable():
    events = [
        ProbeEvent("tool_progress", "looking up", metadata={"tool": "web_search"}),
        ProbeEvent("ai", "the answer", is_final=True),
    ]
    text = format_events(events)
    assert "web_search" in text
    assert "the answer" in text


# ---------------------------------------------------------------------------
# Telegram idle-completion logic (no live Telethon)
# ---------------------------------------------------------------------------


async def test_stream_until_idle_ends_on_exhaustion():
    async def source() -> AsyncIterator[str]:
        yield "hello"
        yield "world"

    frames = [f async for f in stream_until_idle(source(), idle_timeout=0.5)]

    assert [f["type"] for f in frames] == ["ai_chunk", "ai_chunk", "ai_stream_end"]
    assert frames[0]["content"] == "hello"
    assert frames[1]["content"] == "world"


async def test_stream_until_idle_ends_on_silence_gap():
    async def slow_source() -> AsyncIterator[str]:
        yield "first"
        await asyncio.sleep(0.3)  # exceeds idle window — turn ends before "late"
        yield "late"

    frames = [f async for f in stream_until_idle(slow_source(), idle_timeout=0.05)]

    assert [f["type"] for f in frames] == ["ai_chunk", "ai_stream_end"]
    assert frames[0]["content"] == "first"


async def test_telegram_idle_frames_feed_core_normalization():
    # The Telegram transport yields ai_chunk-per-message + ai_stream_end, so the
    # core assembles them into one final ai answer exactly like the WS path.
    async def source() -> AsyncIterator[str]:
        yield "part one. "
        yield "part two."

    frames = [f async for f in stream_until_idle(source(), idle_timeout=0.5)]
    events = normalize(frames)

    assert final_text(events) == "part one. part two."


# ---------------------------------------------------------------------------
# WS driver — round-trip against a stub server (integration)
# ---------------------------------------------------------------------------


async def test_ws_driver_roundtrip_streaming():
    websockets = pytest.importorskip("websockets")

    async def handler(ws: Any) -> None:
        async for raw in ws:
            data = json.loads(raw)
            await ws.send(json.dumps({"type": "ai_chunk", "content": "Hi "}))
            await ws.send(json.dumps({"type": "ai_chunk", "content": data["content"]}))
            await ws.send(json.dumps({"type": "ai_stream_end"}))

    server = await websockets.serve(handler, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        transport = WebSocketProbeTransport(f"ws://127.0.0.1:{port}")
        events = await probe("there", transport=transport)
        assert events[-1].type == "ai"
        assert events[-1].content == "Hi there"
    finally:
        server.close()
        await server.wait_closed()


async def test_ws_driver_passes_agent_metadata():
    websockets = pytest.importorskip("websockets")

    seen: dict[str, Any] = {}

    async def handler(ws: Any) -> None:
        async for raw in ws:
            data = json.loads(raw)
            seen.update(data)
            await ws.send(json.dumps({"type": "ai", "content": "ok"}))

    server = await websockets.serve(handler, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        transport = WebSocketProbeTransport(f"ws://127.0.0.1:{port}", user_id="u1")
        await probe("hi", transport=transport, agent="researcher")
        assert seen["metadata"]["agent_name"] == "researcher"
        assert seen["user_id"] == "u1"
    finally:
        server.close()
        await server.wait_closed()


# ---------------------------------------------------------------------------
# gateway --probe mode — channel assembly (light)
# ---------------------------------------------------------------------------


def test_probe_mode_assembles_websocket_only():
    pytest.importorskip("websockets")
    from langclaw.app import Langclaw
    from langclaw.config.schema import LangclawConfig

    cfg = LangclawConfig()
    cfg.channels.telegram.enabled = True  # would normally add a Telegram channel
    cfg.channels.websocket.enabled = False  # off in config...

    lc = Langclaw(config=cfg)
    lc._probe_ws_only = True
    lc._probe_port = 19999

    channels = lc._build_all_channels()

    assert [c.name for c in channels] == ["websocket"]  # ...but probe forces WS-only
    assert channels[0]._config.port == 19999
    assert channels[0]._config.enabled is True
    # user config object untouched
    assert cfg.channels.websocket.enabled is False
    assert cfg.channels.telegram.enabled is True
