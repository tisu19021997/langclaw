"""
Comprehensive tests for Slack channel functionality.

Tests cover message handling, threading, attachments, commands,
and outbound message delivery.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest

from langclaw.bus.base import Attachment, InboundMessage, OutboundMessage
from langclaw.config.schema import SlackChannelConfig
from langclaw.gateway.commands import CommandContext, CommandRouter
from langclaw.gateway.slack import SlackChannel


@pytest.fixture
def slack_config():
    """Create a basic Slack channel config for testing."""
    return SlackChannelConfig(
        enabled=True,
        bot_token="xoxb-test-token",
        app_token="xapp-test-token",
        allow_from=[],  # Allow all users
        reaction_feedback_enabled=True,
    )


@pytest.fixture
def slack_channel(slack_config):
    """Create a SlackChannel instance for testing."""
    channel = SlackChannel(slack_config)
    channel._bot_user_id = "B123456"  # Set bot user ID
    return channel


@pytest.fixture
def mock_bus():
    """Create a mock message bus."""
    bus = AsyncMock()
    bus.publish = AsyncMock()
    return bus


@pytest.fixture
def mock_slack_app():
    """Create a mock Slack app with client."""
    app = MagicMock()
    app.client = AsyncMock()
    app.client.chat_postMessage = AsyncMock()
    app.client.reactions_add = AsyncMock()
    app.client.reactions_remove = AsyncMock()
    app.client.users_info = AsyncMock(
        return_value={"user": {"name": "testuser"}}
    )
    return app


class TestOnMessage:
    """Test _on_message method for various message scenarios."""

    async def test_on_message_dm_basic(self, slack_channel, mock_bus, mock_slack_app):
        """Test handling a basic DM message."""
        slack_channel._bus = mock_bus
        slack_channel._app = mock_slack_app

        event = {
            "user": "U123456",
            "channel": "D123456",
            "text": "Hello bot",
            "ts": "1234567890.123456",
            "channel_type": "im",
        }

        await slack_channel._on_message(event)

        # Verify message was published to bus
        mock_bus.publish.assert_called_once()
        msg = mock_bus.publish.call_args[0][0]
        assert isinstance(msg, InboundMessage)
        assert msg.user_id == "U123456"
        assert msg.chat_id == "D123456"
        assert msg.content == "Hello bot"
        assert msg.metadata["thread_ts"] is None  # DM has no thread

    async def test_on_message_channel_mention(self, slack_channel, mock_bus, mock_slack_app):
        """Test handling a channel message with bot mention."""
        slack_channel._bus = mock_bus
        slack_channel._app = mock_slack_app

        event = {
            "user": "U123456",
            "channel": "C123456",
            "text": "<@B123456> help me",
            "ts": "1234567890.123456",
            "channel_type": "channel",
        }

        await slack_channel._on_message(event)

        # Verify message was published with mention stripped
        mock_bus.publish.assert_called_once()
        msg = mock_bus.publish.call_args[0][0]
        assert msg.content == "help me"
        assert msg.metadata["thread_ts"] == "1234567890.123456"  # Channel uses thread

    async def test_on_message_ignores_bot_messages(self, slack_channel, mock_bus):
        """Test that bot messages are ignored."""
        slack_channel._bus = mock_bus

        event = {
            "user": "U123456",
            "channel": "C123456",
            "text": "Bot message",
            "bot_id": "B999",
            "ts": "1234567890.123456",
        }

        await slack_channel._on_message(event)

        # Should not publish to bus
        mock_bus.publish.assert_not_called()

    async def test_on_message_ignores_message_changed(self, slack_channel, mock_bus):
        """Test that message_changed subtype is ignored."""
        slack_channel._bus = mock_bus

        event = {
            "user": "U123456",
            "channel": "C123456",
            "text": "Edited message",
            "subtype": "message_changed",
            "ts": "1234567890.123456",
        }

        await slack_channel._on_message(event)

        # Should not publish to bus
        mock_bus.publish.assert_not_called()

    async def test_on_message_allows_file_share(self, slack_channel, mock_bus, mock_slack_app):
        """Test that file_share subtype is processed."""
        slack_channel._bus = mock_bus
        slack_channel._app = mock_slack_app

        event = {
            "user": "U123456",
            "channel": "D123456",
            "text": "",
            "subtype": "file_share",
            "ts": "1234567890.123456",
            "channel_type": "im",
            "files": [],
        }

        await slack_channel._on_message(event)

        # Should publish to bus
        mock_bus.publish.assert_called_once()

    async def test_on_message_channel_without_mention_ignored(self, slack_channel, mock_bus):
        """Test that channel messages without bot mention are ignored."""
        slack_channel._bus = mock_bus

        event = {
            "user": "U123456",
            "channel": "C123456",
            "text": "Random chat message",
            "ts": "1234567890.123456",
            "channel_type": "channel",
        }

        await slack_channel._on_message(event)

        # Should not publish (no bot mention)
        mock_bus.publish.assert_not_called()

    async def test_on_message_respects_allow_from(self, mock_bus, mock_slack_app):
        """Test that allow_from whitelist is enforced."""
        config = SlackChannelConfig(
            enabled=True,
            bot_token="xoxb-test",
            app_token="xapp-test",
            allow_from=["U111111"],  # Only allow this user
        )
        channel = SlackChannel(config)
        channel._bus = mock_bus
        channel._app = mock_slack_app
        channel._bot_user_id = "B123456"

        # Message from unauthorized user
        event = {
            "user": "U999999",
            "channel": "D123456",
            "text": "Hello",
            "ts": "1234567890.123456",
            "channel_type": "im",
        }

        await channel._on_message(event)

        # Should not publish to bus
        mock_bus.publish.assert_not_called()


class TestThreadReplyLogic:
    """Test thread reply logic for DMs vs channels."""

    async def test_dm_no_thread(self, slack_channel, mock_bus, mock_slack_app):
        """Test that DMs don't use threads."""
        slack_channel._bus = mock_bus
        slack_channel._app = mock_slack_app

        event = {
            "user": "U123456",
            "channel": "D123456",
            "text": "Hello",
            "ts": "1234567890.123456",
            "channel_type": "im",
        }

        await slack_channel._on_message(event)

        msg = mock_bus.publish.call_args[0][0]
        assert msg.metadata["thread_ts"] is None

    async def test_channel_uses_thread(self, slack_channel, mock_bus, mock_slack_app):
        """Test that channel messages use threads."""
        slack_channel._bus = mock_bus
        slack_channel._app = mock_slack_app

        event = {
            "user": "U123456",
            "channel": "C123456",
            "text": "<@B123456> help",
            "ts": "1234567890.123456",
            "channel_type": "channel",
        }

        await slack_channel._on_message(event)

        msg = mock_bus.publish.call_args[0][0]
        # Should use message ts as thread_ts for new thread
        assert msg.metadata["thread_ts"] == "1234567890.123456"

    async def test_thread_reply_preserves_thread(self, slack_channel, mock_bus, mock_slack_app):
        """Test that replies in existing threads preserve thread_ts."""
        slack_channel._bus = mock_bus
        slack_channel._app = mock_slack_app

        event = {
            "user": "U123456",
            "channel": "C123456",
            "text": "<@B123456> follow-up",
            "ts": "1234567891.000000",
            "thread_ts": "1234567890.123456",  # Existing thread
            "channel_type": "channel",
        }

        await slack_channel._on_message(event)

        msg = mock_bus.publish.call_args[0][0]
        # Should preserve original thread_ts
        assert msg.metadata["thread_ts"] == "1234567890.123456"


