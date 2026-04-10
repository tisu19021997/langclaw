"""
Tests for gateway utilities and channel implementations.

Covers shared helpers (split_message, format_tool_progress, is_allowed)
and channel smoke tests (import, instantiation, is_enabled).
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# gateway.utils — split_message
# ---------------------------------------------------------------------------


class TestSplitMessage:
    def test_empty_string(self):
        from langclaw.gateway.utils import split_message

        assert split_message("") == []

    def test_short_content_single_chunk(self):
        from langclaw.gateway.utils import split_message

        assert split_message("hello world", max_len=100) == ["hello world"]

    def test_exact_limit(self):
        from langclaw.gateway.utils import split_message

        text = "a" * 50
        assert split_message(text, max_len=50) == [text]

    def test_splits_at_newline(self):
        from langclaw.gateway.utils import split_message

        text = "line1\nline2\nline3"
        chunks = split_message(text, max_len=10)
        assert len(chunks) >= 2
        assert "line1" in chunks[0]

    def test_splits_at_space(self):
        from langclaw.gateway.utils import split_message

        text = "word1 word2 word3 word4"
        chunks = split_message(text, max_len=12)
        assert len(chunks) >= 2
        for chunk in chunks:
            assert len(chunk) <= 12

    def test_hard_cut_no_break_point(self):
        from langclaw.gateway.utils import split_message

        text = "a" * 20
        chunks = split_message(text, max_len=8)
        assert len(chunks) >= 3
        for chunk in chunks:
            assert len(chunk) <= 8

    def test_preserves_all_content(self):
        from langclaw.gateway.utils import split_message

        text = "The quick brown fox jumps over the lazy dog"
        chunks = split_message(text, max_len=15)
        reassembled = " ".join(chunks)
        for word in text.split():
            assert word in reassembled


# ---------------------------------------------------------------------------
# gateway.utils — format_tool_progress
# ---------------------------------------------------------------------------


class TestFormatToolProgress:
    def test_markdown_format(self):
        from langclaw.gateway.utils import format_tool_progress

        result = format_tool_progress("read_file", {"path": "/tmp/x.py"}, markup="markdown")
        assert "**" in result
        assert "`/tmp/x.py`" in result

    def test_html_format(self):
        from langclaw.gateway.utils import format_tool_progress

        result = format_tool_progress("read_file", {"path": "/tmp/x.py"}, markup="html")
        assert "<b>" in result
        assert "<code>/tmp/x.py</code>" in result

    def test_file_tools(self):
        from langclaw.gateway.utils import format_tool_progress

        for tool in ("read_file", "write_file", "edit_file"):
            result = format_tool_progress(tool, {"path": "/a/b"})
            assert "`/a/b`" in result

    def test_ls_tool(self):
        from langclaw.gateway.utils import format_tool_progress

        result = format_tool_progress("ls", {"path": "/src"})
        assert "`/src`" in result

    def test_glob_tool(self):
        from langclaw.gateway.utils import format_tool_progress

        result = format_tool_progress("glob", {"pattern": "*.py"})
        assert "`*.py`" in result

    def test_execute_tool_truncates(self):
        from langclaw.gateway.utils import format_tool_progress

        long_cmd = "x" * 100
        result = format_tool_progress("execute", {"command": long_cmd})
        # Should truncate to 60 chars
        assert "`" + "x" * 60 + "`" in result

    def test_task_tool(self):
        from langclaw.gateway.utils import format_tool_progress

        result = format_tool_progress("task", {"description": "do stuff"})
        assert "do stuff" in result

    def test_unknown_tool(self):
        from langclaw.gateway.utils import format_tool_progress

        result = format_tool_progress("my_custom_tool", {"key": "val"})
        assert "my_custom_tool" in result

    def test_empty_args(self):
        from langclaw.gateway.utils import format_tool_progress

        result = format_tool_progress("read_file", {})
        assert "Reading" in result

    def test_default_markup_is_markdown(self):
        from langclaw.gateway.utils import format_tool_progress

        result = format_tool_progress("ls", {"path": "."})
        assert "**" in result
        assert "<b>" not in result


# ---------------------------------------------------------------------------
# gateway.utils — is_allowed
# ---------------------------------------------------------------------------


class TestIsAllowed:
    def test_empty_allow_from_allows_everyone(self):
        from langclaw.gateway.utils import is_allowed

        assert is_allowed([], "any_user") is True

    def test_user_id_match(self):
        from langclaw.gateway.utils import is_allowed

        assert is_allowed(["123", "456"], "123") is True

    def test_user_id_no_match(self):
        from langclaw.gateway.utils import is_allowed

        assert is_allowed(["123", "456"], "789") is False

    def test_username_match(self):
        from langclaw.gateway.utils import is_allowed

        assert is_allowed(["alice", "bob"], "999", username="alice") is True

    def test_username_no_match(self):
        from langclaw.gateway.utils import is_allowed

        assert is_allowed(["alice"], "999", username="charlie") is False

    def test_username_none(self):
        from langclaw.gateway.utils import is_allowed

        assert is_allowed(["alice"], "999", username=None) is False

    def test_user_id_or_username_either_works(self):
        from langclaw.gateway.utils import is_allowed

        assert is_allowed(["alice", "123"], "123", username="bob") is True
        assert is_allowed(["alice", "123"], "999", username="alice") is True


# ---------------------------------------------------------------------------
# gateway.utils — constants
# ---------------------------------------------------------------------------


def test_tool_labels_not_empty():
    from langclaw.gateway.utils import TOOL_LABELS

    assert len(TOOL_LABELS) > 0
    assert "read_file" in TOOL_LABELS
    assert "execute" in TOOL_LABELS


def test_truncation_suffix():
    from langclaw.gateway.utils import TRUNCATION_SUFFIX

    assert isinstance(TRUNCATION_SUFFIX, str)
    assert len(TRUNCATION_SUFFIX) > 0


# ---------------------------------------------------------------------------
# Discord channel — smoke tests
# ---------------------------------------------------------------------------


def test_discord_channel_import():
    from langclaw.gateway.discord import DiscordChannel

    assert DiscordChannel.name == "discord"


def test_discord_channel_instantiation():
    from langclaw.config.schema import DiscordChannelConfig
    from langclaw.gateway.discord import DiscordChannel

    config = DiscordChannelConfig(enabled=True, token="fake-token")
    ch = DiscordChannel(config)
    assert ch.name == "discord"
    assert ch.is_enabled() is True


def test_discord_channel_disabled_without_token():
    from langclaw.config.schema import DiscordChannelConfig
    from langclaw.gateway.discord import DiscordChannel

    ch = DiscordChannel(DiscordChannelConfig(enabled=True, token=""))
    assert ch.is_enabled() is False


def test_discord_channel_disabled_flag():
    from langclaw.config.schema import DiscordChannelConfig
    from langclaw.gateway.discord import DiscordChannel

    ch = DiscordChannel(DiscordChannelConfig(enabled=False, token="some-token"))
    assert ch.is_enabled() is False


def test_discord_channel_allow_from_config():
    from langclaw.config.schema import DiscordChannelConfig
    from langclaw.gateway.discord import DiscordChannel

    config = DiscordChannelConfig(enabled=True, token="fake", allow_from=["user1", "user2"])
    ch = DiscordChannel(config)
    assert ch._is_allowed("user1", None) is True
    assert ch._is_allowed("unknown", "user2") is True
    assert ch._is_allowed("unknown", "unknown") is False


def test_discord_channel_allow_from_empty_allows_all():
    from langclaw.config.schema import DiscordChannelConfig
    from langclaw.gateway.discord import DiscordChannel

    config = DiscordChannelConfig(enabled=True, token="fake", allow_from=[])
    ch = DiscordChannel(config)
    assert ch._is_allowed("anyone", None) is True


# ---------------------------------------------------------------------------
# Discord channel — registered in CLI _build_channels
# ---------------------------------------------------------------------------


def test_discord_in_build_channels(monkeypatch):
    """When discord is enabled, Langclaw._build_all_channels should include a DiscordChannel."""
    from langclaw.app import Langclaw
    from langclaw.config.schema import LangclawConfig

    monkeypatch.setenv("LANGCLAW__CHANNELS__DISCORD__ENABLED", "true")
    monkeypatch.setenv("LANGCLAW__CHANNELS__DISCORD__TOKEN", "fake-token")

    cfg = LangclawConfig()
    assert cfg.channels.discord.enabled is True

    lc = Langclaw(config=cfg)
    channels = lc._build_all_channels()
    names = [ch.name for ch in channels]
    assert "discord" in names


# ---------------------------------------------------------------------------
# Telegram channel — still works after utils refactor
# ---------------------------------------------------------------------------


def test_telegram_channel_import():
    from langclaw.gateway.telegram import TelegramChannel

    assert TelegramChannel.name == "telegram"


def test_telegram_channel_allow_from():
    from langclaw.config.schema import TelegramChannelConfig
    from langclaw.gateway.telegram import TelegramChannel

    config = TelegramChannelConfig(enabled=True, token="fake", allow_from=["alice"])
    ch = TelegramChannel(config)
    assert ch._is_allowed("999", "alice") is True
    assert ch._is_allowed("999", "bob") is False


# ---------------------------------------------------------------------------
# GatewayManager — /agent command tests
# ---------------------------------------------------------------------------


class TestAgentCommand:
    """Tests for the /agent command (list, switch, one-off message).

    These tests manually set up the agent registry and command handler
    to avoid triggering actual agent construction (which requires real
    LLM config). This isolates the command routing logic.
    """

    def _setup_manager_with_agents(self, bus, agent_names):
        """Helper to create a GatewayManager with mocked named agents.

        Creates the manager without named_agent_specs to avoid agent
        construction, then manually populates the agent map and
        registers the /agent command.
        """
        from unittest.mock import MagicMock

        config = MagicMock()
        checkpointer = MagicMock()
        checkpointer.get.return_value = MagicMock()
        agent = MagicMock()

        from langclaw.gateway.manager import GatewayManager

        mgr = GatewayManager(
            config=config,
            bus=bus,
            checkpointer_backend=checkpointer,
            agent=agent,
            channels=[],
        )

        # Manually populate agent registry (bypassing _build_named_agent)
        for name in agent_names:
            mgr._agent_map[name] = MagicMock()
            mgr._agent_descriptions[name] = f"{name} agent"

        # Register the /agent command
        mgr._setup_agent_command()

        return mgr

    async def test_agent_list_shows_available_agents(self):
        """``/agent`` with no args lists all agents with active marker."""
        from unittest.mock import MagicMock

        from langclaw.gateway.commands import CommandContext

        bus = MagicMock()
        mgr = self._setup_manager_with_agents(bus, ["researcher", "coder"])

        cmd_entry = mgr._command_router._commands.get("agent")
        assert cmd_entry is not None, "/agent command should be registered"
        cmd_handler = cmd_entry.handler

        ctx = CommandContext(
            channel="test",
            user_id="user1",
            context_id="ctx1",
            chat_id="chat1",
            args=[],
        )
        result = await cmd_handler(ctx)

        assert "Available agents:" in result
        assert "default" in result
        assert "researcher" in result
        assert "coder" in result

    async def test_agent_switch_persistent(self):
        """``/agent <name>`` switches session persistently."""
        from unittest.mock import MagicMock

        from langclaw.gateway.commands import CommandContext

        bus = MagicMock()
        mgr = self._setup_manager_with_agents(bus, ["researcher"])

        cmd_handler = mgr._command_router._commands.get("agent").handler
        ctx = CommandContext(
            channel="test",
            user_id="user1",
            context_id="ctx1",
            chat_id="chat1",
            args=["researcher"],
        )
        result = await cmd_handler(ctx)

        assert "Switched to agent 'researcher'" in result
        active = await mgr._sessions.get_active_agent("test", "user1")
        assert active == "researcher"

    async def test_agent_switch_to_default(self):
        """``/agent default`` returns to main agent."""
        from unittest.mock import MagicMock

        from langclaw.gateway.commands import CommandContext

        bus = MagicMock()
        mgr = self._setup_manager_with_agents(bus, ["researcher"])

        cmd_handler = mgr._command_router._commands.get("agent").handler
        ctx = CommandContext(
            channel="test",
            user_id="user1",
            context_id="ctx1",
            chat_id="chat1",
            args=["researcher"],
        )
        await cmd_handler(ctx)

        # Now switch back to default
        ctx.args = ["default"]
        result = await cmd_handler(ctx)

        assert "Switched back to the main agent" in result
        active = await mgr._sessions.get_active_agent("test", "user1")
        assert active == "default"

    async def test_agent_unknown_name_returns_error(self):
        """``/agent unknown`` returns error with available agents."""
        from unittest.mock import MagicMock

        from langclaw.gateway.commands import CommandContext

        bus = MagicMock()
        mgr = self._setup_manager_with_agents(bus, ["researcher"])

        cmd_handler = mgr._command_router._commands.get("agent").handler
        ctx = CommandContext(
            channel="test",
            user_id="user1",
            context_id="ctx1",
            chat_id="chat1",
            args=["nonexistent"],
        )
        result = await cmd_handler(ctx)

        assert "Unknown agent 'nonexistent'" in result
        assert "researcher" in result
        assert "/agent default" in result

    async def test_agent_oneoff_message_publishes_to_bus(self):
        """``/agent <name> <msg>`` publishes to bus, no session change."""
        from unittest.mock import AsyncMock, MagicMock

        from langclaw.bus.base import InboundMessage
        from langclaw.gateway.commands import CommandContext

        bus = MagicMock()
        bus.publish = AsyncMock()
        mgr = self._setup_manager_with_agents(bus, ["researcher"])

        cmd_handler = mgr._command_router._commands.get("agent").handler
        ctx = CommandContext(
            channel="telegram",
            user_id="user1",
            context_id="ctx1",
            chat_id="chat1",
            args=["researcher", "What", "is", "the", "quarterly", "report?"],
        )
        result = await cmd_handler(ctx)

        # Should return empty string (agent will reply)
        assert result == ""

        # Verify bus.publish was called with correct InboundMessage
        bus.publish.assert_called_once()
        call_args = bus.publish.call_args
        msg = call_args[0][0]

        assert isinstance(msg, InboundMessage)
        assert msg.channel == "telegram"
        assert msg.user_id == "user1"
        assert msg.context_id == "ctx1"
        assert msg.chat_id == "chat1"
        assert msg.content == "What is the quarterly report?"
        assert msg.metadata.get("agent_name") == "researcher"

        # Session should NOT have changed
        active = await mgr._sessions.get_active_agent("telegram", "user1")
        assert active == "default"

    async def test_agent_oneoff_preserves_active_session(self):
        """One-off message does not change the user's active agent session."""
        from unittest.mock import AsyncMock, MagicMock

        from langclaw.gateway.commands import CommandContext

        bus = MagicMock()
        bus.publish = AsyncMock()
        mgr = self._setup_manager_with_agents(bus, ["researcher", "coder"])

        cmd_handler = mgr._command_router._commands.get("agent").handler

        # First, switch to coder persistently
        ctx = CommandContext(
            channel="test",
            user_id="user1",
            context_id="ctx1",
            chat_id="chat1",
            args=["coder"],
        )
        await cmd_handler(ctx)
        assert await mgr._sessions.get_active_agent("test", "user1") == "coder"

        # Now send one-off to researcher
        ctx.args = ["researcher", "Help", "me"]
        await cmd_handler(ctx)

        # Session should still be coder
        assert await mgr._sessions.get_active_agent("test", "user1") == "coder"


