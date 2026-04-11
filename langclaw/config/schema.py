"""
Pydantic-settings config schema for langclaw.

Load priority (highest to lowest):
  1. Environment variables  (LANGCLAW__AGENTS__MODEL=...)
  2. ~/.langclaw/config.json
  3. Built-in defaults
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import BaseModel, BeforeValidator, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic_settings.sources.providers.dotenv import DotEnvSettingsSource
from pydantic_settings.sources.providers.env import EnvSettingsSource

# ---------------------------------------------------------------------------
# Custom settings sources that accept comma-separated strings for list fields
# ---------------------------------------------------------------------------


class _CommaListMixin:
    """
    Overrides pydantic-settings' decode_complex_value so that list[str] fields
    can be supplied as plain comma-separated strings in .env / env vars
    instead of requiring JSON arrays.

    Examples that all work:
        LANGCLAW__CHANNELS__TELEGRAM__ALLOW_FROM=alice,bob
        LANGCLAW__CHANNELS__TELEGRAM__ALLOW_FROM=["alice","bob"]
        LANGCLAW__CHANNELS__TELEGRAM__ALLOW_FROM=   (empty → [])
    """

    def decode_complex_value(self, field_name: str, field_info: object, value: str) -> object:
        try:
            return json.loads(value)
        except (json.JSONDecodeError, ValueError):
            if isinstance(value, str):
                stripped = value.strip()
                if not stripped:
                    return []
                return [item.strip() for item in stripped.split(",") if item.strip()]
            return value


class _LangclawEnvSource(_CommaListMixin, EnvSettingsSource):  # type: ignore[misc]
    pass


class _LangclawDotEnvSource(_CommaListMixin, DotEnvSettingsSource):  # type: ignore[misc]
    pass


# Keep BeforeValidator as a second-layer defence for non-env code paths
def _parse_str_list(v: object) -> list[str]:
    if isinstance(v, list):
        return [str(i) for i in v]
    if isinstance(v, str):
        v = v.strip()
        if not v:
            return []
        if v.startswith("["):
            return json.loads(v)
        return [item.strip() for item in v.split(",") if item.strip()]
    return v  # type: ignore[return-value]


StringList = Annotated[list[str], BeforeValidator(_parse_str_list)]


def _parse_str_dict(v: object) -> dict[str, str]:
    """Parse ``"key:val,key:val"`` strings into a dict.

    Accepts:
        ``{"a": "b"}``              — pass-through
        ``['alice:admin','bob:viewer']`` — list (from env source splitting)
        ``'alice:admin,bob:viewer'`` — comma+colon format
        ``'{"a":"b"}'``             — JSON string
        ``''``                       — empty → {}
    """
    if isinstance(v, dict):
        return {str(k): str(val) for k, val in v.items()}
    if isinstance(v, list):
        v = ",".join(str(item) for item in v)
    if isinstance(v, str):
        v = v.strip()
        if not v:
            return {}
        if v.startswith("{"):
            return json.loads(v)
        result: dict[str, str] = {}
        for pair in v.split(","):
            pair = pair.strip()
            if not pair:
                continue
            key, _, val = pair.partition(":")
            if key.strip() and val.strip():
                result[key.strip()] = val.strip()
        return result
    return v  # type: ignore[return-value]


StringDict = Annotated[dict[str, str], BeforeValidator(_parse_str_dict)]


def _parse_labeled_tokens(v: object) -> dict[str, str]:
    """Parse ``"label=token,label=token"`` strings into a dict.

    Uses ``=`` as the key/value separator (rather than ``:``) because
    Telegram bot tokens already contain ``:``. For env-var configuration
    of multiple Telegram bots::

        LANGCLAW__CHANNELS__TELEGRAM__TOKENS=main=123:abc,backup=456:def

    Accepts:
        ``{"main": "123:abc"}``            — pass-through
        ``'main=123:abc,backup=456:def'``  — comma+equals format
        ``'{"main":"123:abc"}'``           — JSON string
        ``''``                              — empty → {}
    """
    if isinstance(v, dict):
        return {str(k): str(val) for k, val in v.items()}
    if isinstance(v, list):
        v = ",".join(str(item) for item in v)
    if isinstance(v, str):
        v = v.strip()
        if not v:
            return {}
        if v.startswith("{"):
            return json.loads(v)
        result: dict[str, str] = {}
        for pair in v.split(","):
            pair = pair.strip()
            if not pair:
                continue
            key, sep, val = pair.partition("=")
            if sep and key.strip() and val.strip():
                result[key.strip()] = val.strip()
        return result
    return v  # type: ignore[return-value]


LabeledTokenDict = Annotated[dict[str, str], BeforeValidator(_parse_labeled_tokens)]

# ---------------------------------------------------------------------------
# Langclaw home
# ---------------------------------------------------------------------------

_LANGCLAW_HOME = Path.home() / ".langclaw"
_CONFIG_PATH = _LANGCLAW_HOME / "config.json"

# ---------------------------------------------------------------------------
# Sub-schemas
# ---------------------------------------------------------------------------


class TelegramChannelConfig(BaseModel):
    enabled: bool = False
    token: str = ""
    """Single bot token. Kept for backwards compatibility; prefer ``tokens``
    when running more than one bot in the same process."""
    tokens: LabeledTokenDict = Field(default_factory=dict)
    """
    Label → bot-token mapping for running multiple Telegram bots in the
    same process, each with a stable routing key of ``telegram:<label>``.

    Env format (comma + equals, because Telegram tokens contain ``:``)::

        LANGCLAW__CHANNELS__TELEGRAM__TOKENS=support=111:abc,oncall=222:def

    JSON also works::

        LANGCLAW__CHANNELS__TELEGRAM__TOKENS='{"support":"111:abc","oncall":"222:def"}'

    When set (non-empty), ``_build_all_channels`` spawns one independent
    ``TelegramChannel`` per entry. Labels must be non-empty strings and
    are used verbatim as the instance ID, so reordering the mapping has
    no effect on routing keys, cron jobs, or session state.

    **Pairs with named agents for full isolation.** If you register a
    named agent whose name matches a bot label (e.g.
    ``app.agent("support", ...)``), ``GatewayManager._resolve_agent_name``
    will auto-route incoming messages from that bot to the matching agent,
    which already gets its own workspace at ``workspace_dir/<label>/``.
    Labels with no matching named agent fall through to the default agent
    and share state with the rest of the channels.

    When ``tokens`` is empty, the legacy single-token ``token`` field is
    used and the channel is registered under the classic ``telegram`` key.
    """

    allow_from: StringList = Field(default_factory=list)
    user_roles: StringDict = Field(default_factory=dict)
    """Maps Telegram user IDs / @usernames to permission roles.
    Env format: ``123456:admin,@alice:editor``"""
    streaming_enabled: bool = False
    """
    Stream AI responses token-by-token by sending one message then editing
    it in place as new content arrives.

    .. warning::
        **Enabling this may degrade reliability.**
        Telegram enforces a global rate limit of ~20 message edits per minute
        per bot.  Under moderate load (multiple concurrent users) this limit is
        easily exceeded, causing ``RetryAfter`` errors and delayed delivery.
        The 300 ms edit throttle reduces — but does not eliminate — the risk.

        Enable only when the live-typing UX is more important than reliability,
        and only in low-traffic environments.  Leave disabled (default) to
        receive the full response as a single message after generation completes.

    Env: ``LANGCLAW__CHANNELS__TELEGRAM__STREAMING_ENABLED=true``
    """

    def resolved_tokens(self) -> dict[str, str]:
        """Return the effective label → token mapping to start.

        Prefers the multi-bot ``tokens`` mapping; falls back to the single
        ``token`` field for backwards compatibility. An empty-string label
        represents legacy single-bot mode and causes the spawned channel
        to register under the classic ``telegram`` routing key (without
        a suffix). Returns an empty dict if neither field is set.
        """
        if self.tokens:
            return dict(self.tokens)
        if self.token:
            return {"": self.token}
        return {}


class DiscordChannelConfig(BaseModel):
    enabled: bool = False
    token: str = ""
    allow_from: StringList = Field(default_factory=list)
    user_roles: StringDict = Field(default_factory=dict)
    """Maps Discord user IDs to permission roles.
    Env format: ``123456:admin,789012:viewer``"""
    streaming_enabled: bool = False
    """
    Stream AI responses token-by-token by sending one message then editing
    it in place as new content arrives.

    .. warning::
        **Enabling this may degrade reliability.**
        Discord allows at most 5 edits per second per message and enforces a
        global 50 req/s REST limit per bot.  High-frequency edits during
        generation can trigger ``429 Too Many Requests`` errors, cause visible
        lag, or result in dropped updates.  The 300 ms throttle mitigates but
        does not prevent this under concurrent load.

        Enable only when the live-typing UX is more important than reliability,
        and only in low-traffic environments.  Leave disabled (default) to
        receive the full response as a single message after generation completes.

    Env: ``LANGCLAW__CHANNELS__DISCORD__STREAMING_ENABLED=true``
    """


class WebSocketChannelConfig(BaseModel):
    enabled: bool = False
    host: str = "127.0.0.1"
    port: int = 18789
    allow_from: StringList = Field(default_factory=list)
    user_roles: StringDict = Field(default_factory=dict)
    """Maps WebSocket user IDs to permission roles.
    Env format: ``user1:admin,user2:viewer``"""
    streaming_enabled: bool = True
    """
    Stream AI responses token-by-token, emitting ``{"type": "ai_chunk"}``
    events as content is generated, followed by ``{"type": "ai_stream_end"}``.

    Unlike Telegram, Slack, and Discord, WebSocket streaming carries no
    rate-limit risk — chunks are pushed directly over the open socket without
    any platform API calls.  Clients should accumulate ``ai_chunk`` payloads
    and render them incrementally.

    Defaults to ``True``.  Set to ``False`` to receive a single
    ``{"type": "ai"}`` event with the complete response instead.

    Env: ``LANGCLAW__CHANNELS__WEBSOCKET__STREAMING_ENABLED=false``
    """


class SlackChannelConfig(BaseModel):
    enabled: bool = False
    bot_token: str = ""
    """Slack Bot User OAuth Token (starts with xoxb-).
    Get from https://api.slack.com/apps -> OAuth & Permissions"""
    app_token: str = ""
    """Slack App-Level Token for Socket Mode (starts with xapp-).
    Get from https://api.slack.com/apps -> Basic Information -> App-Level Tokens"""
    allow_from: StringList = Field(default_factory=list)
    user_roles: StringDict = Field(default_factory=dict)
    """Maps Slack user IDs to permission roles.
    Env format: ``U123456:admin,U789012:viewer``"""
    reaction_feedback_enabled: bool = True
    """Enable reaction emoji feedback (👀 while processing, ✅ when done)."""
    reaction_processing: str = "eyes"
    """Emoji name for 'processing' reaction. Default: 'eyes' (👀)."""
    reaction_complete: str = "white_check_mark"
    """Emoji name for 'complete' reaction. Default: 'white_check_mark' (✅)."""
    streaming_enabled: bool = False
    """
    Stream AI responses token-by-token by posting one message then updating
    it in place via ``chat_update`` as new content arrives.

    .. warning::
        **Enabling this may degrade reliability.**
        Slack's ``chat_update`` API is Tier 3 (~50 req/min per app).  Rapid
        edits during generation can exhaust this quota, causing ``ratelimited``
        errors and stalled responses.  The 300 ms update throttle reduces —
        but does not eliminate — the risk, especially with multiple concurrent
        users sharing the same bot quota.

        Enable only when the live-typing UX is more important than reliability,
        and only in low-traffic environments.  Leave disabled (default) to
        receive the full response as a single message after generation completes.

    Env: ``LANGCLAW__CHANNELS__SLACK__STREAMING_ENABLED=true``
    """


