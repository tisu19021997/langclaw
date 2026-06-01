"""File-backed SavedWorkflowStore — persist an LLM-authored JS body as a named,
reusable workflow under the workspace ``workflows/`` folder, so it survives
restart and can be reloaded as a ``workflow_<name>`` tool.

Distinct from ``StoreScriptStore`` (per-run, keyed by run_id, in the checkpointer
DB): this is the user's "save that workflow" artifact — a named .js file on disk.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from langclaw.workflows.saved_store import SavedWorkflow, SavedWorkflowStore


def test_save_then_load_roundtrips(tmp_path: Path) -> None:
    store = SavedWorkflowStore(tmp_path / "workflows")
    store.save(
        "hn_digest",
        script="await tools.output({ result: 'ok' });",
        description="Summarize HN",
        uses_tools=["web_fetch", "write_file"],
    )

    loaded = store.load_all()
    assert len(loaded) == 1
    wf = loaded[0]
    assert isinstance(wf, SavedWorkflow)
    assert wf.name == "hn_digest"
    assert wf.description == "Summarize HN"
    assert wf.uses_tools == ["web_fetch", "write_file"]
    assert "tools.output" in wf.script


def test_save_writes_js_file_to_workflows_folder(tmp_path: Path) -> None:
    root = tmp_path / "workflows"
    store = SavedWorkflowStore(root)
    store.save("daily", script="await tools.output({ result: 1 });", description="d")
    # The JS body is a plain .js file the developer can read/edit/version.
    assert (root / "daily.js").read_text("utf-8").startswith("await tools.output")


def test_load_all_empty_when_dir_missing(tmp_path: Path) -> None:
    store = SavedWorkflowStore(tmp_path / "does-not-exist")
    assert store.load_all() == []


def test_save_overwrites_same_name(tmp_path: Path) -> None:
    store = SavedWorkflowStore(tmp_path / "workflows")
    store.save("x", script="await tools.output({ result: 1 });", description="v1")
    store.save("x", script="await tools.output({ result: 2 });", description="v2")
    loaded = store.load_all()
    assert len(loaded) == 1
    assert loaded[0].description == "v2"
    assert "result: 2" in loaded[0].script


def test_rejects_unsafe_name(tmp_path: Path) -> None:
    store = SavedWorkflowStore(tmp_path / "workflows")
    for bad in ("../escape", "a/b", "with space", ""):
        with pytest.raises(ValueError):
            store.save(bad, script="x", description="")


def test_delete_removes_files(tmp_path: Path) -> None:
    store = SavedWorkflowStore(tmp_path / "workflows")
    store.save("gone", script="await tools.output({ result: 1 });", description="")
    assert store.delete("gone") is True
    assert store.load_all() == []
    assert store.delete("gone") is False  # idempotent
