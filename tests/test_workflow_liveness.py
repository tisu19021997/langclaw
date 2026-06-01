"""Same-session liveness: when a workflow is registered at runtime (via
``save_workflow``), the gateway rebuilds the default agent so the new
``workflow_<name>`` tool goes live without a restart — and the rebuild carries
the workflow registry/runtime (so AGENTS.md reloads no longer silently drop the
workflow tools either).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from langclaw.config.schema import LangclawConfig
from langclaw.gateway.manager import GatewayManager
from langclaw.workflows import WorkflowRegistry, WorkflowSpec


class _Chan:
    name = "dummy"

    def is_enabled(self) -> bool:
        return True

    async def start(self, bus) -> None:  # pragma: no cover
        _ = bus

    async def stop(self) -> None:  # pragma: no cover
        return


class _CP:
    def get(self) -> Any:
        return None


class _Agent:
    def __init__(self, tag: str) -> None:
        self.tag = tag


def _mgr(tmp_path: Path, registry: WorkflowRegistry) -> GatewayManager:
    (tmp_path / "workspace").mkdir(parents=True, exist_ok=True)
    cfg = LangclawConfig()
    cfg.agents.root_dir = str(tmp_path)
    cfg.workflows.enabled = True
    return GatewayManager(
        config=cfg,
        bus=None,
        checkpointer_backend=_CP(),
        agent=_Agent("initial"),
        channels=[_Chan()],
        default_agent_spec={"system_prompt": None, "bus": None, "model": None},
        workflow_registry=registry,
        workflow_runtime=object(),
    )


@pytest.mark.asyncio
async def test_registering_workflow_rebuilds_default_agent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    registry = WorkflowRegistry()
    mgr = _mgr(tmp_path, registry)

    captured: dict = {}

    def fake_create_claw_agent(config, **kwargs):
        _ = config
        captured.update(kwargs)
        return _Agent("rebuilt")

    monkeypatch.setattr(
        "langclaw.agents.builder.create_claw_agent", fake_create_claw_agent, raising=True
    )

    # First check establishes the baseline (registry version + AGENTS.md hash).
    first = await mgr._ensure_agent_fresh("default")
    assert first.tag == "initial"

    # Author a workflow at runtime — registry version bumps.
    registry.register(
        WorkflowSpec(name="hn", fn=lambda c, i: None, description="d", mode="saved", script="BODY")
    )

    rebuilt = await mgr._ensure_agent_fresh("default")
    assert rebuilt.tag == "rebuilt"
    assert mgr._agent_map["default"] is rebuilt
    # The rebuild must carry the workflow wiring (else the new tool is dropped).
    assert captured["workflow_registry"] is registry
    assert captured["workflow_runtime"] is mgr._workflow_runtime


@pytest.mark.asyncio
async def test_no_rebuild_when_registry_unchanged(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    registry = WorkflowRegistry()
    mgr = _mgr(tmp_path, registry)
    calls = {"n": 0}

    def fake_create_claw_agent(config, **kwargs):
        _ = (config, kwargs)
        calls["n"] += 1
        return _Agent("rebuilt")

    monkeypatch.setattr(
        "langclaw.agents.builder.create_claw_agent", fake_create_claw_agent, raising=True
    )

    await mgr._ensure_agent_fresh("default")
    again = await mgr._ensure_agent_fresh("default")
    assert again.tag == "initial"
    assert calls["n"] == 0  # nothing changed → no rebuild
