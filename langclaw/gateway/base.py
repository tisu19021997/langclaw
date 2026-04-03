"""
BaseChannel — abstract interface for all message channels.

Adding a new channel = one file implementing BaseChannel + one field in ChannelsConfig.

Message delivery uses the Template Method pattern:
  ``send()`` is a concrete dispatcher — subclasses must NOT override it.
  Override only the per-type hooks that the channel supports:

  - ``send_tool_progress`` — default: no-op (opt-in)
  - ``send_tool_result``   — default: no-op (opt-in)
  - ``send_ai_message``    — abstract (required)
  - ``send_ai_chunk``      — default: buffer + flush on final (opt-in for live streaming)

A minimal channel only needs ``send_ai_message``.
Both ``tool_progress`` and ``tool_result`` carry a ``"tool_call_id"`` key in
``msg.metadata`` so channels can correlate call and result for combined rendering.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import replace
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from langclaw.bus.base import BaseMessageBus, OutboundMessage
    from langclaw.gateway.commands import CommandRouter


class BaseChannel(ABC):
    """
    Abstract base for all langclaw gateway channels.

    Lifecycle managed by :class:`~langclaw.gateway.manager.GatewayManager`.
    Each channel runs as an independent async task inside an ``asyncio.TaskGroup``.
    """

    name: str
    """Unique channel identifier (e.g. ``"telegram"``, ``"discord"``)."""

    _command_router: CommandRouter | None = None
    _chunk_buffer: dict[tuple[str, str], list[str]]

    def set_command_router(self, router: CommandRouter) -> None:
        """Inject the shared command router (called by GatewayManager)."""
        self._command_router = router

    @abstractmethod
    async def start(self, bus: BaseMessageBus) -> None:
        """
        Connect to the channel and start consuming incoming messages.

        Must publish each incoming message to *bus* via
        ``await bus.publish(InboundMessage(...))``.

        This coroutine runs indefinitely until cancelled.
        """
        ...

    async def send(self, msg: OutboundMessage) -> None:
        """
        Dispatch *msg* to the appropriate per-type hook.

        Guarantees ``msg.chat_id`` is non-empty before reaching any hook
        by falling back to ``msg.user_id`` (correct for private-chat platforms).

        Do NOT override this method in subclasses — override the hooks instead.
        """
        if not msg.chat_id:
            msg.chat_id = msg.user_id
        if msg.type == "tool_progress":
            await self.send_tool_progress(msg)
        elif msg.type == "tool_result":
            await self.send_tool_result(msg)
        elif msg.streaming:
            await self.send_ai_chunk(msg)
        else:
            await self.send_ai_message(msg)

    async def send_tool_progress(self, msg: OutboundMessage) -> None:
        """
        Notify the user that a tool is being called.

        Default: silent no-op. Override to show a status indicator or buffer
        the call info for later combination with the result.

        ``msg.metadata`` contains:
          - ``"tool_call_id"`` (str)  — correlates with the matching result
          - ``"tool"``         (str)  — tool name
          - ``"args"``         (dict) — arguments passed to the tool
        """

    async def send_tool_result(self, msg: OutboundMessage) -> None:
        """
        Deliver the raw output returned by a tool.

        Default: silent no-op. Override to display the result — e.g. combine
        it with the buffered call info for a single rendered message.

        ``msg.metadata`` contains:
          - ``"tool_call_id"`` (str) — correlates with the matching call
        ``msg.content`` holds the raw tool output text.
        """

    async def send_ai_chunk(self, msg: OutboundMessage) -> None:
        """
        Deliver one streaming chunk of an AI response.

        Default: accumulate all chunks and call ``send_ai_message`` on the final
        one.  Override this method to stream content live (e.g. WebSocket push,
        Telegram message edit).

        ``msg.streaming`` is always ``True`` here.
        ``msg.is_final`` is ``True`` on the last chunk — flush and deliver.
        """
        if not hasattr(self, "_chunk_buffer"):
            self._chunk_buffer = {}
        key = (msg.chat_id or msg.user_id, msg.context_id)
        if msg.content:
            self._chunk_buffer.setdefault(key, []).append(msg.content)
        if msg.is_final:
            full = "".join(self._chunk_buffer.pop(key, []))
            if full:
                await self.send_ai_message(
                    replace(msg, content=full, streaming=False, is_final=False)
                )

    @abstractmethod
    async def send_ai_message(self, msg: OutboundMessage) -> None:
        """
        Deliver the final AI response to the user.

        Every channel must implement this.
        """
        ...

    @abstractmethod
    async def stop(self) -> None:
        """Gracefully disconnect and release resources."""
        ...

    def is_enabled(self) -> bool:
        """Return True if this channel should be started by the gateway."""
        return True