class ChannelsConfig(BaseModel):
    telegram: TelegramChannelConfig = Field(default_factory=TelegramChannelConfig)
    discord: DiscordChannelConfig = Field(default_factory=DiscordChannelConfig)
    websocket: WebSocketChannelConfig = Field(default_factory=WebSocketChannelConfig)
    slack: SlackChannelConfig = Field(default_factory=SlackChannelConfig)


class AgentConfig(BaseModel):
    model: str = "anthropic:claude-sonnet-4-5-20250929"
    model_kwargs: dict[str, Any] = Field(default_factory=dict)
    rate_limit_rpm: int = 60
    banned_keywords: StringList = Field(default_factory=list)
    extra_skills: StringList = Field(default_factory=list)

    root_dir: str = Field(default_factory=lambda: str(_LANGCLAW_HOME))

    @property
    def workspace_dir(self) -> Path:
        return Path(self.root_dir).expanduser() / "workspace"

    @property
    def skills_source(self) -> str:
        return "/skills"

    @property
    def agents_md_source(self) -> str:
        return "/AGENTS.md"

    @property
    def memories_source(self) -> str:
        return "/memories"

    @property
    def skills_dir(self) -> Path:
        return self.workspace_dir / self.skills_source.lstrip("/")

    @property
    def agents_md_file(self) -> Path:
        return self.workspace_dir / self.agents_md_source.lstrip("/")

    @property
    def memories_dir(self) -> Path:
        return self.workspace_dir / self.memories_source.lstrip("/")


