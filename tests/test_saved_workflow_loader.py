"""Reconcile loader: saved-workflow .js files on disk become (and stay in sync
with) registered ``mode="saved"`` specs — and therefore ``workflow_<name>`` tools.
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


def _store(app: Langclaw) -> SavedWorkflowStore:
    return SavedWorkflowStore(app._config.agents.workflows_dir)


def test_loader_registers_saved_workflows(tmp_path: Path) -> None:
    app = _app_with_workspace(tmp_path)
    _store(app).save(
        "hn_digest",
        script="await tools.output({ result: 1 });",
        description="Daily HN digest",
        uses_tools=["web_fetch", "write_file"],
    )

    assert app._reload_saved_workflows() is True

    spec = app._workflows.get("hn_digest")
    assert spec is not None
    assert spec.mode == "saved"
    assert spec.description == "Daily HN digest"
    assert "tools.output" in spec.script
    assert spec.uses_tools == ["web_fetch", "write_file"]


def test_loader_noop_when_interpreter_disabled(tmp_path: Path) -> None:
    app = _app_with_workspace(tmp_path, interpreter=False)
    _store(app).save("x", script="await tools.output({ result: 1 });", description="d")
    assert app._reload_saved_workflows() is False
    assert app._workflows.get("x") is None


def test_loader_reconciles_when_interpreter_enabled_via_constructor_flag(tmp_path: Path) -> None:
    # `Langclaw(enable_interpreter=True)` must enable saved-workflow reconcile
    # exactly like `interpreter.enabled=True` in config. The reconcile gated on the
    # RAW config flag, so the constructor flag silently skipped it — `/workflows`
    # stayed empty and no `workflow_<name>` tools appeared, with no error.
    cfg = LangclawConfig()
    cfg.agents.root_dir = str(tmp_path)
    cfg.workflows.enabled = True
    cfg.interpreter.enabled = False  # NOT set in raw config...
    app = Langclaw(config=cfg, enable_interpreter=True)  # ...only via the constructor flag

    SavedWorkflowStore(app._config.agents.workflows_dir).save(
        "flagged", script="await tools.output({ result: 1 });", description="via flag"
    )

    assert app._reload_saved_workflows() is True
    assert app._workflows.get("flagged") is not None


def test_loader_skips_name_colliding_with_registered_workflow(tmp_path: Path) -> None:
    app = _app_with_workspace(tmp_path)

    @app.workflow("dup", description="python one")
    async def _dup(ctx, inp):
        return "py"

    _store(app).save("dup", script="await tools.output({ result: 1 });", description="saved one")
    app._reload_saved_workflows()

    # The pre-registered python workflow wins; the saved file is ignored.
    assert app._workflows.get("dup").mode == "python"


def test_reconcile_removes_deleted_file(tmp_path: Path) -> None:
    app = _app_with_workspace(tmp_path)
    store = _store(app)
    store.save("temp", script="await tools.output({ result: 1 });", description="d")
    app._reload_saved_workflows()
    assert app._workflows.get("temp") is not None

    store.delete("temp")
    assert app._reload_saved_workflows() is True
    assert app._workflows.get("temp") is None


def test_reconcile_updates_changed_file(tmp_path: Path) -> None:
    app = _app_with_workspace(tmp_path)
    store = _store(app)
    store.save("ev", script="await tools.output({ result: 1 });", description="v1")
    app._reload_saved_workflows()
    assert app._workflows.get("ev").description == "v1"

    store.save("ev", script="await tools.output({ result: 2 });", description="v2")
    assert app._reload_saved_workflows() is True
    spec = app._workflows.get("ev")
    assert spec.description == "v2"
    assert "result: 2" in spec.script


def test_reconcile_is_idempotent_when_unchanged(tmp_path: Path) -> None:
    app = _app_with_workspace(tmp_path)
    _store(app).save("stable", script="await tools.output({ result: 1 });", description="d")
    assert app._reload_saved_workflows() is True
    # Second run with no disk change must report no change (so no needless rebuild).
    assert app._reload_saved_workflows() is False
