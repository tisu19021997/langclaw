"""
Probe core — inject a user turn through the real channel pipeline and capture
the structured response.

The probe is a transport-agnostic test client. It builds a user turn, hands it
to an injected :class:`ProbeTransport`, collects the raw response frames,
detects when the turn is complete, and normalises the frames into
:class:`ProbeEvent` records an automated agent (or a developer) can assert on.

The transport is the injected, testable seam (mirroring the injected executor /
script-runner of the workflow runtime): swap WebSocket for Telegram with one
argument, or a fake transport in a unit test. The core owns turn-completion
detection and normalisation; every transport speaks the same WebSocket-style
frame contract:

    {"type": "ai_chunk", "content": "<delta>"}      # streaming token
    {"type": "ai_stream_end"}                         # streaming done (terminal)
    {"type": "ai", "content": "<full answer>"}        # non-streaming (terminal)
    {"type": "tool_progress", "content": ..., "metadata": {...}}
    {"type": "tool_result",   "content": ..., "metadata": {...}}
    {"type": "command", "content": "<reply>"}         # /command path (terminal)
    {"type": "error",   "content": "<message>"}       # error (terminal)

A frame is *terminal* — it ends the turn — when its ``type`` is ``ai``,
``ai_stream_end``, ``command`` or ``error``. Transports without a native
stream-end signal (e.g. Telegram) synthesise an ``ai_stream_end`` frame when
their idle window elapses, so the core's completion logic stays uniform.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

# Frame types that end a turn. ``ai_chunk`` / ``tool_progress`` / ``tool_result``
# are interstitial and never terminate collection.
_TERMINAL_TYPES = frozenset({"ai", "ai_stream_end", "command", "error"})

DEFAULT_TIMEOUT = 60.0
"""Per-turn safety-net timeout (seconds) — caps a hung agent."""


@dataclass(slots=True)
class ProbeEvent:
    """A normalised response event, isolated from per-transport frame quirks.

    Attributes:
        type: One of ``"ai"``, ``"ai_chunk"``, ``"tool_progress"``,
            ``"tool_result"``, ``"command"``, ``"error"`` (or the raw frame type
            for anything unrecognised).
        content: Text payload of the event.
        is_final: True for the terminal event of a turn (the assembled ``ai``
            answer, a ``command`` reply, or an ``error``).
        metadata: Passthrough metadata from the frame (e.g. ``tool``,
            ``tool_call_id``, ``args`` for tool events).
        raw: The original frame dict, for callers that need transport detail.
    """

    type: str
    content: str = ""
    is_final: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class ProbeTransport(Protocol):
    """The injected seam: how a probe turn reaches the pipeline and comes back.

    Implementations open a connection, send a turn, and yield raw response
    frames (WebSocket-style dicts). Completion detection and normalisation live
    in the core, so a transport only has to deliver frames in order.
    """

    async def open(self) -> None:
        """Establish the connection (idempotent-friendly; called once per probe)."""
        ...

    async def close(self) -> None:
        """Tear down the connection. Always called, even on error/timeout."""
        ...

    async def send_turn(self, content: str, *, agent: str | None = None) -> None:
        """Send one user turn. ``agent`` targets a named agent when supported."""
        ...

    def receive(self) -> AsyncIterator[dict[str, Any]]:
        """Yield raw response frames as they arrive, indefinitely.

        Called once per probe and shared across turns (e.g. a ``reset`` turn
        followed by the real turn), so it must not re-open the stream.
        """
        ...


def _is_terminal(frame: dict[str, Any]) -> bool:
    return frame.get("type") in _TERMINAL_TYPES


async def _drain_turn(
    frames: AsyncIterator[dict[str, Any]],
    timeout: float,
) -> list[dict[str, Any]]:
    """Collect frames until a terminal frame, the safety timeout, or stream end.

    On timeout a synthetic ``error`` frame is appended so the caller still sees
    whatever partial frames arrived plus an explicit terminal marker — the probe
    never hangs and never silently truncates.
    """
    collected: list[dict[str, Any]] = []

    async def _loop() -> None:
        async for frame in frames:
            collected.append(frame)
            if _is_terminal(frame):
                return

    try:
        await asyncio.wait_for(_loop(), timeout)
    except TimeoutError:
        collected.append(
            {
                "type": "error",
                "content": f"probe turn timed out after {timeout}s",
                "metadata": {"timeout": True},
            }
        )
    except StopAsyncIteration:
        # Transport stream ended without a terminal frame — return what we have.
        pass
    return collected


def normalize(frames: list[dict[str, Any]]) -> list[ProbeEvent]:
    """Turn raw transport frames into ordered :class:`ProbeEvent` records.

    ``ai_chunk`` deltas are emitted individually *and* assembled into a final
    ``ai`` event on ``ai_stream_end``, so callers can assert on either the
    streamed pieces or the complete answer.
    """
    events: list[ProbeEvent] = []
    chunks: list[str] = []

    for frame in frames:
        ftype = frame.get("type") or "unknown"
        content = frame.get("content", "") or ""
        meta = frame.get("metadata") or {}

        if ftype == "ai_chunk":
            chunks.append(content)
            events.append(ProbeEvent("ai_chunk", content, False, meta, frame))
        elif ftype == "ai_stream_end":
            events.append(ProbeEvent("ai", "".join(chunks), True, meta, frame))
            chunks = []
        elif ftype == "ai":
            events.append(ProbeEvent("ai", content, True, meta, frame))
        elif ftype in ("tool_progress", "tool_result"):
            events.append(ProbeEvent(ftype, content, False, meta, frame))
        elif ftype == "command":
            events.append(ProbeEvent("command", content, True, meta, frame))
        elif ftype == "error":
            events.append(ProbeEvent("error", content, True, meta, frame))
        else:
            events.append(ProbeEvent(ftype, content, False, meta, frame))

    return events


async def probe(
    content: str,
    *,
    transport: ProbeTransport,
    agent: str | None = None,
    reset: bool = False,
    timeout: float = DEFAULT_TIMEOUT,
) -> list[ProbeEvent]:
    """Inject a user turn through the pipeline and return the response events.

    Args:
        content: The user message to send (a leading ``/`` routes to the
            command path, just like a real channel).
        transport: The injected transport driving the turn (WebSocket, Telegram,
            or a fake in tests).
        agent: Optional named-agent target, stamped as ``agent_name`` metadata.
        reset: When True, send ``/reset`` first (and discard its events) so prior
            history does not bleed into this turn.
        timeout: Per-turn safety-net timeout in seconds.

    Returns:
        Ordered :class:`ProbeEvent` records for the turn. The final event is the
        terminal one (assembled ``ai`` answer, ``command`` reply, or ``error``).
    """
    await transport.open()
    try:
        frames = transport.receive()
        if reset:
            await transport.send_turn("/reset")
            await _drain_turn(frames, timeout)  # discard the reset acknowledgement
        await transport.send_turn(content, agent=agent)
        raw = await _drain_turn(frames, timeout)
    finally:
        await transport.close()
    return normalize(raw)


def final_text(events: list[ProbeEvent]) -> str:
    """Return the assembled final AI answer from a probe result, or ``""``."""
    for event in reversed(events):
        if event.type == "ai" and event.is_final:
            return event.content
    return ""


_LABELS = {
    "ai": "AI",
    "ai_chunk": "ai·chunk",
    "tool_progress": "tool→",
    "tool_result": "tool✓",
    "command": "command",
    "error": "ERROR",
}


def format_events(events: list[ProbeEvent]) -> str:
    """Render events as readable, labeled lines for the CLI / `-c` use."""
    lines: list[str] = []
    for event in events:
        label = _LABELS.get(event.type, event.type)
        if event.type == "tool_progress":
            tool = event.metadata.get("tool", "")
            suffix = f" [{tool}]" if tool else ""
            lines.append(f"  {label}{suffix} {event.content}".rstrip())
        else:
            text = event.content.replace("\n", "\n        ")
            lines.append(f"  {label:>9}  {text}".rstrip())
    return "\n".join(lines)