class SqliteCheckpointerConfig(BaseModel):
    db_path: str = Field(default_factory=lambda: str(_LANGCLAW_HOME / "state.db"))


class PostgresCheckpointerConfig(BaseModel):
    dsn: str = ""


class CheckpointerConfig(BaseModel):
    backend: Literal["sqlite", "postgres"] = "sqlite"
    sqlite: SqliteCheckpointerConfig = Field(default_factory=SqliteCheckpointerConfig)
    postgres: PostgresCheckpointerConfig = Field(default_factory=PostgresCheckpointerConfig)


class AsyncioBusConfig(BaseModel):
    pass


class RabbitMQBusConfig(BaseModel):
    amqp_url: str = "amqp://guest:guest@localhost/"
    queue_name: str = "langclaw.inbound"
    exchange_name: str = "langclaw"


class KafkaBusConfig(BaseModel):
    bootstrap_servers: str = "localhost:9092"
    topic: str = "langclaw.inbound"
    group_id: str = "langclaw"


class BusConfig(BaseModel):
    backend: Literal["asyncio", "rabbitmq", "kafka"] = "asyncio"
    asyncio: AsyncioBusConfig = Field(default_factory=AsyncioBusConfig)
    rabbitmq: RabbitMQBusConfig = Field(default_factory=RabbitMQBusConfig)
    kafka: KafkaBusConfig = Field(default_factory=KafkaBusConfig)