class TestContextIdScoping:
    """Test that context_id is scoped correctly for threads."""

    async def test_dm_context_is_channel(self, slack_channel, mock_bus, mock_slack_app):
        """Test that DM context_id is just the channel ID."""
        slack_channel._bus = mock_bus
        slack_channel._app = mock_slack_app

        event = {
            "user": "U123456",
            "channel": "D123456",
            "text": "Hello",
            "ts": "1234567890.123456",
            "channel_type": "im",
        }

        await slack_channel._on_message(event)

        msg = mock_bus.publish.call_args[0][0]
        assert msg.context_id == "D123456"

    async def test_channel_thread_context_includes_thread_ts(
        self, slack_channel, mock_bus, mock_slack_app
    ):
        """Test that channel thread context includes thread_ts."""
        slack_channel._bus = mock_bus
        slack_channel._app = mock_slack_app

        event = {
            "user": "U123456",
            "channel": "C123456",
            "text": "<@B123456> help",
            "ts": "1234567890.123456",
            "channel_type": "channel",
        }

        await slack_channel._on_message(event)

        msg = mock_bus.publish.call_args[0][0]
        assert msg.context_id == "C123456:1234567890.123456"


class TestAttachmentDownload:
    """Test attachment download functionality."""

    @pytest.mark.skip(reason="Complex async file I/O mocking - integration test recommended")
    async def test_attachment_download_success(
        self, slack_channel, mock_bus, mock_slack_app
    ):
        """Test successful attachment download.

        NOTE: This test requires complex mocking of async file I/O operations.
        Consider using integration tests for attachment download functionality.
        """
        pass

    async def test_attachment_too_large_rejected(self, slack_channel, mock_bus, mock_slack_app):
        """Test that attachments over 20MB are rejected."""
        slack_channel._bus = mock_bus
        slack_channel._app = mock_slack_app

        event = {
            "user": "U123456",
            "channel": "D123456",
            "text": "",
            "ts": "1234567890.123456",
            "channel_type": "im",
            "files": [
                {
                    "id": "F123456",
                    "name": "huge.zip",
                    "size": 25 * 1024 * 1024,  # 25 MB
                    "url_private": "https://files.slack.com/huge.zip",
                }
            ],
        }

        await slack_channel._on_message(event)

        # Verify attachment was rejected
        msg = mock_bus.publish.call_args[0][0]
        assert len(msg.attachments) == 0
        assert "[attachment: huge.zip - too large]" in msg.content