# ---------------------------------------------------------------------------
# gateway.manager — _handle_message_chunk (regression for issue #26)
# ---------------------------------------------------------------------------


class TestHandleMessageChunk:
    """Regression coverage for issue #26 — middleware-generated chunks
    must not leak into ``stream_mode="messages"`` output.

    LangGraph emits message chunks for *every* LLM call in the compiled
    graph, including nested calls from middleware nodes (e.g.
    ``SummarizationMiddleware`` invoking its summary model). The handler
    must filter on the ``langgraph_node`` carried in the chunk metadata
    and only forward chunks from the user-facing ``"model"`` node.
    """

    def _make_manager(self):
        from unittest.mock import MagicMock

        from langclaw.gateway.manager import GatewayManager

        config = MagicMock()
        checkpointer = MagicMock()
        checkpointer.get.return_value = MagicMock()
        agent = MagicMock()

        return GatewayManager(
            config=config,
            bus=MagicMock(),
            checkpointer_backend=checkpointer,
            agent=agent,
            channels=[],
        )

    def _make_msg(self):
        from langclaw.bus.base import InboundMessage

        return InboundMessage(
            channel="websocket",
            user_id="u1",
            context_id="c1",
            chat_id="c1",
            content="hi",
        )

    class _FakeChannel:
        def __init__(self):
            self.sent: list = []

        async def send(self, m):
            self.sent.append(m)

    async def test_drops_chunk_from_summarization_middleware_node(self):
        """The exact failure mode from issue #26: a HumanMessage-shaped
        summary leaking through is one symptom, but the underlying cause
        is the summarization model's *AIMessageChunk* tokens streaming
        out of ``SummarizationMiddleware.before_model``."""
        from langchain_core.messages import AIMessageChunk

        mgr = self._make_manager()
        msg = self._make_msg()
        channel = self._FakeChannel()

        chunk = (
            AIMessageChunk(content="Here is a summary of the conversation"),
            {"langgraph_node": "SummarizationMiddleware.before_model"},
        )
        await mgr._handle_message_chunk(chunk, msg, channel, set())

        assert channel.sent == []

    async def test_forwards_chunk_from_model_node(self):
        """Real model output must still stream end-to-end."""
        from langchain_core.messages import AIMessageChunk

        mgr = self._make_manager()
        msg = self._make_msg()
        channel = self._FakeChannel()
        streaming_contexts: set[str] = set()

        chunk = (
            AIMessageChunk(content="Hello, "),
            {"langgraph_node": "model"},
        )
        await mgr._handle_message_chunk(chunk, msg, channel, streaming_contexts)

        assert len(channel.sent) == 1
        out = channel.sent[0]
        assert out.content == "Hello, "
        assert out.streaming is True
        assert out.is_final is False
        assert out.type == "ai"
        # Marks the context as actively streaming so the updates path
        # knows to skip the duplicate full AIMessage.
        assert "c1" in streaming_contexts

    async def test_drops_chunk_with_missing_metadata(self):
        """Defensive: if LangGraph ever yields a tuple without
        ``langgraph_node``, fail closed (drop) rather than leak."""
        from langchain_core.messages import AIMessageChunk

        mgr = self._make_manager()
        msg = self._make_msg()
        channel = self._FakeChannel()

        chunk = (AIMessageChunk(content="orphan chunk"), {})
        await mgr._handle_message_chunk(chunk, msg, channel, set())
        assert channel.sent == []

        chunk = (AIMessageChunk(content="orphan chunk"), None)
        await mgr._handle_message_chunk(chunk, msg, channel, set())
        assert channel.sent == []

    async def test_drops_non_aimessagechunk_from_model_node(self):
        """Existing behavior preserved: non-AIMessageChunk objects (e.g.
        tool messages routed via the messages stream) are dropped even
        when they originate from the ``"model"`` node."""
        from langchain_core.messages import HumanMessage

        mgr = self._make_manager()
        msg = self._make_msg()
        channel = self._FakeChannel()

        chunk = (
            HumanMessage(content="not an AI chunk"),
            {"langgraph_node": "model"},
        )
        await mgr._handle_message_chunk(chunk, msg, channel, set())
        assert channel.sent == []
