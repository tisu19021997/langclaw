"""
CronManager — scheduled task engine backed by APScheduler v4.

Jobs publish InboundMessages to the bus on fire, so they flow through the
same agent pipeline as channel messages. Cron messages have ``origin="cron"``
to identify their source.

Persistence: APScheduler v4 jobs are held in-memory by default. For
production persistence configure a SQLAlchemy data store (PostgreSQL, etc.)
via APScheduler's built-in facilities.

Serialisation note
------------------
When using a persistent data store (SQLAlchemy), APScheduler serialises the
job function and its kwargs via pickle so they survive across restarts.
Bound methods (``self._fire``) cannot be pickled when the instance holds
un-picklable objects such as ``asyncio.Queue``.

To work around this, the fire callback is a **module-level function**
(``_fire_job``) that APScheduler serialises as a dotted import path.  The
live bus reference is never pickled — instead each ``CronManager`` registers
itself in a module-level ``_MANAGERS`` dict under a plain string ID, and
``_fire_job`` looks the manager up at fire time.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING

from langclaw.bus.base import BaseMessageBus, InboundMessage

if TYPE_CHECKING:
    from apscheduler import AsyncScheduler
    from apscheduler._structures import Schedule
    from apscheduler.abc import DataStore, EventBroker

logger = logging.getLogger(__name__)


def _wrap_cron_runtime_prompt(task_message: str) -> str:
    """Return a compact execution wrapper for cron-fired prompts."""
    return (
        "Scheduled run. Execute now.\n"
        "Rules: no follow-up questions unless blocked by missing access/credentials; "
        "use reasonable defaults; use tools if needed; return only the final user-facing "
        "result in the requested format.\n\n"
        f"Task:\n{task_message}"
    )


# ---------------------------------------------------------------------------
# Module-level manager registry
# ---------------------------------------------------------------------------

# Maps manager_id → CronManager so that the module-level _fire_job function
# can reach the live bus without holding an un-picklable reference.
_MANAGERS: dict[str, CronManager] = {}


# ---------------------------------------------------------------------------
# Module-level fire callback (picklable by APScheduler)
# ---------------------------------------------------------------------------


async def _fire_job(
    manager_id: str,
    message: str,
    channel: str,
    user_id: str,
    context_id: str,
    chat_id: str,
    job_name: str,
    schedule: str = "",
    user_role: str = "",
) -> None:
    """APScheduler job function — must stay at module level to be picklable.

    All parameters are plain strings so APScheduler can pickle them safely
    regardless of which data store backend is configured.

    ``schedule`` is stored for introspection (``cron list``) and is not used
    during execution. It defaults to ``""`` so old persisted jobs without the
    field continue to fire without error.

    ``user_role`` is the RBAC role resolved at schedule time and carried
    through so cron-fired messages run with the same permissions as the
    user who created the job.  Defaults to ``""`` for backward compat
    with jobs persisted before this field was added.
    """
    manager = _MANAGERS.get(manager_id)
    if manager is None and _MANAGERS:
        fallback_id, manager = next(iter(_MANAGERS.items()))
        logger.warning(
            f"CronManager '{manager_id}' not in registry "
            f"(stale persisted job?); falling back to '{fallback_id}'."
        )
    if manager is None:
        logger.error(
            f"CronManager '{manager_id}' not found in registry — dropping fired job '{job_name}'."
        )
        return
    logger.debug(f"Cron job '{job_name}' fired → publishing to bus.")
    metadata: dict[str, str] = {
        "job_name": job_name,
    }
    if user_role:
        metadata["user_role"] = user_role
    await manager._bus.publish(
        InboundMessage(
            channel=channel,
            user_id=user_id,
            context_id=context_id,
            content=_wrap_cron_runtime_prompt(message),
            chat_id=chat_id,
            origin="cron",
            metadata=metadata,
        )
    )


# ---------------------------------------------------------------------------
# Job descriptor
# ---------------------------------------------------------------------------


@dataclass
class CronJob:
    id: str
    name: str
    message: str
    channel: str
    user_id: str
    context_id: str
    chat_id: str
    schedule: str
    """Either a cron expression (``"0 9 * * *"``) or ``"every:<seconds>"``."""


def _trigger_to_str(trigger: object) -> str:
    """Return a compact human-readable string for an APScheduler trigger.

    For ``CronTrigger`` this produces a standard 5-field cron expression
    (``minute hour day month day_of_week``).  For ``IntervalTrigger`` it
    produces ``every:<seconds>s``.  Unknown trigger types fall back to their
    repr.

    ``_fields`` is used instead of the public attrs (``trigger.minute`` etc.)
    because ``CronTrigger.__setstate__`` only restores ``_fields``; the public
    attrs are stale after deserialisation from a persistent store.
    """
    try:
        from apscheduler.triggers.cron import CronTrigger
        from apscheduler.triggers.interval import IntervalTrigger
    except ImportError:
        return str(trigger)

    if isinstance(trigger, CronTrigger):
        # FIELDS_MAP order: year(0) month(1) day(2) week(3) dow(4) hour(5) minute(6) second(7)
        # 5-field cron expression: minute hour day month day_of_week
        fields = [str(f) for f in trigger._fields]
        if len(fields) >= 7:
            return f"{fields[6]} {fields[5]} {fields[2]} {fields[1]} {fields[4]}"
        return str(trigger)

    if isinstance(trigger, IntervalTrigger):
        total = int(trigger._interval.total_seconds())
        return f"every:{total}s"

    return str(trigger)


def _schedule_to_cronjob(schedule: Schedule) -> CronJob | None:
    """Reconstruct a ``CronJob`` from an APScheduler ``Schedule``.

    Returns ``None`` if the schedule's kwargs cannot be decoded (e.g. it was
    created by a different system or its pickle is corrupt).
    """
    kwargs = schedule.kwargs
    if not isinstance(kwargs, dict):
        return None
    try:
        return CronJob(
            id=schedule.id,
            name=kwargs.get("job_name", ""),
            message=kwargs.get("message", ""),
            channel=kwargs.get("channel", ""),
            user_id=kwargs.get("user_id", ""),
            context_id=kwargs.get("context_id", "default"),
            chat_id=kwargs.get("chat_id", ""),
            schedule=kwargs.get("schedule") or _trigger_to_str(schedule.trigger),
        )
    except Exception:
        logger.debug("Could not reconstruct CronJob from schedule %s", schedule.id, exc_info=True)
        return None


# ---------------------------------------------------------------------------
# Manager
# ---------------------------------------------------------------------------


class CronManager:
    """
    Manages scheduled jobs that trigger agent invocations.

    Natural-language scheduling is handled by the agent itself via the
    ``cron`` tool — the LLM parses "every morning at 9" and calls
    this manager with the resulting cron expression.

    Args:
        bus:          MessageBus to publish triggered messages into.
        timezone:     Default timezone for cron expressions
                      (e.g. ``"Europe/Amsterdam"``).
        data_store:   APScheduler ``DataStore`` instance. Defaults to
                      ``MemoryDataStore`` (in-process, lost on restart).
                      Pass a ``SQLAlchemyDataStore`` for persistence.
        event_broker: APScheduler ``EventBroker`` instance. Defaults to
                      ``LocalEventBroker`` (single-process). Pass an
                      ``AsyncpgEventBroker``, ``PsycopgEventBroker``, or
                      ``RedisEventBroker`` for multi-process coordination.
    """

    _DEFAULT_MANAGER_ID = "default"

    def __init__(
        self,
        bus: BaseMessageBus,
        timezone: str = "UTC",
        data_store: DataStore | None = None,
        event_broker: EventBroker | None = None,
    ) -> None:
        self._bus = bus
        self._timezone = timezone
        self._data_store = data_store
        self._event_broker = event_broker
        self._manager_id: str = self._DEFAULT_MANAGER_ID
        self._scheduler: AsyncScheduler | None = None

    async def start(self) -> None:
        """Start the APScheduler AsyncScheduler and register in the registry."""
        try:
            from apscheduler import AsyncScheduler
            from apscheduler.datastores.memory import MemoryDataStore
            from apscheduler.eventbrokers.local import LocalEventBroker
        except ImportError as exc:
            raise ImportError(
                "CronManager requires apscheduler>=4. Install with: uv add 'apscheduler>=4'"
            ) from exc

        self._scheduler = AsyncScheduler(
            data_store=self._data_store or MemoryDataStore(),
            event_broker=self._event_broker or LocalEventBroker(),
        )
        await self._scheduler.__aenter__()
        await self._scheduler.start_in_background()
        _MANAGERS[self._manager_id] = self
        logger.info(f"CronManager started (id={self._manager_id}, timezone={self._timezone}).")

    async def stop(self) -> None:
        """Stop the scheduler and deregister from the registry."""
        _MANAGERS.pop(self._manager_id, None)
        if self._scheduler is not None:
            await self._scheduler.__aexit__(None, None, None)
            self._scheduler = None

    async def add_job(
        self,
        name: str,
        message: str,
        channel: str,
        user_id: str,
        context_id: str = "default",
        chat_id: str = "",
        cron_expr: str | None = None,
        every_seconds: int | None = None,
        user_role: str = "",
    ) -> str:
        """
        Schedule a job that fires a message into the agent pipeline.

        Args:
            name:          Human-readable label.
            message:       Text content to send as an InboundMessage.
            channel:       Target channel name (e.g. ``"telegram"``).
            user_id:       Target user ID on the channel.
            context_id:    Session key for thread mapping.
            chat_id:       Delivery address on the channel (falls back to user_id).
            cron_expr:     Standard 5-field cron expression (``"0 9 * * *"``).
            every_seconds: Interval in seconds (alternative to cron_expr).
            user_role:     RBAC role of the scheduling user (persisted so the
                           fired job runs with the same permissions).

        Returns:
            A stable job ID string.
        """
        if self._scheduler is None:
            raise RuntimeError("CronManager not started — call start() first.")
        if cron_expr is None and every_seconds is None:
            raise ValueError("Provide either cron_expr or every_seconds.")

        try:
            from apscheduler.triggers.cron import CronTrigger
            from apscheduler.triggers.interval import IntervalTrigger
        except ImportError as exc:
            raise ImportError("apscheduler>=4 required") from exc

        job_id = str(uuid.uuid4())
        trigger = (
            CronTrigger.from_crontab(cron_expr, timezone=self._timezone)
            if cron_expr
            else IntervalTrigger(seconds=every_seconds)
        )

        job = CronJob(
            id=job_id,
            name=name,
            message=message,
            channel=channel,
            user_id=user_id,
            context_id=context_id,
            chat_id=chat_id,
            schedule=cron_expr or f"every:{every_seconds}s",
        )
        # All kwargs are plain strings — safe to pickle for persistent stores.
        # The bus is looked up from _MANAGERS at fire time via manager_id.
        # ``schedule`` is stored so CLI / list_jobs can reconstruct CronJob
        # without needing to introspect the trigger object.
        fire_kwargs: dict[str, str] = {
            "manager_id": self._manager_id,
            "message": message,
            "channel": channel,
            "user_id": user_id,
            "context_id": context_id,
            "chat_id": chat_id,
            "job_name": name,
            "schedule": job.schedule,
        }
        if user_role:
            fire_kwargs["user_role"] = user_role
        await self._scheduler.add_schedule(
            _fire_job,
            trigger,
            id=job_id,
            kwargs=fire_kwargs,
        )
        logger.info(f"Cron job '{name}' scheduled (id={job_id}, schedule={job.schedule}).")
        return job_id

    async def remove_job(
        self,
        job_id: str,
        *,
        channel: str | None = None,
        user_id: str | None = None,
    ) -> bool:
        """Remove a scheduled job. Returns True if it existed.

        When *channel* and *user_id* are provided, the job is only removed
        if it belongs to that owner. This prevents users from deleting
        jobs created by others.

        Falls back to querying the data store for jobs that were added by the
        CLI and are not present in the in-memory ownership cache.
        """
        if self._scheduler is None:
            return False
        try:
            schedules = await self._scheduler.data_store.get_schedules({job_id})
        except Exception:
            return False
        if not schedules:
            return False
        job = _schedule_to_cronjob(schedules[0])
        if job is None:
            return False
        if channel is not None and job.channel != channel:
            return False
        if user_id is not None and job.user_id != user_id:
            return False
        try:
            await self._scheduler.remove_schedule(job_id)
            return True
        except Exception:
            return False

    async def list_jobs(
        self,
        *,
        channel: str | None = None,
        user_id: str | None = None,
    ) -> list[CronJob]:
        """Return all cron jobs, queried from the APScheduler data store.

        Queries the data store directly so the result is always up-to-date,
        including schedules added by the CLI while the gateway was running.
        Falls back to the in-memory cache if the data store is unavailable.

        When *channel* and *user_id* are both provided, only jobs matching
        that owner are returned — preventing users from seeing jobs
        scheduled by others.
        """
        if self._scheduler is None:
            return []
        try:
            schedules = await self._scheduler.data_store.get_schedules()
        except Exception:
            logger.warning("Failed to query cron data store for list_jobs.")
            return []
        all_jobs = [job for s in schedules if (job := _schedule_to_cronjob(s)) is not None]
        if channel is not None:
            all_jobs = [j for j in all_jobs if j.channel == channel]
        if user_id is not None:
            all_jobs = [j for j in all_jobs if j.user_id == user_id]
        return all_jobs
