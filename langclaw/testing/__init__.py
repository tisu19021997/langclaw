"""
langclaw.testing — the probe harness.

Inject a user turn through the *real* ``gateway → bus → agent → channel``
pipeline and capture the structured response, so an automated agent (or a
developer) can drive features end-to-end without a human tapping on Telegram.

Three ways in:
  - ``langclaw probe "message"`` CLI
  - ``from langclaw.testing import probe`` for ``uv run python -c`` / pytest
  - ``langclaw gateway --probe`` to run a WebSocket-only, isolated gateway

The transport is the swappable, testable seam::

    from langclaw.testing import probe, WebSocketProbeTransport

    events = await probe("hello", transport=WebSocketProbeTransport())
    assert any(e.type == "ai" and e.is_final for e in events)

``TelegramProbeTransport`` requires the ``telegram-e2e`` extra; importing it lazily
keeps the core import dependency-free.
"""

from __future__ import annotations

from langclaw.testing.probe import (
    DEFAULT_TIMEOUT,
    ProbeEvent,
    ProbeTransport,
    final_text,
    format_events,
    normalize,
    probe,
)
from langclaw.testing.ws import WebSocketProbeTransport

__all__ = [
    "DEFAULT_TIMEOUT",
    "ProbeEvent",
    "ProbeTransport",
    "WebSocketProbeTransport",
    "final_text",
    "format_events",
    "normalize",
    "probe",
]


def __getattr__(name: str) -> object:
    # Lazy: TelegramProbeTransport pulls in telethon only when actually used.
    if name == "TelegramProbeTransport":
        from langclaw.testing.telegram import TelegramProbeTransport

        return TelegramProbeTransport
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
