# Deployment

How to take a langclaw app from `python app.py` on your laptop to a running
service. This page is honest about what's wired and what you have to provide
yourself.

## Process model

`app.run()` is **blocking** and runs a **single asyncio event loop in one
process**. It wires the bus, checkpointer, channels, cron, and agent, then serves
until cancelled. There is no built-in worker pool or forking — concurrency is
cooperative within the loop. To scale beyond one process, see [Scaling](#scaling).

Startup and shutdown hooks let you manage external resources:

```python
@app.on_startup
async def open_pool():
    ...

@app.on_shutdown
async def close_pool():
    ...
```

`on_startup` hooks run **before** the bus and checkpointer are started — use them
to open your own resources (DB pools, HTTP clients), not to publish to the bus. On
normal cancellation (Ctrl-C / `SIGINT`), langclaw unwinds the bus, checkpointer,
and workflow stores via an `AsyncExitStack` and then runs your `on_shutdown` hooks.

!!! warning "Honest limit: SIGTERM is not trapped"
    langclaw does not install a `SIGTERM` handler. Orchestrators (Docker, k8s)
    send `SIGTERM` on stop — by default that terminates the process without
    running the graceful unwind. If you need clean shutdown under an
    orchestrator, install your own handler that raises `KeyboardInterrupt` /
    cancels the loop, and give the platform a `terminationGracePeriod` long
    enough for in-flight turns to finish.

## Dev → prod backends

Everything below swaps via environment variables — no code changes. See the
[config reference](../reference/config.md) for the full key list.

| Concern | Dev default | Production |
|---|---|---|
| Message bus | `asyncio` (in-process) | `rabbitmq` or `kafka` |
| Conversation state | `sqlite` | `postgres` |
| Cron job store | `sqlite` | `postgres` |
| Workflow durable-step store | (off) | `postgres` + `LANGCLAW__WORKFLOWS__DURABLE_STEPS=true` |

```bash
# Production backends
LANGCLAW__BUS__BACKEND=rabbitmq
LANGCLAW__BUS__RABBITMQ__AMQP_URL=amqp://user:pass@rabbit:5672/
LANGCLAW__CHECKPOINTER__BACKEND=postgres
LANGCLAW__CHECKPOINTER__POSTGRES__DSN=postgresql://user:pass@db:5432/langclaw
LANGCLAW__CRON__DATA_STORE__BACKEND=postgres
```

Install the matching extras: `uv add "langclaw[rabbitmq,postgres]"`.

## Scaling

- **Default (`asyncio` bus):** single process only. The bus is an in-memory
  queue — you cannot run a second instance against it.
- **`rabbitmq` bus:** the inbound queue is **durable** and consumed with
  `prefetch=1`, i.e. **competing consumers**. Run N gateway processes against the
  same queue and inbound messages are load-balanced across them — one message goes
  to exactly one instance. Delivery is **at-least-once** (an instance that crashes
  mid-turn leaves the message un-acked, so it's redelivered). A shared `postgres`
  checkpointer keeps conversation state consistent across all instances.

!!! warning "Honest limit: don't duplicate a polling channel"
    Competing consumers load-balance *the bus*, not a channel's upstream
    connection. A channel that owns a single long-poll connection (e.g. Telegram
    `getUpdates`) must run in **exactly one** process — two instances polling the
    same bot conflict. Scale-out is clean when many producers fan into the bus and
    multiple workers drain it; it is not "just run N identical copies" for
    single-connection polling channels. Webhook-style ingestion behind a load
    balancer avoids this.

## Containerizing

No Dockerfile ships with langclaw. A minimal one for your own `app.py`:

```dockerfile title="Dockerfile"
FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev
COPY . .
# Run YOUR app (tools/subagents/commands you registered), not `langclaw gateway`,
# which only runs the config-only built-in agent.
CMD ["uv", "run", "python", "app.py"]
```

```yaml title="compose.yaml"
services:
  app:
    build: .
    env_file: .env          # LANGCLAW__* + provider keys
    depends_on: [db, rabbit]
  db:
    image: postgres:16
    environment: { POSTGRES_PASSWORD: pass, POSTGRES_DB: langclaw }
  rabbit:
    image: rabbitmq:3-management
```

Configure entirely through environment variables in the container — `langclaw
init` (which writes `~/.langclaw/` and installs deps) is a local convenience, not
a deploy step.

## Security checklist

- **Drop the host shell in prod.** The default `local_shell` backend gives the
  agent an **unsandboxed `execute`** tool that runs host commands. Set
  `LANGCLAW__AGENTS__BACKEND__BACKEND=filesystem` to keep file tools without
  `execute`, and/or run inside a locked-down container. See the
  [Architecture guide](architecture.md).
- **Enable RBAC.** It's off by default — set `LANGCLAW__PERMISSIONS__ENABLED=true`
  and assign roles. See the [RBAC guide](rbac.md).
- **Don't expose the WebSocket channel unauthenticated** to the public internet —
  it has no built-in auth; put it behind your own auth/proxy.
- **Keep provider keys and tokens in the environment / a secrets manager**, never
  baked into the image.

## Observability

!!! note "Honest limit: no built-in HTTP health endpoint"
    langclaw exposes no `/healthz`. `langclaw status` checks configuration (keys,
    channels, bus), not a live process. For an orchestrator liveness probe, do a
    TCP check on the WebSocket port, or add a tiny health route via a custom
    channel / `on_startup` hook.

- **Logs:** `app.run()` writes INFO+ to `<workspace>/logs/{date}.log` (daily
  rotation, 30-day retention) in addition to stderr.
- **End-to-end checks:** drive a real turn through the pipeline with the
  [probe harness](probe.md) (`langclaw probe`) against a running gateway.