class CronSQLiteDataStoreConfig(BaseModel):
    db_path: str = Field(default_factory=lambda: str(_LANGCLAW_HOME / "cron.db"))


class CronPostgresDataStoreConfig(BaseModel):
    dsn: str = ""
    """SQLAlchemy async DSN, e.g.
    ``postgresql+asyncpg://user:pass@host/db``."""


class CronDataStoreConfig(BaseModel):
    """APScheduler data store — controls where job schedules are persisted.

    - ``"memory"``   — in-process only, lost on restart (default).
    - ``"sqlite"``   — persistent local file via SQLAlchemy + aiosqlite.
    - ``"postgres"`` — persistent shared DB via SQLAlchemy + asyncpg.
    """

    backend: Literal["memory", "sqlite", "postgres"] = "sqlite"
    sqlite: CronSQLiteDataStoreConfig = Field(default_factory=CronSQLiteDataStoreConfig)
    postgres: CronPostgresDataStoreConfig = Field(default_factory=CronPostgresDataStoreConfig)


class CronAsyncpgEventBrokerConfig(BaseModel):
    dsn: str = ""
    """asyncpg connection DSN, e.g.
    ``postgresql+asyncpg://user:pass@host/db``."""


class CronPsycopgEventBrokerConfig(BaseModel):
    dsn: str = ""
    """psycopg3 connection DSN, e.g.
    ``postgresql+psycopg://user:pass@host/db``."""


