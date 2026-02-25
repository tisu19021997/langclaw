"""
Smoke tests — verify core modules import and instantiate correctly
without requiring any LLM API keys or external services.
"""

from __future__ import annotations

import pytest

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


def test_config_default_values():
    """LangclawConfig should load and parse correctly (respects .env overrides)."""
    from langclaw.config import load_config

    cfg = load_config()
    assert isinstance(cfg.agents.model, str) and cfg.agents.model
    assert cfg.bus.backend in ("asyncio", "rabbitmq", "kafka")
    assert cfg.checkpointer.backend in ("sqlite", "postgres")
    assert isinstance(cfg.agents.rate_limit_rpm, int)
    assert isinstance(cfg.agents.banned_keywords, list)


def test_config_schema_defaults():
    """LangclawConfig model-level defaults are correct when no env overrides."""
    from langclaw.config.schema import AgentConfig, BusConfig, CheckpointerConfig

    assert AgentConfig().model == "anthropic:claude-sonnet-4-5-20250929"
    assert BusConfig().backend == "asyncio"
    assert CheckpointerConfig().backend == "sqlite"
    assert AgentConfig().rate_limit_rpm == 60


def test_config_env_override(monkeypatch):
    """Environment variables should override defaults."""
    monkeypatch.setenv("LANGCLAW__AGENTS__MODEL", "openai:gpt-4.1")
    monkeypatch.setenv("LANGCLAW__BUS__BACKEND", "rabbitmq")

    # Re-instantiate directly to pick up monkeypatched env
    from langclaw.config.schema import LangclawConfig

    cfg = LangclawConfig()
    assert cfg.agents.model == "openai:gpt-4.1"
    assert cfg.bus.backend == "rabbitmq"


# ---------------------------------------------------------------------------
# Provider registry
# ---------------------------------------------------------------------------


def test_provider_registry_list():
    """ProviderRegistry should enumerate built-in providers."""
    from langclaw.config.schema import ProvidersConfig
    from langclaw.providers import provider_registry

    providers_cfg = ProvidersConfig()
    rows = provider_registry.list_configured(providers_cfg)
    names = [r["name"] for r in rows]
    assert "openai" in names
    assert "anthropic" in names
    assert "openrouter" in names


def test_provider_spec_match():
    """ProviderRegistry._match_spec should resolve model strings correctly."""
    from langclaw.providers.registry import ProviderRegistry

    reg = ProviderRegistry()
    spec = reg._match_spec("anthropic:claude-sonnet-4-5-20250929")
    assert spec is not None
    assert spec.name == "anthropic"

    spec2 = reg._match_spec("gpt-4.1")
    assert spec2 is not None
    assert spec2.name == "openai"


# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------


def test_content_filter_middleware_instantiation():
    from langclaw.middleware.guardrails import ContentFilterMiddleware

    mw = ContentFilterMiddleware(banned_keywords=["hack", "exploit"])
    assert mw._keywords == ["hack", "exploit"]


def test_rate_limit_middleware_instantiation():
    from langclaw.middleware.rate_limit import RateLimitMiddleware

    mw = RateLimitMiddleware(rpm=30, burst=5)
    assert mw._rpm == 30
    assert mw._burst == 5


def test_pii_middleware_importable():
    from langclaw.middleware import PIIMiddleware

    assert PIIMiddleware is not None


# ---------------------------------------------------------------------------
# Checkpointer backends
# ---------------------------------------------------------------------------


def test_checkpointer_factory_sqlite():
    from langclaw.checkpointer import (
        SqliteCheckpointerBackend,
        make_checkpointer_backend,
    )

    backend = make_checkpointer_backend("sqlite", db_path="/tmp/test_langclaw.db")
    assert isinstance(backend, SqliteCheckpointerBackend)


def test_checkpointer_factory_postgres():
    from langclaw.checkpointer import (
        PostgresCheckpointerBackend,
        make_checkpointer_backend,
    )

    backend = make_checkpointer_backend(
        "postgres", dsn="postgresql://user:pass@localhost/db"
    )
    assert isinstance(backend, PostgresCheckpointerBackend)


def test_checkpointer_factory_unknown():
    from langclaw.checkpointer import make_checkpointer_backend

    with pytest.raises(ValueError, match="Unknown checkpointer backend"):
        make_checkpointer_backend("redis")


# ---------------------------------------------------------------------------
# Message bus
# ---------------------------------------------------------------------------


def test_bus_factory_asyncio():
    from langclaw.bus import AsyncioMessageBus, make_message_bus

    bus = make_message_bus("asyncio")
    assert isinstance(bus, AsyncioMessageBus)


def test_bus_factory_rabbitmq():
    from langclaw.bus import RabbitMQMessageBus, make_message_bus

    bus = make_message_bus("rabbitmq", rabbitmq_url="amqp://localhost/")
    assert isinstance(bus, RabbitMQMessageBus)


def test_bus_factory_kafka():
    from langclaw.bus import KafkaMessageBus, make_message_bus

    bus = make_message_bus("kafka", kafka_servers="localhost:9092")
    assert isinstance(bus, KafkaMessageBus)


