"""
Tests for enabling workflows standalone — i.e. letting the agent compose a
*dynamic* workflow (`orchestrate`) even when no `@app.workflow()` is predefined.

Activation is true when ANY of:
  - a workflow is registered via `@app.workflow()` (back-compat), OR
  - the constructor flag `Langclaw(enable_workflows=True)`, OR
  - the config flag `LANGCLAW__WORKFLOWS__ENABLED=true` (so the CLI gateway can
    turn it on with no code).

When active, the agent gets the `orchestrate` tool (and `run_workflow` only when
named workflows exist), the `/workflow` command, and a short system-prompt nudge.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from langclaw.app import Langclaw
from langclaw.config.schema import LangclawConfig


def _app(**kwargs) -> Langclaw:
    # Pass an explicit config so tests don't depend on the repo .env.
    return Langclaw(config=LangclawConfig(), **kwargs)


# ---------------------------------------------------------------------------
# Config flag
# ---------------------------------------------------------------------------


class TestWorkflowsConfig:
    def test_default_disabled(self):
        from langclaw.config.schema import WorkflowsConfig

        assert WorkflowsConfig().enabled is False

    def test_env_enables(self, monkeypatch):
        monkeypatch.setenv("LANGCLAW__WORKFLOWS__ENABLED", "true")
        assert LangclawConfig().workflows.enabled is True


# ---------------------------------------------------------------------------
# App-level activation logic
# ---------------------------------------------------------------------------


class TestWorkflowsActive:
    def test_inactive_by_default(self):
        assert _app()._workflows_active() is False

    def test_constructor_flag_activates(self):
        assert _app(enable_workflows=True)._workflows_active() is True

    def test_config_flag_activates(self, monkeypatch):
        monkeypatch.setenv("LANGCLAW__WORKFLOWS__ENABLED", "true")
        assert Langclaw(config=LangclawConfig())._workflows_active() is True

    def test_registering_a_workflow_activates(self):
        app = _app()

        @app.workflow("w", description="d")
        async def w(ctx):
            return "x"

        assert app._workflows_active() is True


# ---------------------------------------------------------------------------
# Tool wiring
# ---------------------------------------------------------------------------


class TestWorkflowToolWiring:
    def test_orchestrate_present_when_enabled_without_predefined_workflows(self):
        tools = _app(enable_workflows=True)._workflow_tools(MagicMock())
        names = {t.name for t in tools}
        assert "orchestrate" in names
        assert "run_workflow" not in names  # no named workflows registered

    def test_both_tools_when_a_workflow_is_registered(self):
        app = _app(enable_workflows=True)

        @app.workflow("digest", description="d")
        async def digest(ctx):
            return "x"

        names = {t.name for t in app._workflow_tools(MagicMock())}
        assert {"orchestrate", "run_workflow"} <= names

    def test_no_tools_when_disabled(self):
        assert _app()._workflow_tools(MagicMock()) == []

    def test_no_tools_without_a_bus(self):
        # Tools publish to the bus; without one they cannot function.
        assert _app(enable_workflows=True)._workflow_tools(None) == []


# ---------------------------------------------------------------------------
# System-prompt nudge
# ---------------------------------------------------------------------------


class TestSystemPromptAugmentation:
    def test_guidance_added_when_active(self):
        app = Langclaw(config=LangclawConfig(), system_prompt="BASE", enable_workflows=True)
        prompt = app._augment_system_prompt()
        assert "BASE" in prompt
        assert "orchestrate" in prompt.lower()

    def test_prompt_unchanged_when_inactive(self):
        app = Langclaw(config=LangclawConfig(), system_prompt="BASE")
        assert app._augment_system_prompt() == "BASE"


# ---------------------------------------------------------------------------
# Gateway gate
# ---------------------------------------------------------------------------


class TestGatewayWorkflowsEnabled:
    def _mgr(self, **kwargs):
        from langclaw.gateway.manager import GatewayManager

        config = MagicMock()
        config.agents.display_name = ""
        config.permissions.enabled = False
        cp = MagicMock()
        cp.get.return_value = MagicMock()
        return GatewayManager(
            config=config,
            bus=MagicMock(),
            checkpointer_backend=cp,
            agent=MagicMock(),
            channels=[],
            **kwargs,
        )

    def test_enabled_flag_creates_runner_and_command(self):
        mgr = self._mgr(workflows_enabled=True)
        assert mgr._workflow_runner is not None
        assert "_interpret" in mgr._workflow_runner  # built-in compose interpreter
        assert mgr._command_router._commands.get("workflow") is not None

    def test_disabled_has_no_runner_or_command(self):
        mgr = self._mgr()
        assert mgr._workflow_runner is None
        assert mgr._command_router._commands.get("workflow") is None
