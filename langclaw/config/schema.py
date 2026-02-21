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

    def decode_complex_value(
        self, field_name: str, field_info: object, value: str
    ) -> object:
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

# ---------------------------------------------------------------------------
# Sub-schemas
# ---------------------------------------------------------------------------


class ProviderConfig(BaseModel):
    api_key: str = ""
    api_base: str = ""


class AzureOpenAIProviderConfig(BaseModel):
    api_key: str = ""
    api_base: str = ""
    """Azure endpoint URL, e.g. https://<resource>.openai.azure.com/"""
    api_version: str = "2025-01-01-preview"
    """Azure OpenAI API version string. See Azure docs for supported values."""


class ProvidersConfig(BaseModel):
    model_config = {"extra": "allow"}

    openai: ProviderConfig = Field(default_factory=ProviderConfig)
    anthropic: ProviderConfig = Field(default_factory=ProviderConfig)
    openrouter: ProviderConfig = Field(default_factory=ProviderConfig)
    google: ProviderConfig = Field(default_factory=ProviderConfig)
    azure_openai: AzureOpenAIProviderConfig = Field(
        default_factory=AzureOpenAIProviderConfig
    )


class TelegramChannelConfig(BaseModel):
    enabled: bool = False
    token: str = ""
    allow_from: StringList = Field(default_factory=list)


class DiscordChannelConfig(BaseModel):
    enabled: bool = False
    token: str = ""
    allow_from: StringList = Field(default_factory=list)


class SlackChannelConfig(BaseModel):
    enabled: bool = False
    bot_token: str = ""
    app_token: str = ""
    allow_from: StringList = Field(default_factory=list)


class ChannelsConfig(BaseModel):
    telegram: TelegramChannelConfig = Field(default_factory=TelegramChannelConfig)
    discord: DiscordChannelConfig = Field(default_factory=DiscordChannelConfig)
    slack: SlackChannelConfig = Field(default_factory=SlackChannelConfig)


class AgentConfig(BaseModel):
    model: str = "anthropic:claude-sonnet-4-5-20250929"
    system_prompt: str = "You are a helpful AI assistant."
    rate_limit_rpm: int = 60
    banned_keywords: StringList = Field(default_factory=list)
    extra_skills: StringList = Field(default_factory=list)


class SqliteCheckpointerConfig(BaseModel):
    db_path: str = "~/.langclaw/state.db"


class PostgresCheckpointerConfig(BaseModel):
    dsn: str = ""


class CheckpointerConfig(BaseModel):
    backend: Literal["sqlite", "postgres"] = "sqlite"
    sqlite: SqliteCheckpointerConfig = Field(default_factory=SqliteCheckpointerConfig)
    postgres: PostgresCheckpointerConfig = Field(
        default_factory=PostgresCheckpointerConfig
    )


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


class CronConfig(BaseModel):
    enabled: bool = True
    timezone: str = "UTC"


class HeartbeatConfig(BaseModel):
    enabled: bool = False
    interval_seconds: int = 60


# ---------------------------------------------------------------------------
# Root config
# ---------------------------------------------------------------------------


_LANGCLAW_HOME = Path.home() / ".langclaw"
_CONFIG_PATH = _LANGCLAW_HOME / "config.json"


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
        LANGCLAW__PROVIDERS__ANTHROPIC__API_KEY=sk-...
    """

    model_config = SettingsConfigDict(
        env_prefix="LANGCLAW__",
        env_nested_delimiter="__",
        env_file=".env",
        env_file_encoding="utf-8",
        env_ignore_empty=True,
        extra="ignore",
    )

    providers: ProvidersConfig = Field(default_factory=ProvidersConfig)
    channels: ChannelsConfig = Field(default_factory=ChannelsConfig)
    agents: AgentConfig = Field(default_factory=AgentConfig)
    checkpointer: CheckpointerConfig = Field(default_factory=CheckpointerConfig)
    bus: BusConfig = Field(default_factory=BusConfig)
    cron: CronConfig = Field(default_factory=CronConfig)
    heartbeat: HeartbeatConfig = Field(default_factory=HeartbeatConfig)

    root_dir: str = Field(default_factory=lambda: str(_LANGCLAW_HOME))

    @property
    def workspace_dir(self) -> Path:
        return Path(self.root_dir).expanduser() / "workspace"

    @property
    def skills_dir(self) -> Path:
        return self.workspace_dir / "skills"

    @property
    def agents_md_file(self) -> Path:
        return self.workspace_dir / "AGENTS.md"

    @property
    def memories_dir(self) -> Path:
        return self.workspace_dir / "memories"

    @model_validator(mode="before")
    @classmethod
    def _merge_json_file(cls, values: Any) -> Any:
        """Merge JSON file as low-priority base; env vars win."""
        if isinstance(values, dict):
            json_data = _load_json_defaults()
            merged = {**json_data, **values}
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
    """Load and return the merged LangclawConfig."""
    return LangclawConfig()


def save_default_config() -> Path:
    """Write a default config.json to ~/.langclaw/config.json."""
    _CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    default = LangclawConfig()
    _CONFIG_PATH.write_text(default.model_dump_json(indent=2, exclude_none=False))
    return _CONFIG_PATH


# Global config instance
config: LangclawConfig = load_config()
