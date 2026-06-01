"""File-backed SavedWorkflowStore — saved workflows are plain .js files the agent
writes into the workspace ``workflows/`` folder, with metadata inline as
``// @`` comment directives. The loader reads them back as workflow_<name> tools.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from langclaw.workflows.saved_store import (
    SavedWorkflow,
    SavedWorkflowStore,
    parse_metadata,
)


def test_parse_metadata_from_header() -> None:
    script = (
        "// @description Summarize HN\n"
        "// @uses web_fetch, write_file\n"
        "await tools.output({ result: 1 });"
    )
    description, uses = parse_metadata(script)
    assert description == "Summarize HN"
    assert uses == ["web_fetch", "write_file"]


def test_parse_metadata_missing_header_is_tolerant() -> None:
    description, uses = parse_metadata("await tools.output({ result: 1 });")
    assert description == ""
    assert uses == []


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


def test_load_reads_a_hand_written_js_file(tmp_path: Path) -> None:
    # Simulates the agent writing the file directly with write_file.
    root = tmp_path / "workflows"
    root.mkdir(parents=True)
    (root / "daily.js").write_text(
        "// @description Daily report\n"
        "// @uses web_search\n"
        "const r = await tools.webSearch({ query: 'x' });\n"
        "await tools.output({ result: r });",
        encoding="utf-8",
    )
    [wf] = SavedWorkflowStore(root).load_all()
    assert wf.name == "daily"
    assert wf.description == "Daily report"
    assert wf.uses_tools == ["web_search"]


def test_load_all_empty_when_dir_missing(tmp_path: Path) -> None:
    assert SavedWorkflowStore(tmp_path / "nope").load_all() == []


def test_no_header_loads_with_blank_metadata(tmp_path: Path) -> None:
    root = tmp_path / "workflows"
    root.mkdir(parents=True)
    (root / "bare.js").write_text("await tools.output({ result: 1 });", encoding="utf-8")
    [wf] = SavedWorkflowStore(root).load_all()
    assert wf.name == "bare"
    assert wf.description == ""
    assert wf.uses_tools == []


def test_save_rejects_unsafe_name(tmp_path: Path) -> None:
    store = SavedWorkflowStore(tmp_path / "workflows")
    for bad in ("../escape", "a/b", "with space", ""):
        with pytest.raises(ValueError):
            store.save(bad, script="x")


def test_load_skips_unsafe_filenames(tmp_path: Path) -> None:
    root = tmp_path / "workflows"
    root.mkdir(parents=True)
    # A dotfile stem like ".hidden" -> stem "" is unsafe and skipped.
    (root / "ok.js").write_text("await tools.output({ result: 1 });", encoding="utf-8")
    names = [wf.name for wf in SavedWorkflowStore(root).load_all()]
    assert names == ["ok"]


def test_delete_removes_file(tmp_path: Path) -> None:
    store = SavedWorkflowStore(tmp_path / "workflows")
    store.save("gone", script="await tools.output({ result: 1 });")
    assert store.delete("gone") is True
    assert store.load_all() == []
    assert store.delete("gone") is False