def test_bus_factory_unknown():
    from langclaw.bus import make_message_bus

    with pytest.raises(ValueError, match="Unknown bus backend"):
        make_message_bus("nats")


def test_inbound_message_dataclass():
    from langclaw.bus.base import InboundMessage

    msg = InboundMessage(
        channel="telegram",
        user_id="123",
        context_id="456",
        content="hello",
        metadata={"source": "channel"},
    )
    assert msg.channel == "telegram"
    assert msg.metadata["source"] == "channel"
    assert msg.attachments == []


# ---------------------------------------------------------------------------
# Session manager
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_session_manager_creates_thread():
    from langclaw.session import SessionManager

    sm = SessionManager()
    tid = await sm.get_or_create_thread("telegram", "user1", "chat1")
    assert isinstance(tid, str) and len(tid) == 36  # UUID format

    # Same key → same thread_id
    tid2 = await sm.get_or_create_thread("telegram", "user1", "chat1")
    assert tid == tid2

    # Different user → different thread_id
    tid3 = await sm.get_or_create_thread("telegram", "user2", "chat1")
    assert tid3 != tid


@pytest.mark.asyncio
async def test_session_manager_delete():
    from langclaw.session import SessionManager

    sm = SessionManager()
    await sm.get_or_create_thread("telegram", "user1")
    deleted = await sm.delete_thread("telegram", "user1")
    assert deleted is True
    # After delete a new thread is created
    tid_new = await sm.get_or_create_thread("telegram", "user1")
    assert tid_new  # Non-empty UUID


@pytest.mark.asyncio
async def test_session_manager_runnable_config():
    from langclaw.session import SessionManager

    sm = SessionManager()
    cfg = await sm.get_config(
        "telegram", "user1", channel_context={"channel": "telegram", "user_id": "user1"}
    )
    assert "configurable" in cfg
    assert "thread_id" in cfg["configurable"]
    assert cfg["configurable"]["channel_context"]["channel"] == "telegram"


# ---------------------------------------------------------------------------
# Asyncio bus lifecycle
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_asyncio_bus_publish_subscribe():
    from langclaw.bus import AsyncioMessageBus
    from langclaw.bus.base import InboundMessage

    bus = AsyncioMessageBus()
    async with bus:
        msg = InboundMessage(
            channel="test", user_id="u1", context_id="c1", content="hello"
        )
        await bus.publish(msg)

        received = None
        async for m in bus.subscribe():
            received = m
            break

        assert received is not None
        assert received.content == "hello"


# ---------------------------------------------------------------------------
# Gateway base channel
# ---------------------------------------------------------------------------


def test_base_channel_is_abstract():
    import inspect

    from langclaw.gateway.base import BaseChannel

    assert inspect.isabstract(BaseChannel)


# ---------------------------------------------------------------------------
# Heartbeat
# ---------------------------------------------------------------------------


def test_heartbeat_condition_is_abstract():
    import inspect

    from langclaw.heartbeat import HeartbeatCondition

    assert inspect.isabstract(HeartbeatCondition)


@pytest.mark.asyncio
async def test_heartbeat_manager_fires(monkeypatch):
    from langclaw.bus import AsyncioMessageBus
    from langclaw.bus.base import InboundMessage
    from langclaw.heartbeat import HeartbeatCondition, HeartbeatManager, HeartbeatTarget

    class AlwaysFire(HeartbeatCondition):
        name = "always"

        async def check(self) -> str | None:
            return "test heartbeat triggered"

    fired: list[InboundMessage] = []

    bus = AsyncioMessageBus()
    async with bus:
        manager = HeartbeatManager(
            bus=bus,
            interval=9999,  # We'll tick manually
            conditions=[(AlwaysFire(), HeartbeatTarget("test", "user1"))],
        )
        await manager._tick()

        async for m in bus.subscribe():
            fired.append(m)
            break

    assert len(fired) == 1
    assert fired[0].metadata["source"] == "heartbeat"
    assert fired[0].content == "test heartbeat triggered"


# ---------------------------------------------------------------------------
# Defaults directory
# ---------------------------------------------------------------------------


def test_default_skills_exist():
    from langclaw.agents.builder import _DEFAULTS_DIR

    assert (_DEFAULTS_DIR / "AGENTS.md").exists()
    assert (_DEFAULTS_DIR / "skills" / "summarize" / "SKILL.md").exists()


def test_config_workspace_paths():
    """agents_md_file, skills_dir and memories_dir all resolve under workspace_dir."""
    from langclaw.config.schema import LangclawConfig

    cfg = LangclawConfig()
    agents = cfg.agents
    assert agents.agents_md_file == agents.workspace_dir / "AGENTS.md"
    assert agents.skills_dir == agents.workspace_dir / "skills"
    assert agents.memories_dir == agents.workspace_dir / "memories"
    # Tilde must be expanded — path should not start with '~'
    assert not str(agents.workspace_dir).startswith("~")
