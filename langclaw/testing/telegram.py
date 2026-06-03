"""
Telegram probe transport — the narrow smoke layer.

A Telegram *user* account (via MTProto/Telethon) sends to the bot and reads its
replies. Its only added value over the WebSocket workhorse is Telegram-specific
rendering — Markdown, message chunking, tool-progress formatting — so it is meant
for occasional use, not per-feature testing.

Honest constraints:
  - A bot cannot DM another bot, so simulating a user needs a Telegram *user*
    account (Telethon ``StringSession``), not the bot token. The one-time login
    is a human step; the resulting session string is reused headlessly.
  - Telegram has no stream-end signal, so a turn completes by *idle-timeout*:
    once the bot has been silent for ``idle_timeout`` seconds, the turn is done.
    The transport synthesises an ``ai_stream_end`` frame at that point so the
    probe core's completion logic stays identical to the WebSocket path.

Requires the ``telegram-e2e`` extra (``uv add 'langclaw[telegram-e2e]'``).
Secrets come from gitignored env: ``TELEGRAM_API_ID``, ``TELEGRAM_API_HASH``,
``TELETHON_SESSION``.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any


async def stream_until_idle(
    messages: AsyncIterator[str],
    idle_timeout: float,
) -> AsyncIterator[dict[str, Any]]:
    """Convert a stream of reply texts into WebSocket-style frames.

    Yields one ``ai_chunk`` frame per incoming message, then a terminal
    ``ai_stream_end`` frame once no new message arrives within ``idle_timeout``
    seconds (or the source is exhausted). This is the Telegram completion logic,
    factored out so it can be unit-tested against a fake message source without
    any live Telethon connection.

    Args:
        messages: Async iterator of reply message texts from the bot.
        idle_timeout: Seconds of silence that mark the turn complete.

    Yields:
        ``{"type": "ai_chunk", "content": <text>}`` per message, then a single
        ``{"type": "ai_stream_end"}``.
    """
    iterator = messages.__aiter__()
    while True:
        try:
            text = await asyncio.wait_for(iterator.__anext__(), idle_timeout)
        except (TimeoutError, StopAsyncIteration):
            yield {"type": "ai_stream_end"}
            return
        yield {"type": "ai_chunk", "content": text}


class TelegramProbeTransport:
    """Drive a turn against the live Telegram wire via a Telethon user client.

    Args:
        bot_username: The target bot's ``@username`` (or numeric id).
        idle_timeout: Seconds of bot silence that mark a turn complete.
        api_id / api_hash / session: Telethon credentials. Default to the
            ``TELEGRAM_API_ID`` / ``TELEGRAM_API_HASH`` / ``TELETHON_SESSION``
            env vars when not given.
    """

    def __init__(
        self,
        bot_username: str,
        *,
        idle_timeout: float = 10.0,
        api_id: int | None = None,
        api_hash: str | None = None,
        session: str | None = None,
    ) -> None:
        import os

        self._bot = bot_username
        self._idle_timeout = idle_timeout
        self._api_id = api_id or int(os.environ.get("TELEGRAM_API_ID", "0"))
        self._api_hash = api_hash or os.environ.get("TELEGRAM_API_HASH", "")
        self._session = session or os.environ.get("TELETHON_SESSION", "")
        self._client: Any = None
        self._queue: asyncio.Queue[str] | None = None

    async def open(self) -> None:
        try:
            from telethon import TelegramClient, events
            from telethon.sessions import StringSession
        except ImportError as exc:  # pragma: no cover - import guard
            raise ImportError(
                "TelegramProbeTransport requires 'telethon'. "
                "Install with: uv add 'langclaw[telegram-e2e]'"
            ) from exc

        if not (self._api_id and self._api_hash and self._session):
            raise RuntimeError(
                "Telegram probe needs TELEGRAM_API_ID, TELEGRAM_API_HASH and "
                "TELETHON_SESSION (run the one-time login to mint the session)."
            )

        self._queue = asyncio.Queue()
        self._client = TelegramClient(StringSession(self._session), self._api_id, self._api_hash)

        @self._client.on(events.NewMessage(from_users=self._bot, incoming=True))
        async def _on_message(event: Any) -> None:  # pragma: no cover - live path
            assert self._queue is not None
            await self._queue.put(event.message.message or "")

        await self._client.connect()

    async def close(self) -> None:
        if self._client is not None:
            await self._client.disconnect()
            self._client = None

    async def send_turn(self, content: str, *, agent: str | None = None) -> None:
        # Telegram has no agent_name channel; named-agent targeting is a WS feature.
        await self._client.send_message(self._bot, content)

    async def receive(self) -> AsyncIterator[dict[str, Any]]:
        assert self._queue is not None
        queue = self._queue

        async def _drain() -> AsyncIterator[str]:
            while True:
                yield await queue.get()

        async for frame in stream_until_idle(_drain(), self._idle_timeout):
            yield frame
