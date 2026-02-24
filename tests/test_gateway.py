"""
Tests for gateway utilities and channel implementations.

Covers shared helpers (split_message, format_tool_progress, is_allowed)
and channel smoke tests (import, instantiation, is_enabled).
"""

from __future__ import annotations

import pytest

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

    config = DiscordChannelConfig(
        enabled=True, token="fake", allow_from=["user1", "user2"]
    )
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
    """When discord is enabled, _build_channels should include a DiscordChannel."""
    from langclaw.config.schema import LangclawConfig

    monkeypatch.setenv("LANGCLAW__CHANNELS__DISCORD__ENABLED", "true")
    monkeypatch.setenv("LANGCLAW__CHANNELS__DISCORD__TOKEN", "fake-token")

    cfg = LangclawConfig()
    assert cfg.channels.discord.enabled is True

    from langclaw.cli.app import _build_channels

    channels = _build_channels(cfg)
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

    config = TelegramChannelConfig(
        enabled=True, token="fake", allow_from=["alice"]
    )
    ch = TelegramChannel(config)
    assert ch._is_allowed("999", "alice") is True
    assert ch._is_allowed("999", "bob") is False
