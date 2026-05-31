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


class MatrixChannelConfig(BaseModel):
    enabled: bool = False
    homeserver_url: str = ""
    """Full homeserver URL, e.g. ``"https://matrix.org"``."""
    user_id: str = ""
    """Bot user ID in fully-qualified form, e.g. ``"@mybot:matrix.org"``."""
    access_token: str = ""
    """Long-lived access token obtained via ``/login`` or
    ``matrix-commander --login``. Starts with ``syt_`` on Synapse."""
    device_id: str = ""
    """Device ID associated with ``access_token``. Required by matrix-nio
    for token-only authentication."""
    store_path: str = ""
    """Optional directory for nio's state store. Defaults to
    ``~/.langclaw/matrix_store`` when left empty."""
    auto_join_invites: bool = True
    """Automatically join rooms the bot is invited to. When ``allow_from``
    is non-empty the inviter must be on the allow-list."""
    allow_from: StringList = Field(default_factory=list)
    """Whitelist of Matrix user IDs, e.g. ``@alice:matrix.org,@bob:matrix.org``.
    Empty list means 'allow everyone'."""
    user_roles: StringDict = Field(default_factory=dict)
    """Maps Matrix user IDs to permission roles.
    Env format: ``@alice:matrix.org:admin,@bob:matrix.org:viewer``"""
    e2ee_enabled: bool = False
    """
    Reserved for future support of end-to-end-encrypted rooms.

    .. warning::
        Not implemented in this release. Starting the channel with
        ``e2ee_enabled=True`` raises an error at startup — the bot will
        not silently fall back to unencrypted mode, which would leak
        otherwise-encrypted messages on sync. Leave ``False`` for now;
        set it to opt in once E2EE support ships.
    """


class ChannelsConfig(BaseModel):
    telegram: TelegramChannelConfig = Field(default_factory=TelegramChannelConfig)
    discord: DiscordChannelConfig = Field(default_factory=DiscordChannelConfig)
    websocket: WebSocketChannelConfig = Field(default_factory=WebSocketChannelConfig)
    slack: SlackChannelConfig = Field(default_factory=SlackChannelConfig)
    matrix: MatrixChannelConfig = Field(default_factory=MatrixChannelConfig)


class AgentConfig(BaseModel):
    model: str = "anthropic:claude-sonnet-4-5-20250929"
    model_kwargs: dict[str, Any] = Field(default_factory=dict)
    rate_limit_rpm: int = 60
    banned_keywords: StringList = Field(default_factory=list)
    extra_skills: StringList = Field(default_factory=list)
    display_name: str = ""
    """Human-facing name for the default agent. Injected into the system prompt
    so the model knows its own name and shown in ``/agent`` listings. Empty
    string means no display name configured."""

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
    """Defines which tools (and interpreter subagents) a role may use."""

    tools: StringList = Field(default_factory=list)
    """Tool names this role is allowed to invoke.
    Use ``["*"]`` to grant access to all tools."""

    subagents: StringList = Field(default_factory=list)
    """Subagent types this role may invoke from an interpreter script via
    ``tools.task({subagent_type})``.  **Default-deny** — an empty list means
    the role cannot spawn any subagent from a script.  Use ``["*"]`` to allow
    every registered subagent.  This is a separate axis from ``tools``: a role
    with ``tools=["*"]`` still cannot reach subagents unless they are listed
    here."""

    workflows: StringList = Field(default_factory=list)
    """Workflow names this role may invoke — via the ``workflow_<name>`` tool,
    a ``/workflow`` command, cron, or (Phase 2) ``tools.workflow.<name>`` inside
    an interpreter script.  **Default-deny** like ``subagents`` — an empty list
    means the role may invoke no workflows.  Use ``["*"]`` to allow every
    registered workflow.  A third RBAC axis alongside ``tools`` and
    ``subagents``; their unification is tracked in issue #37."""


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


