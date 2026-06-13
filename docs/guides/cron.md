# Scheduled Jobs

Cron jobs publish an `InboundMessage` to the same bus as user messages — they flow through the identical middleware pipeline and produce channel output.

Cron is **off by default**. Enabling it with the default SQLite data store also requires `sqlalchemy` and `aiosqlite`:

```bash
LANGCLAW__CRON__ENABLED=true
uv add sqlalchemy aiosqlite   # needed for the default sqlite cron data store
```

## Schedule via the agent

Once cron is enabled, the agent gets a `cron` tool. Users schedule jobs in plain language:

```
Schedule a daily HN digest every morning at 8am
Schedule the landscape workflow for "agent frameworks" every Monday
```

The agent translates this into a cron job via the `cron` tool.

## Schedule a saved workflow

Saved workflows scheduled via cron run their frozen body **without any LLM call** — deterministic, zero LLM cost:

```
Schedule workflow 'digest' every day at 9am
```

On fire, the workflow runs verbatim. If the `.js` file is deleted, the job self-disarms.

## Manage jobs

```
/cron list          → all scheduled jobs
/cron cancel <id>   → remove a job
```

## Where output goes

A cron job has no live conversation, so it captures its delivery target **at schedule time**: when the agent's `cron` tool creates a job, it reads the current `LangclawContext` and stamps the `channel`, `user_id`, and `chat_id` into the job. On every fire the result is delivered back to that same channel/chat. Schedule a digest from your Telegram chat and it arrives in that chat.

## Internals

Cron is backed by APScheduler v4. Jobs are persisted to APScheduler's **own data store** (`LANGCLAW__CRON__DATA_STORE__BACKEND`, `sqlite` by default — a separate store from the conversation checkpointer), so they survive restarts. Each fire also stamps `agent_name` into `InboundMessage.metadata` (from the context at schedule time), so jobs always run against the right agent even if the user switches later.
