"""Cron-fired workflows (#48): a scheduled job can run a workflow, not just an
agent prompt. Tests the module-level fire callback directly with a fake manager.
"""

from __future__ import annotations

import pytest

from langclaw.cron import scheduler as sched


class _FakeBus:
    def __init__(self) -> None:
        self.published: list[object] = []

    async def publish(self, msg: object) -> None:
        self.published.append(msg)


class _FakeManager:
    def __init__(self, bus: _FakeBus) -> None:
        self._bus = bus


@pytest.fixture
def fake_manager():
    bus = _FakeBus()
    mgr = _FakeManager(bus)
    sched._MANAGERS["test-mgr"] = mgr  # type: ignore[assignment]
    try:
        yield mgr, bus
    finally:
        sched._MANAGERS.pop("test-mgr", None)


@pytest.mark.asyncio
async def test_fire_job_workflow_publishes_origin_workflow(fake_manager):
    """A workflow-fired job publishes origin='workflow' with name + input in metadata."""
    _, bus = fake_manager

    await sched._fire_job(
        manager_id="test-mgr",
        message="run the research workflow",
        channel="telegram",
        user_id="u1",
        context_id="default",
        chat_id="c1",
        job_name="daily research",
        workflow_name="research",
        workflow_input='{"query": "AI news"}',
    )

    assert len(bus.published) == 1
    msg = bus.published[0]
    assert msg.origin == "workflow"
    assert msg.metadata["workflow_name"] == "research"
    assert msg.metadata["workflow_input"] == '{"query": "AI news"}'
    # Workflow jobs carry the raw message (no agent-prompt wrapper).
    assert msg.content == "run the research workflow"


@pytest.mark.asyncio
async def test_fire_job_without_workflow_is_still_agent_prompt(fake_manager):
    """Backward compat: a job without workflow_name fires origin='cron' (wrapped)."""
    _, bus = fake_manager

    await sched._fire_job(
        manager_id="test-mgr",
        message="say hi",
        channel="telegram",
        user_id="u1",
        context_id="default",
        chat_id="c1",
        job_name="greet",
    )

    msg = bus.published[0]
    assert msg.origin == "cron"
    assert "workflow_name" not in msg.metadata
    assert "say hi" in msg.content  # wrapped, but contains the task


@pytest.mark.asyncio
async def test_fire_job_workflow_stamps_cron_job_id(fake_manager):
    """A workflow fire carries its cron job_id so the gateway can disarm it if the
    workflow has since been deleted."""
    _, bus = fake_manager

    await sched._fire_job(
        manager_id="test-mgr",
        message="run it",
        channel="telegram",
        user_id="u1",
        context_id="default",
        chat_id="c1",
        job_name="daily digest",
        workflow_name="hn_ai_digest",
        job_id="job-42",
    )

    msg = bus.published[0]
    assert msg.origin == "workflow"
    assert msg.metadata["cron_job_id"] == "job-42"
