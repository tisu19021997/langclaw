"""
WebSocket probe transport — the workhorse.

Connects to a running gateway's ``WebSocketChannel`` (``ws://<host>:<port>``) as
an ordinary client and drives it through the real ``gateway → bus → agent →
channel`` pipeline. Deterministic: the channel emits ``{"type":"ai_stream_end"}``
on the final chunk (streaming on) or a single ``{"type":"ai"}`` (streaming off),
so the probe knows exactly when a turn is done — no arbitrary sleeps.

Requires no change to ``WebSocketChannel``: it reuses the existing JSON wire
contract verbatim. Needs the ``websockets`` client (``langclaw[websocket]``).
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any


class WebSocketProbeTransport:
    """Drive a turn over a live WebSocket gateway connection.

    Args:
        url: The gateway WebSocket URL, e.g. ``ws://127.0.0.1:18789``.
        user_id: Client identity. The channel routes replies back to the
            ``(user_id, context_id)`` pair, so the probe must echo these.
        context_id: LangGraph thread/session key for the turn.
    """

    def __init__(
        self,
        url: str = "ws://127.0.0.1:18789",
        *,
        user_id: str = "probe",
        context_id: str = "default",
    ) -> None:
        self._url = url
        self._user_id = user_id
        self._context_id = context_id
        self._ws: Any = None

    async def open(self) -> None:
        try:
            import websockets
        except ImportError as exc:  # pragma: no cover - import guard
            raise ImportError(
                "WebSocketProbeTransport requires 'websockets'. "
                "Install with: uv add 'langclaw[websocket]'"
            ) from exc
        self._ws = await websockets.connect(self._url)

    async def close(self) -> None:
        if self._ws is not None:
            await self._ws.close()
            self._ws = None

    async def send_turn(self, content: str, *, agent: str | None = None) -> None:
        frame: dict[str, Any] = {
            "type": "message",
            "content": content,
            "user_id": self._user_id,
            "context_id": self._context_id,
        }
        if agent:
            frame["metadata"] = {"agent_name": agent}
        await self._ws.send(json.dumps(frame))

    async def receive(self) -> AsyncIterator[dict[str, Any]]:
        async for raw in self._ws:
            try:
                yield json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                continue
