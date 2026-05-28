"""
Tests for cron-triggered workflows.

A scheduled job can target a workflow instead of the agent: it stamps
``metadata["workflow_name"]`` at fire time (mirroring how ``agent_name`` is
threaded), so the fired ``InboundMessage`` flows through the same dispatch
branch in ``GatewayManager._handle`` as a ``/workflow`` command. Because a cron
trigger is *not* agent-spawned, the result is delivered to the channel (the
cron's chat), not routed back to the agent.

Unlike agent prompts, a workflow job's ``message`` is passed through verbatim as
the workflow's input — not wrapped in the agent execution preamble.
"""

from __future__ import annotations

import types
from unittest.mock import AsyncMock, MagicMock

from langclaw.bus.base import InboundMessage
from langclaw.cron.scheduler import (
    _MANAGERS,
    CronJob,
    CronManager,
    _fire_job,
    _schedule_to_cronjob,
)


class TestFireJobWorkflow:
    def _register_manager(self, manager_id="default"):
        bus = MagicMock()
        bus.publish = AsyncMock()
        manager = MagicMock()
        manager._bus = bus
        _MANAGERS[manager_id] = manager
        return manager, bus

    def teardown_method(self):
        _MANAGERS.clear()

    async def test_workflow_job_stamps_workflow_name_and_skips_prompt_wrap(self):
        _manager, bus = self._register_manager()

        await _fire_job(
            manager_id="default",
            message="summarise today's news",
            channel="telegram",
            user_id="u1",
            context_id="c1",
            chat_id="chat1",
            job_name="daily digest",
            workflow_name="digest",
        )

        bus.publish.assert_awaited_once()
        msg = bus.publish.call_args[0][0]
        assert isinstance(msg, InboundMessage)
        assert msg.origin == "cron"
        assert msg.metadata["workflow_name"] == "digest"
        # Workflow input is the raw message, not the agent "Scheduled run" wrapper.
        assert msg.content == "summarise today's news"

    async def test_non_workflow_job_keeps_agent_prompt_wrap(self):
        _manager, bus = self._register_manager()

        await _fire_job(
            manager_id="default",
            message="remind me to stretch",
            channel="telegram",
            user_id="u1",
            context_id="c1",
            chat_id="chat1",
            job_name="stretch",
        )

        msg = bus.publish.call_args[0][0]
        assert "workflow_name" not in msg.metadata
        assert "Scheduled run" in msg.content  # agent execution wrapper preserved


class TestAddJobWorkflow:
    async def test_workflow_name_threaded_into_fire_kwargs(self):
        manager = CronManager(bus=MagicMock())
        manager._scheduler = AsyncMock()

        await manager.add_job(
            name="daily digest",
            message="news",
            channel="telegram",
            user_id="u1",
            workflow_name="digest",
            every_seconds=3600,
        )

        manager._scheduler.add_schedule.assert_awaited_once()
        _args, kwargs = manager._scheduler.add_schedule.call_args
        assert kwargs["kwargs"]["workflow_name"] == "digest"


class TestScheduleReconstruction:
    def test_workflow_name_restored_from_kwargs(self):
        schedule = types.SimpleNamespace(
            id="job1",
            trigger=None,
            kwargs={
                "job_name": "daily digest",
                "message": "news",
                "channel": "telegram",
                "user_id": "u1",
                "context_id": "c1",
                "chat_id": "chat1",
                "schedule": "every:3600s",
                "workflow_name": "digest",
            },
        )

        job = _schedule_to_cronjob(schedule)
        assert isinstance(job, CronJob)
        assert job.workflow_name == "digest"

    def test_old_job_without_workflow_name_defaults_empty(self):
        schedule = types.SimpleNamespace(
            id="job2",
            trigger=None,
            kwargs={
                "job_name": "x",
                "message": "y",
                "channel": "telegram",
                "user_id": "u1",
                "context_id": "c1",
                "chat_id": "chat1",
                "schedule": "every:60s",
            },
        )
        job = _schedule_to_cronjob(schedule)
        assert job.workflow_name == ""