class TestCommandDispatch:
    """Test command routing and dispatch."""

    async def test_command_dispatch(self, slack_channel, mock_slack_app):
        """Test that commands are routed to command router."""
        from langclaw.session.manager import SessionManager

        # Create router with mock session manager
        session_mgr = Mock(spec=SessionManager)
        router = CommandRouter(session_mgr)
        response_sent = []

        async def mock_handler(ctx: CommandContext) -> str:
            return "Command executed"

        router.register("test", mock_handler, "Test command")
        slack_channel._command_router = router
        slack_channel._app = mock_slack_app
        slack_channel._bus = AsyncMock()

        # Mock _send_text to capture response
        async def capture_response(channel_id, text, thread_ts=None):
            response_sent.append(text)

        slack_channel._send_text = capture_response

        event = {
            "user": "U123456",
            "channel": "D123456",
            "text": "/test",
            "ts": "1234567890.123456",
            "channel_type": "im",
        }

        await slack_channel._on_message(event)

        # Command response should be sent
        assert len(response_sent) > 0
        assert "Command executed" in response_sent[0]
        # Bus should not receive the command
        slack_channel._bus.publish.assert_not_called()


class TestSendAiMessage:
    """Test send_ai_message functionality."""

    async def test_send_ai_message_basic(self, slack_channel, mock_slack_app):
        """Test sending a basic AI message."""
        slack_channel._app = mock_slack_app

        msg = OutboundMessage(
            channel="slack",
            user_id="U123456",
            chat_id="D123456",
            context_id="D123456",
            content="Hello, how can I help?",
            metadata={"thread_ts": None},
        )

        await slack_channel.send_ai_message(msg)

        # Verify message was sent
        mock_slack_app.client.chat_postMessage.assert_called_once()
        call_kwargs = mock_slack_app.client.chat_postMessage.call_args[1]
        assert call_kwargs["channel"] == "D123456"
        assert "Hello, how can I help?" in call_kwargs["text"]

    async def test_send_ai_message_with_markdown(self, slack_channel, mock_slack_app):
        """Test that markdown messages are sent."""
        slack_channel._app = mock_slack_app

        msg = OutboundMessage(
            channel="slack",
            user_id="U123456",
            chat_id="D123456",
            context_id="D123456",
            content="**Bold** and `code`",
            metadata={"thread_ts": None},
        )

        await slack_channel.send_ai_message(msg)

        # Verify message was sent (content may or may not be converted depending on slackify_markdown availability)
        mock_slack_app.client.chat_postMessage.assert_called_once()
        call_kwargs = mock_slack_app.client.chat_postMessage.call_args[1]
        assert call_kwargs["channel"] == "D123456"
        # Message content should be present
        assert len(call_kwargs["text"]) > 0

    async def test_send_ai_message_chunking(self, slack_channel, mock_slack_app):
        """Test that long messages are split into chunks."""
        slack_channel._app = mock_slack_app

        # Create a message longer than MAX_MESSAGE_LEN (3000)
        long_content = "x" * 4000

        msg = OutboundMessage(
            channel="slack",
            user_id="U123456",
            chat_id="D123456",
            context_id="D123456",
            content=long_content,
            metadata={"thread_ts": None},
        )

        await slack_channel.send_ai_message(msg)

        # Should be called multiple times for chunks
        assert mock_slack_app.client.chat_postMessage.call_count > 1


