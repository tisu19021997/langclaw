# Config

All config is read from environment variables with the `LANGCLAW__` prefix and
`__` as the nesting delimiter (e.g. `LANGCLAW__BUS__BACKEND=rabbitmq`). Provider
API keys are the exception — they use the provider's own plain env var
(`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, …), not a `LANGCLAW__` key.

## Common environment variables

| Env var | Default | Notes |
|---|---|---|
| `LANGCLAW__AGENTS__MODEL` | `anthropic:claude-sonnet-4-5-20250929` | `provider:model` spec |
| `LANGCLAW__AGENTS__RATE_LIMIT_RPM` | `60` | requests/min |
| `LANGCLAW__AGENTS__BACKEND__BACKEND` | `local_shell` | `local_shell` (file tools **+ unsandboxed `execute`**), `filesystem` (file tools only), `state`, `store` |
| `LANGCLAW__CHECKPOINTER__BACKEND` | `sqlite` | `sqlite` \| `postgres` |
| `LANGCLAW__CHECKPOINTER__SQLITE__DB_PATH` | `~/.langclaw/state.db` | |
| `LANGCLAW__CHECKPOINTER__POSTGRES__DSN` | `""` | required for `postgres` (needs `langclaw[postgres]`) |
| `LANGCLAW__BUS__BACKEND` | `asyncio` | `asyncio` \| `rabbitmq` \| `kafka` |
| `LANGCLAW__BUS__RABBITMQ__AMQP_URL` | `amqp://guest:guest@localhost/` | needs `langclaw[rabbitmq]` |
| `LANGCLAW__BUS__RABBITMQ__QUEUE_NAME` | `langclaw.inbound` | |
| `LANGCLAW__BUS__KAFKA__BOOTSTRAP_SERVERS` | `localhost:9092` | needs `langclaw[kafka]` |
| `LANGCLAW__BUS__KAFKA__TOPIC` | `langclaw.inbound` | |
| `LANGCLAW__PERMISSIONS__ENABLED` | `false` | turn on RBAC |
| `LANGCLAW__PERMISSIONS__DEFAULT_ROLE` | `viewer` | role for unlisted users |
| `LANGCLAW__CRON__ENABLED` | `false` | sqlite store needs `sqlalchemy`+`aiosqlite` |
| `LANGCLAW__CRON__DATA_STORE__BACKEND` | `sqlite` | separate from the checkpointer |
| `LANGCLAW__WORKFLOWS__ENABLED` | `false` | |
| `LANGCLAW__WORKFLOWS__DURABLE_STEPS` | `false` | memoize completed steps |
| `LANGCLAW__WORKFLOWS__RESUME_ON_STARTUP` | `false` | re-drive interrupted runs (requires `DURABLE_STEPS`) |
| `LANGCLAW__INTERPRETER__ENABLED` | `false` | sandboxed `eval` tool (needs `langclaw[interpreter]`) |
| `LANGCLAW__CHANNELS__TELEGRAM__ENABLED` | `false` | |
| `LANGCLAW__CHANNELS__TELEGRAM__TOKEN` | `""` | bot token (needs `langclaw[telegram]`) |
| `LANGCLAW__CHANNELS__TELEGRAM__USER_ROLES` | `{}` | `id:role` comma list, e.g. `123:admin,@alice:analyst` |
| `LANGCLAW__CHANNELS__WEBSOCKET__ENABLED` | `false` | bundled, no extra |
| `LANGCLAW__CHANNELS__WEBSOCKET__PORT` | `18789` | |

The same `enabled` / `token` / `user_roles` pattern applies to the Discord, Slack,
and Matrix channels. Full field-level docs (types and defaults) are rendered below.

## Top-level config

::: langclaw.LangclawConfig

::: langclaw.load_config

## Sub-schemas

::: langclaw.config.schema.AgentConfig
::: langclaw.config.schema.BackendConfig
::: langclaw.config.schema.ChannelsConfig
::: langclaw.config.schema.TelegramChannelConfig
::: langclaw.config.schema.DiscordChannelConfig
::: langclaw.config.schema.SlackChannelConfig
::: langclaw.config.schema.MatrixChannelConfig
::: langclaw.config.schema.WebSocketChannelConfig
::: langclaw.config.schema.CheckpointerConfig
::: langclaw.config.schema.BusConfig
::: langclaw.config.schema.PermissionsConfig
::: langclaw.config.schema.RoleConfig
::: langclaw.config.schema.CronConfig
::: langclaw.config.schema.WorkflowsConfig
::: langclaw.config.schema.InterpreterConfig
::: langclaw.config.schema.ToolsConfig
