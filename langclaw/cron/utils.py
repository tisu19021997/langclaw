from __future__ import annotations

from langclaw.cron.scheduler import CronJob


def format_cron_job_detail(job: CronJob) -> str:
    """Human-readable full detail for a single job (CLI / tool view)."""
    agent_label = job.agent_name or "(default agent)"
    return (
        f"Job ID: {job.id}\n"
        f"Schedule: {job.schedule}\n"
        f"Name: {job.name!r}\n"
        f"Context ID: {job.context_id}\n"
        f"Agent: {agent_label}\n"
        "Message:\n"
        f"{job.message}"
    )


def make_cron_context_id() -> str:
    import uuid

    return f"cron:task:{uuid.uuid4()}"


def is_cron_context_id(context_id: str) -> bool:
    return context_id.startswith("cron:task:")