class CronRedisEventBrokerConfig(BaseModel):
    host: str = "localhost"
    port: int = 6379


class CronEventBrokerConfig(BaseModel):
    """APScheduler event broker — controls how scheduler events are fanned out.

    - ``"local"``   — in-process only, single-instance (default).
    - ``"asyncpg"`` — PostgreSQL pub/sub via asyncpg (multi-process).
    - ``"psycopg"`` — PostgreSQL pub/sub via psycopg3 (multi-process).
    - ``"redis"``   — Redis pub/sub (multi-process).
    """

    backend: Literal["local", "asyncpg", "psycopg", "redis"] = "local"
    asyncpg: CronAsyncpgEventBrokerConfig = Field(default_factory=CronAsyncpgEventBrokerConfig)
    psycopg: CronPsycopgEventBrokerConfig = Field(default_factory=CronPsycopgEventBrokerConfig)
    redis: CronRedisEventBrokerConfig = Field(default_factory=CronRedisEventBrokerConfig)


class CronConfig(BaseModel):
    enabled: bool = True
    timezone: str = "UTC"
    data_store: CronDataStoreConfig = Field(default_factory=CronDataStoreConfig)
    event_broker: CronEventBrokerConfig = Field(default_factory=CronEventBrokerConfig)


class HeartbeatConfig(BaseModel):
    enabled: bool = False
    interval_seconds: int = 60


class GmailConfig(BaseModel):
    """Gmail tool configuration (OAuth 2.0 Desktop flow)."""

    enabled: bool = False
    """Enable Gmail tools. Requires ``client_id`` and ``client_secret``."""

    client_id: str = ""
    """OAuth 2.0 client ID from the Google Cloud Console."""

    client_secret: str = ""
    """OAuth 2.0 client secret from the Google Cloud Console."""

    token_path: str = Field(default_factory=lambda: str(_LANGCLAW_HOME / "gmail_token.json"))
    """Path to the persisted OAuth refresh/access token file."""

    readonly: bool = True
    """When ``True`` only read/search tools are registered;
    when ``False`` send, draft, reply, and label tools are added as well."""


class RoleConfig(BaseModel):
    """Defines which tools a role may use."""

    tools: StringList = Field(default_factory=list)
    """Tool names this role is allowed to invoke.
    Use ``["*"]`` to grant access to all tools."""


class PermissionsConfig(BaseModel):
    """Global RBAC definitions.

    Role *definitions* (role name -> allowed tools) live here.
    User -> role *mappings* live per-channel alongside ``allow_from``.
    """

    enabled: bool = False
    """Enable per-user tool permission filtering."""

    default_role: str = "viewer"
    """Role assigned to users not listed in any channel's ``user_roles``."""

    roles: dict[str, RoleConfig] = Field(default_factory=dict)
    """Role name -> ``RoleConfig``. Define in ``config.json``::

        {"roles": {"admin": {"tools": ["*"]}, "viewer": {"tools": ["web_search"]}}}
    """


class ToolsConfig(BaseModel):
    """Configuration for built-in agent tools (web search, fetch, etc.)."""

    search_backend: Literal["brave", "tavily", "duckduckgo"] = "brave"
    """Search backend to use. One of ``"brave"``, ``"tavily"``, or ``"duckduckgo"``."""

    brave_api_key: str = ""
    """Brave Search API key. Required when search_backend = "brave".
    Obtain one at https://api.search.brave.com/app/dashboard"""

    tavily_api_key: str = ""
    """Tavily Search API key. Required when search_backend = "tavily".
    Obtain one at https://app.tavily.com"""

    gmail: GmailConfig = Field(default_factory=GmailConfig)
    """Gmail tool configuration. See ``GmailConfig``."""