class InterpreterConfig(BaseModel):
    """Sandboxed code-interpreter (RLM) configuration.

    Opt-in and **off by default**.  When enabled, the agent gains an ``eval``
    tool backed by ``langchain-quickjs``'s ``CodeInterpreterMiddleware``: it
    writes a sandboxed JavaScript program that can loop, branch, retry, and
    fan out over a role-filtered allowlist of tools (Programmatic Tool Calling),
    including ``tools.task({subagent_type})`` to orchestrate registered
    subagents.

    The QuickJS sandbox has no filesystem, network, or shell access; the real
    trust boundary is the *exposed tools*, so the PTC allowlist defaults to a
    read-only set and mutating/egress tools require explicit ``allow_tools``
    opt-in.  Requires the ``interpreter`` extra::

        uv add 'langclaw[interpreter]'

    Env: ``LANGCLAW__INTERPRETER__ENABLED=true``
    """

    enabled: bool = False
    """Enable the ``eval`` code-interpreter tool. Off by default."""

    timeout: float = 5.0
    """Per-``eval`` wall-clock budget in seconds, **including awaited
    ``tools.task`` subagent runs**."""

    memory_limit: int = 64 * 1024 * 1024
    """Bytes the QuickJS heap may use per eval. Default 64 MiB."""

    max_ptc_calls: int = 256
    """Maximum total ``tools.*`` bridge calls allowed during one ``eval``.
    Bounds runaway fan-out / PTC-call DoS."""

    max_concurrent_subagents: int = 4
    """Advisory cap on concurrent ``tools.task`` fan-out width.

    .. note::
        Reserved for forward compatibility — the current ``langchain-quickjs``
        ``CodeInterpreterMiddleware`` does not expose a concurrency knob, so
        this value is documented but not yet enforced by the sandbox.  The
        effective bound today is ``max_ptc_calls`` plus ``timeout``."""

    max_result_chars: int = 4000
    """Caps what a script ``return`` (and captured ``console.log``) sends back
    to the agent turn."""

    snapshot_between_turns: bool = False
    """Persist REPL state across ``eval`` calls (snapshot after / restore before).

    Off by default: a fresh context per ``eval`` avoids cross-call ``const``/
    ``let`` redeclaration errors when the model retries with the same variable
    names. Enable only when a workflow genuinely needs to accumulate state
    across separate ``eval`` calls."""

    allow_tools: StringList = Field(default_factory=list)
    """Operator opt-in beyond the read-only default PTC allowlist.  Add
    mutating/egress tool names here to expose them inside scripts, or ``["*"]``
    to expose every available tool (subject to per-role RBAC)."""


class WorkflowsConfig(BaseModel):
    """Operator-authored Workflow primitive configuration (issue #38).

    Opt-in and **off by default**.  When enabled, workflows registered with
    ``@app.workflow()`` become invocable three ways: the LLM calls the
    ``workflow_<name>`` tool; an operator runs ``/workflow run <name>``; or a
    message with ``origin="workflow"`` (e.g. a cron-fired job) dispatches one
    through the gateway.  Each is typed, multi-step, and RBAC-gated by role.

    Steps run **in-process** against the live, role-filtered toolset (so
    tool-level RBAC applies).  Bus dispatch runs a *whole workflow* as one bus
    message; full bus → gateway re-entry per *step* (inheriting rate limiting,
    channel context, and per-step checkpointing) is not yet wired.

    Env: ``LANGCLAW__WORKFLOWS__ENABLED=true``
    """

    enabled: bool = False
    """Enable the Workflow primitive.  Off by default — registering workflows
    is inert until this is set."""

    max_concurrent_runs: int = 16
    """Global ceiling on simultaneously-running workflow runs across the host,
    regardless of any single workflow's own budget."""

    max_steps_per_run: int = 1000
    """Hard backstop on total steps a single run may execute.  Guards against a
    runaway loop in an operator-authored body."""

    max_depth: int = 2
    """Maximum nesting depth — how many levels a workflow may invoke other
    workflows.  Bounds recursive fan-out."""

    durable_steps: bool = False
    """When ``True``, completed workflow step results are persisted to a
    LangGraph ``BaseStore`` (a sibling SQLite file or the Postgres DSN, matching
    the checkpointer backend) instead of an in-process dict, so they survive a
    process restart.  Off by default.

    NOTE: this only *persists* step results — it does not re-run anything on its
    own.  Set ``resume_on_startup`` for that.  The store currently has no TTL or
    pruning, so it grows unbounded; keep an eye on it for long-lived deployments."""

    resume_on_startup: bool = False
    """When ``True``, workflow runs left incomplete by a previous process (a crash
    / kill) are re-run on startup from the run journal: completed steps replay
    from the durable step store and only the unfinished tail executes.  Off by
    default.  Requires ``durable_steps`` (the step store + run journal share one
    ``BaseStore``); without it, enabling this logs a warning and does nothing.

    Caveats developers should know before relying on it:

    - **Python-mode workflows only.**  Runs whose spec is no longer registered, or
      whose ``mode`` is ``llm_authored``, are skipped (logged), not resumed.
    - **Crash vs. clean failure.**  A killed process leaves a run ``running`` and
      so resumable; a workflow that raised a normal exception is marked ``failed``
      and is *not* retried (avoids looping on a deterministic bug).
    - **No step-result invalidation.**  Resume matches cached steps by a
      deterministic ``step_id`` (``<phase>#<seq>``).  Editing a workflow body
      between crash and restart can shift those IDs and replay stale results — bump
      the workflow name or clear the store after changing a body you may resume.
    - **Resumes under the default agent's permissions.**  The resume step executor
      is built from the default agent's role-filtered toolset, not the original
      invoker's role/named-agent context — a resumed run may see a different
      toolset than the run that crashed.
    - **Blocking at startup.**  Incomplete runs are replayed sequentially before
      the gateway begins serving traffic, so a slow or hanging resumed run delays
      startup.
    - **One attempt.**  A run that raises again during resume is marked ``failed``
      and not retried — even if the cause was transient."""


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
    interpreter: InterpreterConfig = Field(default_factory=InterpreterConfig)
    workflows: WorkflowsConfig = Field(default_factory=WorkflowsConfig)
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
