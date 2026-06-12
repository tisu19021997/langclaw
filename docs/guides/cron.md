# Scheduled Jobs

Cron jobs publish an `InboundMessage` to the same bus as user messages — they flow through the identical middleware pipeline and produce channel output.

```bash
LANGCLAW__CRON__ENABLED=true
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

## Internals

Cron is backed by APScheduler v4. Jobs are persisted to the checkpointer so they survive restarts. Each fire stamps `agent_name` into `InboundMessage.metadata` (from the context at schedule time), so jobs always run against the right agent even if the user switches later.