# ---------------------------------------------------------------------------
# Root config
# ---------------------------------------------------------------------------


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge override into base; override wins on conflicts."""
    result = dict(base)
    for key, val in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(val, dict):
            result[key] = _deep_merge(result[key], val)
        else:
            result[key] = val
    return result


def _load_json_defaults() -> dict[str, Any]:
    """Load ~/.langclaw/config.json if it exists."""
    if _CONFIG_PATH.exists():
        try:
            return json.loads(_CONFIG_PATH.read_text())
        except Exception:
            return {}
    return {}


class LangclawConfig(BaseSettings):
    """
    Root configuration object. Merges JSON file + env vars.

    Environment variable format (double-underscore delimiter):
        LANGCLAW__AGENTS__MODEL=openai:gpt-4.1
        LANGCLAW__BUS__BACKEND=rabbitmq

    LLM provider keys use standard env vars (loaded from ``.env`` via
    ``load_dotenv``): ``ANTHROPIC_API_KEY``, ``OPENAI_API_KEY``, etc.
    """

    model_config = SettingsConfigDict(
        env_prefix="LANGCLAW__",
        env_nested_delimiter="__",
        env_file=".env",
        env_file_encoding="utf-8",
        env_ignore_empty=True,
        extra="ignore",
    )

    log_level: str = "WARNING"
    """
    Minimum log level for both stdlib ``logging`` and loguru.

    Common values: ``"DEBUG"``, ``"INFO"``, ``"WARNING"``, ``"ERROR"``.
    Override via env var: ``LANGCLAW__LOG_LEVEL=INFO``.
    """

    debug: bool = False
    """
    When ``True``, error responses sent back to the channel include a truncated
    traceback (up to 500 characters) to aid debugging.  Never enable in
    production — tracebacks may expose internal paths and library details.

    Override via env var: ``LANGCLAW__DEBUG=true``.
    """

    channels: ChannelsConfig = Field(default_factory=ChannelsConfig)
    agents: AgentConfig = Field(default_factory=AgentConfig)
    tools: ToolsConfig = Field(default_factory=ToolsConfig)
    permissions: PermissionsConfig = Field(default_factory=PermissionsConfig)
    checkpointer: CheckpointerConfig = Field(default_factory=CheckpointerConfig)
    bus: BusConfig = Field(default_factory=BusConfig)
    cron: CronConfig = Field(default_factory=CronConfig)
    heartbeat: HeartbeatConfig = Field(default_factory=HeartbeatConfig)

    @model_validator(mode="before")
    @classmethod
    def _merge_json_file(cls, values: Any) -> Any:
        """Merge JSON file as low-priority base; env vars win."""
        if isinstance(values, dict):
            json_data = _load_json_defaults()
            merged = _deep_merge(json_data, values)
            return merged
        return values

    @classmethod
    def settings_customise_sources(  # type: ignore[override]
        cls,
        settings_cls: type[BaseSettings],
        init_settings: object,
        env_settings: object,
        dotenv_settings: object,
        file_secret_settings: object,
    ) -> tuple:
        return (
            init_settings,
            _LangclawEnvSource(settings_cls),
            _LangclawDotEnvSource(
                settings_cls,
                env_file=".env",
                env_file_encoding="utf-8",
            ),
            file_secret_settings,
        )


def load_config() -> LangclawConfig:
    """Load and return the merged LangclawConfig.

    Also calls ``load_dotenv()`` so that standard provider env vars
    (``ANTHROPIC_API_KEY``, ``OPENAI_API_KEY``, etc.) from ``.env``
    are available in ``os.environ`` for ``init_chat_model``.
    """
    from dotenv import load_dotenv

    load_dotenv(override=False)
    return LangclawConfig()


def save_default_config() -> Path:
    """Write a default config.json to ~/.langclaw/config.json."""
    _CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    default = LangclawConfig()
    _CONFIG_PATH.write_text(default.model_dump_json(indent=2, exclude_none=False))
    return _CONFIG_PATH
