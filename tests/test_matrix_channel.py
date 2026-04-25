"""
Tests for Matrix channel configuration and basic functionality.
"""

from __future__ import annotations

import pytest


class TestMatrixConfig:
    """Test Matrix channel configuration."""

    def test_matrix_config_default(self):
        """Test that Matrix config has correct defaults."""
        from langclaw.config.schema import MatrixChannelConfig

        config = MatrixChannelConfig()
        assert config.enabled is False
        assert config.homeserver_url == ""
        assert config.user_id == ""
        assert config.access_token == ""
        assert config.device_id == ""
        assert config.store_path == ""
        assert config.auto_join_invites is True
        assert config.allow_from == []
        assert config.user_roles == {}
        assert config.e2ee_enabled is False

    def test_matrix_config_from_dict(self):
        """Test creating Matrix config from dict."""
        from langclaw.config.schema import MatrixChannelConfig

        config = MatrixChannelConfig(
            enabled=True,
            homeserver_url="https://matrix.org",
            user_id="@mybot:matrix.org",
            access_token="syt_test",
            device_id="ABCD1234",
            allow_from=["@alice:matrix.org"],
            user_roles={"@alice:matrix.org": "admin"},
        )
        assert config.enabled is True
        assert config.homeserver_url == "https://matrix.org"
        assert config.user_id == "@mybot:matrix.org"
        assert config.access_token == "syt_test"
        assert config.device_id == "ABCD1234"
        assert config.allow_from == ["@alice:matrix.org"]
        assert config.user_roles == {"@alice:matrix.org": "admin"}

    def test_matrix_config_env_string_list(self):
        """Comma-separated strings are parsed into allow_from lists."""
        from langclaw.config.schema import MatrixChannelConfig

        config = MatrixChannelConfig(
            enabled=True,
            homeserver_url="https://matrix.org",
            user_id="@mybot:matrix.org",
            access_token="syt_test",
            device_id="ABCD1234",
            allow_from="@alice:matrix.org,@bob:matrix.org",
        )
        assert config.allow_from == ["@alice:matrix.org", "@bob:matrix.org"]


class TestMatrixChannel:
    """Test Matrix channel implementation."""

    def test_matrix_channel_import(self):
        """Test that MatrixChannel can be imported."""
        from langclaw.gateway.matrix import MatrixChannel

        assert MatrixChannel is not None
        assert MatrixChannel.name == "matrix"

    def test_matrix_channel_instantiation(self):
        """Test that MatrixChannel can be instantiated with config."""
        from langclaw.config.schema import MatrixChannelConfig
        from langclaw.gateway.matrix import MatrixChannel

        config = MatrixChannelConfig(
            enabled=True,
            homeserver_url="https://matrix.org",
            user_id="@mybot:matrix.org",
            access_token="syt_test",
            device_id="ABCD1234",
        )
        channel = MatrixChannel(config)
        assert channel.name == "matrix"
        assert channel._config == config

    def test_matrix_channel_is_enabled_false_by_default(self):
        """Channel is disabled when required credentials are missing."""
        from langclaw.config.schema import MatrixChannelConfig
        from langclaw.gateway.matrix import MatrixChannel

        config = MatrixChannelConfig(enabled=True)  # enabled but no credentials
        channel = MatrixChannel(config)
        assert channel.is_enabled() is False

    def test_matrix_channel_is_enabled_missing_device_id(self):
        """Channel stays disabled if device_id is missing."""
        from langclaw.config.schema import MatrixChannelConfig
        from langclaw.gateway.matrix import MatrixChannel

        config = MatrixChannelConfig(
            enabled=True,
            homeserver_url="https://matrix.org",
            user_id="@mybot:matrix.org",
            access_token="syt_test",
            # device_id omitted
        )
        channel = MatrixChannel(config)
        assert channel.is_enabled() is False

    def test_matrix_channel_is_enabled_with_credentials(self):
        """Channel is enabled when all four credential fields are populated."""
        from langclaw.config.schema import MatrixChannelConfig
        from langclaw.gateway.matrix import MatrixChannel

        config = MatrixChannelConfig(
            enabled=True,
            homeserver_url="https://matrix.org",
            user_id="@mybot:matrix.org",
            access_token="syt_test",
            device_id="ABCD1234",
        )
        channel = MatrixChannel(config)
        assert channel.is_enabled() is True

    def test_matrix_channel_allow_from(self):
        """User whitelist matches only on Matrix IDs listed in allow_from."""
        from langclaw.config.schema import MatrixChannelConfig
        from langclaw.gateway.matrix import MatrixChannel

        config = MatrixChannelConfig(
            enabled=True,
            homeserver_url="https://matrix.org",
            user_id="@mybot:matrix.org",
            access_token="syt_test",
            device_id="ABCD1234",
            allow_from=["@alice:matrix.org"],
        )
        channel = MatrixChannel(config)

        assert channel._is_allowed("@alice:matrix.org") is True
        assert channel._is_allowed("@eve:matrix.org") is False

        # Empty allow_from means allow all.
        config.allow_from = []
        channel = MatrixChannel(config)
        assert channel._is_allowed("@anyone:matrix.org") is True

    def test_matrix_channel_rejects_e2ee_enabled(self):
        """Starting the channel with e2ee_enabled=True must fail loudly."""
        import asyncio

        from langclaw.config.schema import MatrixChannelConfig
        from langclaw.gateway.matrix import MatrixChannel

        config = MatrixChannelConfig(
            enabled=True,
            homeserver_url="https://matrix.org",
            user_id="@mybot:matrix.org",
            access_token="syt_test",
            device_id="ABCD1234",
            e2ee_enabled=True,
        )
        channel = MatrixChannel(config)

        pytest.importorskip("nio")  # Only meaningful when matrix-nio is installed.

        async def _run() -> None:
            await channel.start(bus=None)  # type: ignore[arg-type]

        with pytest.raises(RuntimeError, match="e2ee_enabled=True"):
            asyncio.run(_run())

    def test_matrix_channel_markdown_to_html(self):
        """The Matrix HTML renderer escapes, formats, and fences code blocks."""
        from langclaw.gateway.matrix import _markdown_to_matrix_html

        src = "Hello **world** with `code` and <script>"
        rendered = _markdown_to_matrix_html(src)
        assert "<b>world</b>" in rendered
        assert "<code>code</code>" in rendered
        # Angle-brackets in user input must be escaped, not passed through.
        assert "&lt;script&gt;" in rendered
        assert "<script>" not in rendered

    def test_matrix_channel_in_channels_config(self):
        """MatrixChannelConfig is wired into ChannelsConfig."""
        from langclaw.config.schema import ChannelsConfig, MatrixChannelConfig

        channels = ChannelsConfig()
        assert isinstance(channels.matrix, MatrixChannelConfig)
        assert channels.matrix.enabled is False
