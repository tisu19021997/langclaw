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
# Telegram channel — multi-token (one process, multiple labeled bots)
# ---------------------------------------------------------------------------


def test_telegram_labeled_tokens_parser_comma_equals():
    """``label=token`` pairs must parse into a dict (uses ``=`` because
    Telegram tokens contain ``:``)."""
    from langclaw.config.schema import _parse_labeled_tokens

    assert _parse_labeled_tokens("support=111:abc,oncall=222:def") == {
        "support": "111:abc",
        "oncall": "222:def",
    }


def test_telegram_labeled_tokens_parser_json():
    from langclaw.config.schema import _parse_labeled_tokens

    assert _parse_labeled_tokens('{"support":"111:abc"}') == {"support": "111:abc"}


def test_telegram_labeled_tokens_parser_empty():
    from langclaw.config.schema import _parse_labeled_tokens

    assert _parse_labeled_tokens("") == {}
    assert _parse_labeled_tokens("   ") == {}


def test_telegram_labeled_tokens_parser_skips_malformed():
    """Pairs without ``=`` or with empty key/value must be silently dropped."""
    from langclaw.config.schema import _parse_labeled_tokens

    assert _parse_labeled_tokens("support=111:abc,garbage,oncall=222:def") == {
        "support": "111:abc",
        "oncall": "222:def",
    }
    assert _parse_labeled_tokens("=orphan,good=ok") == {"good": "ok"}


def test_telegram_config_resolved_tokens_prefers_tokens_dict():
    from langclaw.config.schema import TelegramChannelConfig

    cfg = TelegramChannelConfig(enabled=True, tokens={"support": "111:abc", "oncall": "222:def"})
    assert cfg.resolved_tokens() == {"support": "111:abc", "oncall": "222:def"}


def test_telegram_config_resolved_tokens_falls_back_to_token():
    """Legacy single-token mode surfaces as an empty-label entry so
    ``_build_all_channels`` can route it to the classic ``telegram`` key."""
    from langclaw.config.schema import TelegramChannelConfig

    cfg = TelegramChannelConfig(enabled=True, token="legacy")
    assert cfg.resolved_tokens() == {"": "legacy"}


def test_telegram_config_resolved_tokens_empty():
    from langclaw.config.schema import TelegramChannelConfig

    cfg = TelegramChannelConfig(enabled=True)
    assert cfg.resolved_tokens() == {}


def test_telegram_instance_id_shadows_name_with_label():
    """Passing a label as ``instance_id`` shadows ``name`` per instance."""
    from langclaw.config.schema import TelegramChannelConfig
    from langclaw.gateway.telegram import TelegramChannel

    ch_support = TelegramChannel(
        TelegramChannelConfig(enabled=True, token="t1"), instance_id="support"
    )
    ch_oncall = TelegramChannel(
        TelegramChannelConfig(enabled=True, token="t2"), instance_id="oncall"
    )

    assert ch_support.name == "telegram:support"
    assert ch_oncall.name == "telegram:oncall"
    # Class attribute is untouched
    assert TelegramChannel.name == "telegram"
    # Name-keyed routing map must not collide
    mp = {ch.name: ch for ch in (ch_support, ch_oncall)}
    assert len(mp) == 2


def test_telegram_single_bot_in_build_channels(monkeypatch):
    """Single-token configs must still register under the classic ``telegram`` key."""
    from langclaw.app import Langclaw
    from langclaw.config.schema import LangclawConfig

    monkeypatch.setenv("LANGCLAW__CHANNELS__TELEGRAM__ENABLED", "true")
    monkeypatch.setenv("LANGCLAW__CHANNELS__TELEGRAM__TOKEN", "legacy-token")

    cfg = LangclawConfig()
    lc = Langclaw(config=cfg)
    channels = lc._build_all_channels()

    telegram_channels = [ch for ch in channels if ch.name.startswith("telegram")]
    assert len(telegram_channels) == 1
    assert telegram_channels[0].name == "telegram"


def test_telegram_multi_token_in_build_channels(monkeypatch):
    """Labeled ``tokens`` must spawn one channel per label with stable keys."""
    from langclaw.app import Langclaw
    from langclaw.config.schema import LangclawConfig

    monkeypatch.setenv("LANGCLAW__CHANNELS__TELEGRAM__ENABLED", "true")
    monkeypatch.setenv(
        "LANGCLAW__CHANNELS__TELEGRAM__TOKENS",
        "support=111:abc,oncall=222:def",
    )

    cfg = LangclawConfig()
    assert cfg.channels.telegram.tokens == {
        "support": "111:abc",
        "oncall": "222:def",
    }

    lc = Langclaw(config=cfg)
    channels = lc._build_all_channels()

    telegram_channels = [ch for ch in channels if ch.name.startswith("telegram")]
    assert len(telegram_channels) == 2

    names = sorted(ch.name for ch in telegram_channels)
    assert names == ["telegram:oncall", "telegram:support"]

    # Each channel must see its own isolated token (not the full mapping).
    by_name = {ch.name: ch for ch in telegram_channels}
    assert by_name["telegram:support"]._config.token == "111:abc"
    assert by_name["telegram:oncall"]._config.token == "222:def"
    for ch in telegram_channels:
        assert ch._config.tokens == {}

    # And they must not collide in a name-keyed routing map.
    assert len(by_name) == 2


