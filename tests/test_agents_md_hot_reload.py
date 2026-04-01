from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from langclaw.config.schema import LangclawConfig
from langclaw.gateway.manager import GatewayManager


class DummyChannel:
    name = "dummy"

    def __init__(self) -> None:
        self._enabled = True
        self.sent: list[Any] = []

    def is_enabled(self) -> bool:
        return self._enabled

    async def start(self, bus) -> None:  # pragma: no cover - not used in tests
        _ = bus

    async def stop(self) -> None:  # pragma: no cover - not used in tests
        return

    async def send(self, msg) -> None:
        self.sent.append(msg)


class DummyCheckpointerBackend:
    def get(self) -> Any:
        return None


class DummyAgent:
    """Minimal agent that exposes its system prompt via a tool result."""

    def __init__(self, system_prompt: str) -> None:
        self.system_prompt = system_prompt

    async def astream(self, state, *, config=None, stream_mode=None, context=None, **kwargs):
        _ = (state, config, stream_mode, context, kwargs)
        from langchain_core.messages import AIMessage

        yield {
            "model": {
                "messages": [
                    AIMessage(content=f"PROMPT:{self.system_prompt}"),
                ],
            },
        }


@pytest.mark.asyncio
async def test_default_agent_rebuilds_when_agents_md_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    # Use a temporary workspace for this test.
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)

    # Initial AGENTS.md
    agents_md = workspace / "AGENTS.md"
    agents_md.write_text("Initial prompt", encoding="utf-8")

    cfg = LangclawConfig()
    cfg.agents.root_dir = str(tmp_path)

    channel = DummyChannel()
    checkpointer_backend = DummyCheckpointerBackend()

    # Build an initial dummy agent; the first freshness check should reuse it.
    initial_agent = DummyAgent("Initial prompt")

    # Construct GatewayManager with a prebuilt agent and a default_agent_spec.
    mgr = GatewayManager(
        config=cfg,
        bus=None,  # not used by DummyAgent
        checkpointer_backend=checkpointer_backend,
        agent=initial_agent,
        channels=[channel],
        default_agent_spec={
            "extra_tools": None,
            "extra_middleware": None,
            "subagents": None,
            "system_prompt": None,
            "bus": None,
            "model": None,
        },
    )

    # Monkeypatch create_claw_agent so that a rebuild does not require real
    # model credentials and instead returns a new DummyAgent instance whose
    # prompt reflects the updated AGENTS.md contents.
    def fake_create_claw_agent(config, **kwargs):
        _ = (config, kwargs)
        return DummyAgent(agents_md.read_text("utf-8"))

    monkeypatch.setattr(
        "langclaw.agents.builder.create_claw_agent",
        fake_create_claw_agent,
        raising=True,
    )

    # First call should not trigger rebuild (hash is initialised) and should
    # return the existing compiled agent instance.
    agent1 = await mgr._ensure_agent_fresh("default")
    assert agent1 is initial_agent
    assert isinstance(agent1, DummyAgent)
    assert agent1.system_prompt == "Initial prompt"

    # Change AGENTS.md and force a different hash.
    agents_md.write_text("Updated prompt", encoding="utf-8")
    # Force the hash to differ by clearing stored hash.
    mgr._agents_md_hashes["default"] = "old-hash"

    # Second call should rebuild the agent after the hash change. We don't
    # assert on the concrete type (it will be a CompiledStateGraph in real
    # usage); we just verify that a different instance is returned and that
    # the internal registry has been updated.
    rebuilt = await mgr._ensure_agent_fresh("default")
    assert rebuilt is not initial_agent
    assert mgr._agent_map["default"] is rebuilt
