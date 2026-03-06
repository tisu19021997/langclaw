"""
SessionManager — maps (channel, user_id, context_id) → LangGraph thread_id.

``context_id`` is a session discriminator, **not** a delivery address.
Different values create separate LangGraph threads for the same user
(e.g. ``"cron:task:<uuid>"`` isolates a scheduled task from the main
conversation).

Conversation state lives entirely inside the LangGraph checkpointer.
This manager only maintains the ID mapping so the same thread is resumed
across messages from the same user in the same context.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

from loguru import logger


class SessionManager:
    """
    Thread-safe mapping of channel conversation keys to LangGraph thread IDs.

    Key format: ``"<channel>:<user_id>:<context_id>"``

    The mapping is held in-process by default. For multi-process / multi-instance
    deployments extend this class to back the store with Redis or a shared DB —
    the interface stays identical.
    """

    def __init__(self) -> None:
        self._store: dict[str, str] = {}
        self._mode_store: dict[str, str] = {}
        self._lock = asyncio.Lock()

    async def get_or_create_thread(
        self,
        channel: str,
        user_id: str,
        context_id: str = "default",
    ) -> str:
        """
        Return the existing thread_id for this (channel, user, context) triple,
        or create and store a new UUID thread_id.
        """
        key = self._make_key(channel, user_id, context_id)
        async with self._lock:
            if key not in self._store:
                self._store[key] = str(uuid.uuid4())
                logger.info(f"Created new thread {self._store[key]} for {key}")
            return self._store[key]

    async def delete_thread(
        self,
        channel: str,
        user_id: str,
        context_id: str = "default",
    ) -> bool:
        """
        Remove the thread mapping (e.g. on /reset). Returns True if it existed.
        Note: this does NOT delete the checkpoint from LangGraph storage.
        """
        key = self._make_key(channel, user_id, context_id)
        async with self._lock:
            return self._store.pop(key, None) is not None

    def make_runnable_config(
        self,
        thread_id: str,
        channel_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        Build a LangGraph ``RunnableConfig`` dict for the given thread.

        The optional ``channel_context`` dict is forwarded into
        ``configurable["channel_context"]`` where ``ChannelContextMiddleware``
        picks it up.
        """
        configurable: dict[str, Any] = {"thread_id": thread_id}
        if channel_context:
            configurable["channel_context"] = channel_context
        return {"configurable": configurable}

    async def get_config(
        self,
        channel: str,
        user_id: str,
        context_id: str = "default",
        channel_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        Convenience: get or create thread then return a ready RunnableConfig.
        """
        thread_id = await self.get_or_create_thread(channel, user_id, context_id)
        logger.info(f"Current thread_id: {thread_id} for {channel}:{user_id}:{context_id}")
        return self.make_runnable_config(thread_id, channel_context)

    async def get_mode(self, channel: str, user_id: str) -> str:
        """Return the active agent name for this (channel, user_id) pair.

        Returns:
            The stored agent name, or ``"default"`` if none has been set.
        """
        key = f"{channel}:{user_id}"
        async with self._lock:
            return self._mode_store.get(key, "default")

    async def set_mode(self, channel: str, user_id: str, agent_name: str) -> None:
        """Persist the active agent name for this (channel, user_id) pair.

        Passing ``"default"`` removes the entry, keeping the store clean.

        Args:
            channel:    Channel name (e.g. ``"telegram"``).
            user_id:    Platform-specific user identifier.
            agent_name: Agent name to activate. Pass ``"default"`` to reset.
        """
        key = f"{channel}:{user_id}"
        async with self._lock:
            if agent_name == "default":
                self._mode_store.pop(key, None)
            else:
                self._mode_store[key] = agent_name

    def all_threads(self) -> dict[str, str]:
        """Return a snapshot of all key→thread_id mappings (for diagnostics)."""
        return dict(self._store)

    @staticmethod
    def _make_key(channel: str, user_id: str, context_id: str) -> str:
        return f"{channel}:{user_id}:{context_id}"
