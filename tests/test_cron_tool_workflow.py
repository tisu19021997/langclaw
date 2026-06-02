"""The agent-facing ``cron`` tool can schedule a saved workflow deterministically.

Without this, the only way to schedule recurring work is a natural-language
``task`` job whose ``message`` is re-authored freehand by the LLM on every fire.
A ``workflow_name`` arg routes the job through ``origin="workflow"`` instead, so
the frozen saved script runs verbatim (no LLM authoring) on each fire.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from langclaw.agents.tools.cron import make_cron_tool


def _runtime(role: str = "admin"):
    return SimpleNamespace(
        context=SimpleNamespace(
            channel="telegram",
            user_id="u1",
            context_id="default",
            chat_id="c1",
            user_role=role,
        )
    )


@pytest.mark.asyncio
async def test_cron_add_forwards_workflow_name():
    """``workflow_name`` is forwarded to ``add_job`` so the job runs the workflow."""
    mgr = AsyncMock()
    mgr.add_job.return_value = "job-123"
    cron = make_cron_tool(mgr, timezone="Asia/Ho_Chi_Minh").coroutine

    out = await cron(
        action="add",
        type="task",
        message="Run the hn-ai-digest workflow",
        workflow_name="hn-ai-digest",
        cron_expr="0 10 * * *",
        runtime=_runtime(),
    )

    mgr.add_job.assert_awaited_once()
    kwargs = mgr.add_job.await_args.kwargs
    assert kwargs["workflow_name"] == "hn-ai-digest"
    assert kwargs["cron_expr"] == "0 10 * * *"
    assert "job-123" in out
    # The confirmation should make the deterministic routing obvious.
    assert "hn-ai-digest" in out


@pytest.mark.asyncio
async def test_cron_add_forwards_workflow_input():
    """Optional JSON ``workflow_input`` is forwarded alongside ``workflow_name``."""
    mgr = AsyncMock()
    mgr.add_job.return_value = "job-xyz"
    cron = make_cron_tool(mgr, timezone="UTC").coroutine

    await cron(
        action="add",
        type="task",
        message="Run the digest",
        workflow_name="digest",
        workflow_input='{"limit": 5}',
        cron_expr="0 9 * * *",
        runtime=_runtime(),
    )

    kwargs = mgr.add_job.await_args.kwargs
    assert kwargs["workflow_name"] == "digest"
    assert kwargs["workflow_input"] == '{"limit": 5}'


@pytest.mark.asyncio
async def test_cron_add_without_workflow_name_stays_agent_prompt():
    """A plain task job forwards no workflow_name (empty string), preserving old behavior."""
    mgr = AsyncMock()
    mgr.add_job.return_value = "job-1"
    cron = make_cron_tool(mgr, timezone="UTC").coroutine

    await cron(
        action="add",
        type="task",
        message="Summarize the news",
        cron_expr="0 9 * * *",
        runtime=_runtime(),
    )

    kwargs = mgr.add_job.await_args.kwargs
    assert kwargs.get("workflow_name", "") == ""


@pytest.mark.asyncio
async def test_cron_add_workflow_requires_schedule():
    """workflow_name still needs a schedule — no cron_expr/every_seconds is an error."""
    mgr = AsyncMock()
    cron = make_cron_tool(mgr, timezone="UTC").coroutine

    out = await cron(
        action="add",
        type="task",
        message="Run the digest",
        workflow_name="digest",
        runtime=_runtime(),
    )

    assert "Error" in out
    mgr.add_job.assert_not_awaited()
