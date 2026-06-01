"""Startup loader: saved workflows on disk become registered ``mode="saved"``
specs (and therefore ``workflow_<name>`` tools) when the gateway boots.
"""

from __future__ import annotations

from pathlib import Path

from langclaw import Langclaw
from langclaw.config.schema import LangclawConfig
from langclaw.workflows.saved_store import SavedWorkflowStore


def _app_with_workspace(tmp_path: Path, *, workflows=True, interpreter=True) -> Langclaw:
    cfg = LangclawConfig()
    cfg.agents.root_dir = str(tmp_path)
    cfg.workflows.enabled = workflows
    cfg.interpreter.enabled = interpreter
    return Langclaw(config=cfg)


def test_loader_registers_saved_workflows(tmp_path: Path) -> None:
    app = _app_with_workspace(tmp_path)
    store = SavedWorkflowStore(app._config.agents.workflows_dir)
    store.save(
        "hn_digest",
        script="await tools.output({ result: 1 });",
        description="Daily HN digest",
        uses_tools=["web_fetch", "write_file"],
    )

    app._load_saved_workflows()

    spec = app._workflows.get("hn_digest")
    assert spec is not None
    assert spec.mode == "saved"
    assert spec.description == "Daily HN digest"
    assert "tools.output" in spec.script
    assert spec.uses_tools == ["web_fetch", "write_file"]


def test_loader_noop_when_interpreter_disabled(tmp_path: Path) -> None:
    # Saved workflows are JS bodies run in the eval sandbox; without the
    # interpreter there is nothing to run them, so they are not loaded.
    app = _app_with_workspace(tmp_path, interpreter=False)
    SavedWorkflowStore(app._config.agents.workflows_dir).save(
        "x", script="await tools.output({ result: 1 });", description="d"
    )
    app._load_saved_workflows()
    assert app._workflows.get("x") is None


def test_loader_skips_name_colliding_with_registered_workflow(tmp_path: Path) -> None:
    app = _app_with_workspace(tmp_path)

    @app.workflow("dup", description="python one")
    async def _dup(ctx, inp):
        return "py"

    SavedWorkflowStore(app._config.agents.workflows_dir).save(
        "dup", script="await tools.output({ result: 1 });", description="saved one"
    )
    app._load_saved_workflows()

    # The pre-registered python workflow wins; the saved one is skipped.
    assert app._workflows.get("dup").mode == "python"
