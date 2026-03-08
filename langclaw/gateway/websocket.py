"""
WebSocketChannel — Gateway channel exposing ``ws://<host>:<port>``.

Requires: langclaw[websocket]  →  uv add "langclaw[websocket]"

Any number of clients (CLI, WebChat UI, macOS app, mobile) can connect
simultaneously.  The protocol is line-delimited JSON:

  Inbound  (client → gateway):
    {"type": "message", "content": "...", "user_id": "...", "context_id": "..."}

  Outbound (gateway → client):
    {"type": "ai"|"tool_progress"|"tool_result", "content": "...", ...}

Each WebSocket connection is identified by a ``(user_id, context_id)`` pair
sent in the first message.  Subsequent outbound messages are routed only to
connections whose identity matches.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from langclaw.bus.base import (
    Attachment,
    AttachmentType,
    BaseMessageBus,
    InboundMessage,
    OutboundMessage,
)
from langclaw.config.schema import WebSocketChannelConfig
from langclaw.gateway.base import BaseChannel
from langclaw.gateway.commands import CommandContext
from langclaw.gateway.utils import is_allowed

logger = logging.getLogger(__name__)


class _Connection:
    """Tracks one WebSocket client and its identity."""

    __slots__ = ("ws", "user_id", "context_id")

    def __init__(self, ws: Any, user_id: str = "", context_id: str = "") -> None:
        self.ws = ws
        self.user_id = user_id
        self.context_id = context_id


class WebSocketChannel(BaseChannel):
    """
    WebSocket server channel backed by the ``websockets`` library.

    Listens on ``ws://<host>:<port>`` and accepts JSON-framed messages.
    Broadcasts outbound messages to all connections that match the
    ``(user_id, context_id)`` pair (or to all when ``chat_id`` is ``"*"``).

    Args:
        config: WebSocket-specific section of LangclawConfig.channels.websocket.
    """

    name = "websocket"

    def __init__(self, config: WebSocketChannelConfig) -> None:
        self._config = config
        self._bus: BaseMessageBus | None = None
        self._server: Any = None
        self._connections: set[_Connection] = set()

    def is_enabled(self) -> bool:
        return self._config.enabled

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self, bus: BaseMessageBus) -> None:
        try:
            import websockets  # noqa: F401
        except ImportError as exc:
            raise ImportError(
                "WebSocketChannel requires 'websockets'. Install with: uv add 'langclaw[websocket]'"
            ) from exc

        self._bus = bus

        import websockets.asyncio.server as ws_server

        self._server = await ws_server.serve(
            self._handler,
            host=self._config.host,
            port=self._config.port,
        )
        logger.info(
            "WebSocketChannel listening on ws://%s:%s",
            self._config.host,
            self._config.port,
        )
        await asyncio.Future()  # block until cancelled

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        self._connections.clear()
        logger.info("WebSocketChannel stopped.")

    # ------------------------------------------------------------------
    # Connection handler
    # ------------------------------------------------------------------

    async def _handler(self, ws: Any) -> None:
        """Handle a single WebSocket connection for its entire lifetime."""
        conn = _Connection(ws)
        self._connections.add(conn)
        remote = ws.remote_address
        logger.info("WebSocket client connected: %s", remote)

        try:
            async for raw in ws:
                try:
                    data = json.loads(raw)
                except (json.JSONDecodeError, TypeError):
                    await self._send_json(
                        ws,
                        {
                            "type": "error",
                            "content": "Invalid JSON.",
                        },
                    )
                    continue

                conn.user_id = data.get("user_id", conn.user_id or "ws-anon")
                conn.context_id = data.get("context_id", conn.context_id or "default")

                if not self._is_allowed(conn.user_id):
                    await self._send_json(
                        ws,
                        {
                            "type": "error",
                            "content": "Not authorised.",
                        },
                    )
                    continue

                msg_type = data.get("type", "message")
                content = data.get("content", "")

                if msg_type == "ping":
                    await self._send_json(ws, {"type": "pong"})
                    continue

                if not content and not data.get("attachments"):
                    continue

                # Command handling
                stripped = content.strip()
                if stripped.startswith("/") and self._command_router is not None:
                    parts = stripped.split()
                    cmd = parts[0].lstrip("/").lower()
                    args = parts[1:] if len(parts) > 1 else []
                    ctx = CommandContext(
                        channel=self.name,
                        user_id=conn.user_id,
                        context_id=conn.context_id,
                        chat_id=f"{conn.user_id}:{conn.context_id}",
                        args=args,
                        display_name=conn.user_id,
                    )
                    response = await self._command_router.dispatch(cmd, ctx)
                    await self._send_json(
                        ws,
                        {
                            "type": "command",
                            "content": response,
                        },
                    )
                    continue

                if self._bus is None:
                    continue

                raw_attachments = data.get("attachments") or []
                attachments = [
                    Attachment(
                        type=AttachmentType(a.get("type", "file")),
                        mime_type=a.get("mime_type", ""),
                        filename=a.get("filename", ""),
                        url=a.get("url", ""),
                        data=a.get("data", ""),
                        size=a.get("size", 0),
                    )
                    for a in raw_attachments
                    if isinstance(a, dict)
                ]

                await self._bus.publish(
                    InboundMessage(
                        channel=self.name,
                        user_id=conn.user_id,
                        context_id=conn.context_id,
                        chat_id=f"{conn.user_id}:{conn.context_id}",
                        content=content,
                        origin="channel",
                        attachments=attachments,
                        metadata={
                            "platform": "websocket",
                        },
                    )
                )
        except Exception as exc:
            logger.debug("WebSocket connection closed (%s): %s", remote, exc)
        finally:
            self._connections.discard(conn)
            logger.info("WebSocket client disconnected: %s", remote)

    # ------------------------------------------------------------------
    # Outbound hooks
    # ------------------------------------------------------------------

    async def send_tool_progress(self, msg: OutboundMessage) -> None:
        await self._broadcast(
            msg.user_id,
            msg.context_id,
            {
                "type": "tool_progress",
                "content": msg.content,
                "metadata": msg.metadata,
            },
        )

    async def send_tool_result(self, msg: OutboundMessage) -> None:
        await self._broadcast(
            msg.user_id,
            msg.context_id,
            {
                "type": "tool_result",
                "content": msg.content,
                "metadata": msg.metadata,
            },
        )

    async def send_ai_message(self, msg: OutboundMessage) -> None:
        if not msg.content:
            return
        await self._broadcast(
            msg.user_id,
            msg.context_id,
            {
                "type": "ai",
                "content": msg.content,
            },
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _broadcast(
        self,
        user_id: str,
        context_id: str,
        payload: dict[str, Any],
    ) -> None:
        """Send *payload* to every connection matching the identity pair."""
        chat_id = f"{user_id}:{context_id}"
        dead: list[_Connection] = []
        for conn in self._connections:
            conn_id = f"{conn.user_id}:{conn.context_id}"
            if conn_id == chat_id or chat_id == "*":
                try:
                    await self._send_json(conn.ws, payload)
                except Exception:
                    dead.append(conn)
        for conn in dead:
            self._connections.discard(conn)

    @staticmethod
    async def _send_json(ws: Any, data: dict[str, Any]) -> None:
        await ws.send(json.dumps(data))

    def _is_allowed(self, user_id: str) -> bool:
        return is_allowed(self._config.allow_from, user_id)