def test_telegram_tokens_takes_precedence_over_token(monkeypatch):
    """When both ``token`` and ``tokens`` are set, ``tokens`` wins."""
    from langclaw.app import Langclaw
    from langclaw.config.schema import LangclawConfig

    monkeypatch.setenv("LANGCLAW__CHANNELS__TELEGRAM__ENABLED", "true")
    monkeypatch.setenv("LANGCLAW__CHANNELS__TELEGRAM__TOKEN", "legacy")
    monkeypatch.setenv("LANGCLAW__CHANNELS__TELEGRAM__TOKENS", "a=new-a,b=new-b")

    cfg = LangclawConfig()
    lc = Langclaw(config=cfg)
    channels = lc._build_all_channels()

    telegram_channels = [ch for ch in channels if ch.name.startswith("telegram")]
    assert len(telegram_channels) == 2
    tokens = sorted(ch._config.token for ch in telegram_channels)
    assert tokens == ["new-a", "new-b"]
    names = sorted(ch.name for ch in telegram_channels)
    assert names == ["telegram:a", "telegram:b"]


# ---------------------------------------------------------------------------
# GatewayManager — channel-instance auto-routing to named agents
# ---------------------------------------------------------------------------


def _make_manager(agent_names=()):
    """Build a GatewayManager with mocked named agents (no LLM construction)."""
    from unittest.mock import MagicMock

    from langclaw.gateway.manager import GatewayManager

    config = MagicMock()
    checkpointer = MagicMock()
    checkpointer.get.return_value = MagicMock()
    agent = MagicMock()
    bus = MagicMock()

    mgr = GatewayManager(
        config=config,
        bus=bus,
        checkpointer_backend=checkpointer,
        agent=agent,
        channels=[],
    )
    for name in agent_names:
        mgr._agent_map[name] = MagicMock()
        mgr._agent_descriptions[name] = f"{name} agent"
    return mgr


async def test_auto_route_channel_label_to_named_agent():
    """``telegram:support`` must auto-route to named agent ``support``."""
    from langclaw.bus.base import InboundMessage

    mgr = _make_manager(agent_names=["support", "oncall"])
    msg = InboundMessage(
        channel="telegram:support",
        user_id="u1",
        context_id="chat1",
        chat_id="chat1",
        content="hi",
    )
    assert await mgr._resolve_agent_name(msg) == "support"


async def test_auto_route_falls_through_without_matching_agent():
    """A labeled channel with no matching named agent must fall back to default."""
    from langclaw.bus.base import InboundMessage

    mgr = _make_manager(agent_names=["support"])
    msg = InboundMessage(
        channel="telegram:unknown",
        user_id="u1",
        context_id="chat1",
        chat_id="chat1",
        content="hi",
    )
    assert await mgr._resolve_agent_name(msg) == "default"


async def test_auto_route_ignores_default_label():
    """Label ``"default"`` must not collide with the always-present default agent."""
    from langclaw.bus.base import InboundMessage

    mgr = _make_manager(agent_names=[])  # only "default" in _agent_map
    msg = InboundMessage(
        channel="telegram:default",
        user_id="u1",
        context_id="chat1",
        chat_id="chat1",
        content="hi",
    )
    # Should NOT auto-route — falls through to session lookup → "default" anyway,
    # but crucially it didn't match via the auto-route rule (covered by the label
    # filter). This guards against a future change where the fallback differs.
    assert await mgr._resolve_agent_name(msg) == "default"


async def test_auto_route_single_bot_channel_no_label():
    """Plain ``telegram`` (single-bot mode) must never auto-route."""
    from langclaw.bus.base import InboundMessage

    mgr = _make_manager(agent_names=["support"])
    msg = InboundMessage(
        channel="telegram",  # classic single-bot key, no ":"
        user_id="u1",
        context_id="chat1",
        chat_id="chat1",
        content="hi",
    )
    assert await mgr._resolve_agent_name(msg) == "default"


async def test_metadata_agent_name_beats_auto_route():
    """Cron-stamped ``metadata['agent_name']`` must win over auto-route."""
    from langclaw.bus.base import InboundMessage

    mgr = _make_manager(agent_names=["support", "oncall"])
    msg = InboundMessage(
        channel="telegram:support",  # would auto-route to "support"
        user_id="u1",
        context_id="chat1",
        chat_id="chat1",
        content="hi",
        metadata={"agent_name": "oncall"},  # but cron said oncall
    )
    assert await mgr._resolve_agent_name(msg) == "oncall"


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