class TestSendToolResult:
    """Test send_tool_result functionality."""

    async def test_send_tool_result_basic(self, slack_channel, mock_slack_app):
        """Test sending a basic tool result."""
        slack_channel._app = mock_slack_app

        # First send tool progress
        progress_msg = OutboundMessage(
            channel="slack",
            user_id="U123456",
            chat_id="D123456",
            context_id="D123456",
            content="",
            metadata={
                "tool_call_id": "call_123",
                "tool": "search",
                "args": {"query": "test"},
                "thread_ts": None,
            },
        )
        await slack_channel.send_tool_progress(progress_msg)

        # Then send tool result
        result_msg = OutboundMessage(
            channel="slack",
            user_id="U123456",
            chat_id="D123456",
            context_id="D123456",
            content='{"results": ["item1", "item2"]}',
            metadata={
                "tool_call_id": "call_123",
                "thread_ts": None,
            },
        )
        await slack_channel.send_tool_result(result_msg)

        # Verify formatted message was sent
        mock_slack_app.client.chat_postMessage.assert_called_once()
        call_kwargs = mock_slack_app.client.chat_postMessage.call_args[1]
        assert "search" in call_kwargs["text"]
        assert '{"results": ["item1", "item2"]}' in call_kwargs["text"]
        assert "```" in call_kwargs["text"]  # Code block formatting

    async def test_send_tool_result_truncation(self, slack_channel, mock_slack_app):
        """Test that large tool results are truncated."""
        slack_channel._app = mock_slack_app

        # Send tool progress
        progress_msg = OutboundMessage(
            channel="slack",
            user_id="U123456",
            chat_id="D123456",
            context_id="D123456",
            content="",
            metadata={
                "tool_call_id": "call_123",
                "tool": "search",
                "args": {},
                "thread_ts": None,
            },
        )
        await slack_channel.send_tool_progress(progress_msg)

        # Send huge result
        huge_result = "x" * 5000
        result_msg = OutboundMessage(
            channel="slack",
            user_id="U123456",
            chat_id="D123456",
            context_id="D123456",
            content=huge_result,
            metadata={
                "tool_call_id": "call_123",
                "thread_ts": None,
            },
        )
        await slack_channel.send_tool_result(result_msg)

        # Verify message was truncated
        call_kwargs = mock_slack_app.client.chat_postMessage.call_args[1]
        assert len(call_kwargs["text"]) <= 3000  # MAX_MESSAGE_LEN
        assert "[truncated]" in call_kwargs["text"]


class TestUserCache:
    """Test user info caching."""

    async def test_user_cache_avoids_duplicate_calls(self, slack_channel, mock_bus, mock_slack_app):
        """Test that user info is cached to avoid rate limiting."""
        slack_channel._bus = mock_bus
        slack_channel._app = mock_slack_app

        event = {
            "user": "U123456",
            "channel": "D123456",
            "text": "Hello",
            "ts": "1234567890.123456",
            "channel_type": "im",
        }

        # First message - should call users_info
        await slack_channel._on_message(event)
        assert mock_slack_app.client.users_info.call_count == 1

        # Second message from same user - should use cache
        event["ts"] = "1234567891.000000"
        event["text"] = "Another message"
        await slack_channel._on_message(event)
        # Still only 1 call (cached)
        assert mock_slack_app.client.users_info.call_count == 1


class TestReactionFeedback:
    """Test reaction emoji feedback."""

    async def test_reaction_added_on_message(self, slack_channel, mock_bus, mock_slack_app):
        """Test that processing reaction is added when message arrives."""
        slack_channel._bus = mock_bus
        slack_channel._app = mock_slack_app
        slack_channel._config.reaction_feedback_enabled = True

        event = {
            "user": "U123456",
            "channel": "D123456",
            "text": "Hello",
            "ts": "1234567890.123456",
            "channel_type": "im",
        }

        await slack_channel._on_message(event)

        # Verify processing reaction was added
        mock_slack_app.client.reactions_add.assert_called_once()
        call_kwargs = mock_slack_app.client.reactions_add.call_args[1]
        assert call_kwargs["name"] == "eyes"  # Default processing emoji

    async def test_reaction_swapped_on_completion(self, slack_channel, mock_slack_app):
        """Test that reaction is swapped when AI response is sent."""
        slack_channel._app = mock_slack_app
        slack_channel._config.reaction_feedback_enabled = True
        slack_channel._reaction_tracking["D123456"] = ("D123456", "1234567890.123456")

        msg = OutboundMessage(
            channel="slack",
            user_id="U123456",
            chat_id="D123456",
            context_id="D123456",
            content="Done!",
            metadata={"thread_ts": None},
        )

        await slack_channel.send_ai_message(msg)

        # Verify reactions were updated
        mock_slack_app.client.reactions_remove.assert_called_once()
        mock_slack_app.client.reactions_add.assert_called()
        # Should add white_check_mark (complete emoji)
        final_add_call = mock_slack_app.client.reactions_add.call_args_list[-1]
        assert final_add_call[1]["name"] == "white_check_mark"
