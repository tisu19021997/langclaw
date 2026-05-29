"""
Telegram tool-progress rendering.

A tool *call* (carrying a ``tool_call_id``) is buffered and rendered together
with its matching ``tool_result``. Standalone progress (no ``tool_call_id``) —
emitted by long-running operations that report their own status — has no result
to pair with and must be rendered immediately rather than silently dropped.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

from langclaw.bus.base import OutboundMessage
from langclaw.config.schema import TelegramChannelConfig
from langclaw.gateway.telegram import TelegramChannel


def _channel() -> TelegramChannel:
    ch = TelegramChannel(TelegramChannelConfig(enabled=True, token="fake"))
    ch._send_progress = AsyncMock()
    return ch


def _msg(content="working…", **metadata) -> OutboundMessage:
    return OutboundMessage(
        channel="telegram",
        user_id="u",
        context_id="c",
        chat_id="chat",
        content=content,
        type="tool_progress",
        metadata=metadata,
    )


async def test_standalone_progress_is_rendered_immediately():
    ch = _channel()
    await ch.send_tool_progress(_msg(content="▸ step 1"))
    ch._send_progress.assert_awaited_once()
    chat_id, html = ch._send_progress.call_args[0]
    assert chat_id == "chat"
    assert "step 1" in html


async def test_tool_call_progress_is_buffered_not_rendered():
    ch = _channel()
    await ch.send_tool_progress(
        _msg(content="read_file", tool_call_id="tc1", tool="read_file", args={"path": "/x"})
    )
    ch._send_progress.assert_not_awaited()
    assert "tc1" in ch._tool_call_buffer


async def test_standalone_progress_html_escapes_content():
    ch = _channel()
    await ch.send_tool_progress(_msg(content="a < b & c > d"))
    _chat_id, html = ch._send_progress.call_args[0]
    assert "&lt;" in html and "&amp;" in html and "&gt;" in html
