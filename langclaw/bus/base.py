"""
BaseMessageBus — abstract pub/sub interface for langclaw.

All message sources (channels, cron, heartbeat) publish InboundMessages.
GatewayManager subscribes and drives the agent loop.

Swapping dev → prod bus = one config field + optional install.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from enum import StrEnum

# ---------------------------------------------------------------------------
# Attachment types
# ---------------------------------------------------------------------------


class AttachmentType(StrEnum):
    """Supported attachment media categories."""

    IMAGE = "image"
    FILE = "file"
    AUDIO = "audio"
    VIDEO = "video"


@dataclass
class Attachment:
    """A standardised attachment descriptor.

    Channels convert platform-specific media into this format.
    ``GatewayManager`` converts these into LangChain multimodal content blocks.

    Either ``url`` or ``data`` (base64-encoded) must be populated.
    When ``data`` is used, ``mime_type`` is required for the data URI.

    Args:
        type: Media category (image, file, audio, video).
        mime_type: MIME type (e.g. ``"image/jpeg"``, ``"application/pdf"``).
        filename: Original filename, if available.
        url: URL to the attachment (public URL or local ``file://`` path).
        data: Base64-encoded content (alternative to *url*).
        size: File size in bytes, if known.
    """

    type: AttachmentType
    mime_type: str = ""
    filename: str = ""
    url: str = ""
    data: str = ""
    size: int = 0


# ---------------------------------------------------------------------------
# Message dataclasses
# ---------------------------------------------------------------------------


@dataclass
class InboundMessage:
    """A message arriving from any source heading into the agent.

    Routing fields:
      - ``origin``: Who produced the message. Used to construct the appropriate
        LangChain message type when feeding the agent.
      - ``to``: Where to route the message (``"agent"`` or ``"channel"``).
        Messages with ``to="channel"`` bypass the agent and are delivered
        directly to the originating channel.
    """

    channel: str
    """Originating channel name: ``"telegram"``, ``"discord"``, ``"cron"``, etc."""

    user_id: str
    """Platform-specific user identifier."""

    context_id: str
    """Session key used for LangGraph thread mapping. Never used for delivery."""

    content: str
    """Text content of the message."""

    chat_id: str = ""
    """
    Channel-specific delivery address (e.g. Telegram chat_id, Discord
    channel_id). Should be set by the originating source; if empty,
    ``BaseChannel.send()`` falls back to ``user_id``.
    """

    origin: str = "user"
    """
    Who produced the message. Common values:
      - ``"user"``: End-user input from a channel (default).
      - ``"channel"``: Alias for user input via a channel.
      - ``"cron"``: Scheduled job.
      - ``"heartbeat"``: Event-driven condition trigger.
      - ``"subagent"``: Output from a subagent.

    This field supersedes ``metadata["source"]`` for new code.
    """

    to: str = "agent"
    """
    Where to route the message:
      - ``"agent"``: Feed to the main agent pipeline (default).
      - ``"channel"``: Deliver directly to the channel, bypassing the agent.
    """

    attachments: list[Attachment] = field(default_factory=list)
    """Optional list of standardised attachment descriptors."""

    metadata: dict = field(default_factory=dict)
    """
    Freeform metadata passed through to the agent config.
    Built-in keys:
      - ``"reply_to"``: message ID for threading (platform-specific)
    """

    def __post_init__(self) -> None:
        """Reconstruct nested ``Attachment`` objects from raw dicts.

        Required for bus deserialization (``InboundMessage(**asdict(msg))``).
        """
        if self.attachments and isinstance(self.attachments[0], dict):
            self.attachments = [
                Attachment(**{**a, "type": AttachmentType(a["type"])}) if isinstance(a, dict) else a
                for a in self.attachments
            ]


@dataclass
class OutboundMessage:
    """A message from the agent heading back to a channel."""

    channel: str
    user_id: str
    context_id: str
    """Session key mirrored from the inbound message (not used for delivery)."""
    content: str
    chat_id: str = ""
    """
    Channel-specific delivery address mirrored from the originating
    ``InboundMessage``. Guaranteed non-empty by ``BaseChannel.send()``
    which falls back to ``user_id``.
    """
    type: str = "ai"
    """Message type: ``"ai"`` | ``"tool_progress"`` | ``"tool_result"``."""
    streaming: bool = False
    """True when this message is a streaming chunk rather than a complete response."""
    is_final: bool = False
    """True on the last streaming chunk — signals channels to flush buffered content."""
    metadata: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Abstract bus
# ---------------------------------------------------------------------------


class BaseMessageBus(ABC):
    """
    Abstract message bus.

    Lifecycle::

        async with bus:
            await bus.publish(msg)
            async for inbound in bus.subscribe():
                ...
    """

    async def __aenter__(self) -> BaseMessageBus:
        await self.start()
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.stop()

    @abstractmethod
    async def start(self) -> None:
        """Connect / initialise the underlying transport."""
        ...

    @abstractmethod
    async def stop(self) -> None:
        """Gracefully shut down and release resources."""
        ...

    @abstractmethod
    async def publish(self, msg: InboundMessage) -> None:
        """Publish an inbound message to the bus."""
        ...

    @abstractmethod
    def subscribe(self) -> AsyncIterator[InboundMessage]:
        """
        Return an async iterator that yields inbound messages indefinitely.

        The iterator must be safe to call from a single consumer task.
        """
        ...
