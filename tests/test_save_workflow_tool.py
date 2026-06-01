"""The agent-facing ``save_workflow`` tool: persist a JS body authored at
runtime, register it as a ``mode="saved"`` workflow, and bump the registry so
the gateway can surface it as a live ``workflow_<name>`` tool.
"""

from __future__ import annotations

from pathlib import Path

from langclaw.agents.tools.save_workflow import build_save_workflow_tool
from langclaw.workflows import SavedWorkflowStore, WorkflowRegistry, WorkflowSpec


def _make(tmp_path: Path, *, reserved=(), permissions_enabled=False):
    registry = WorkflowRegistry()
    store = SavedWorkflowStore(tmp_path / "workflows")
    tool = build_save_workflow_tool(
        registry=registry,
        store=store,
        reserved_names=set(reserved),
        permissions_enabled=permissions_enabled,
    )
    return tool, registry, store


async def test_save_persists_and_registers(tmp_path: Path) -> None:
    tool, registry, store = _make(tmp_path)
    before = registry.version

    result = await tool.ainvoke(
        {
            "name": "hn_digest",
            "script": "await tools.output({ result: inp });",
            "description": "Summarize HN",
            "uses_tools": ["web_fetch", "write_file"],
        }
    )

    assert "error" not in result
    assert "workflow_hn_digest" in result["message"]
    # Registered as a saved spec...
    spec = registry.get("hn_digest")
    assert spec is not None and spec.mode == "saved"
    assert spec.uses_tools == ["web_fetch", "write_file"]
    # ...persisted to disk...
    assert (store.directory / "hn_digest.js").exists()
    # ...and the registry version advanced so the gateway rebuilds.
    assert registry.version > before


async def test_invalid_name_returns_error_not_raise(tmp_path: Path) -> None:
    tool, registry, _ = _make(tmp_path)
    result = await tool.ainvoke({"name": "../escape", "script": "x", "description": "d"})
    assert "error" in result
    assert registry.get("../escape") is None


async def test_duplicate_name_returns_error(tmp_path: Path) -> None:
    tool, registry, _ = _make(tmp_path)
    registry.register(WorkflowSpec(name="taken", fn=lambda c, i: None, description="existing"))
    result = await tool.ainvoke(
        {"name": "taken", "script": "await tools.output({ result: 1 });", "description": "d"}
    )
    assert "error" in result


async def test_name_colliding_with_reserved_returns_error(tmp_path: Path) -> None:
    # A name whose workflow_<name> tool would clash with an existing tool/command.
    tool, registry, _ = _make(tmp_path, reserved={"web_search"})
    result = await tool.ainvoke(
        {"name": "web_search", "script": "await tools.output({ result: 1 });", "description": "d"}
    )
    assert "error" in result
    assert registry.get("web_search") is None


async def test_empty_script_returns_error(tmp_path: Path) -> None:
    tool, registry, _ = _make(tmp_path)
    result = await tool.ainvoke({"name": "blank", "script": "   ", "description": "d"})
    assert "error" in result
    assert registry.get("blank") is None


async def test_disk_failure_rolls_back_registration(tmp_path: Path, monkeypatch) -> None:
    """If persistence fails, the registry must NOT keep the spec — else it would
    vanish on restart and block re-saving the same name."""
    tool, registry, store = _make(tmp_path)

    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(store, "save", boom)
    result = await tool.ainvoke(
        {"name": "doomed", "script": "await tools.output({ result: 1 });", "description": "d"}
    )

    assert "error" in result and "not saved" in result["error"]
    # Rolled back: name is free to re-save, and version returned to baseline net-even.
    assert registry.get("doomed") is None
    assert "doomed" not in registry


async def test_permissions_enabled_message_is_honest(tmp_path: Path) -> None:
    """Under RBAC the new workflow_<name> is default-denied; the message must not
    promise immediate availability."""
    tool, registry, _ = _make(tmp_path, permissions_enabled=True)
    result = await tool.ainvoke(
        {"name": "gated", "script": "await tools.output({ result: 1 });", "description": "d"}
    )
    assert "error" not in result
    assert registry.get("gated") is not None  # still registered + persisted
    msg = result["message"].lower()
    assert "rbac" in msg or "grant" in msg or "role" in msg
    assert "now available" not in msg


def test_registry_unregister_roundtrip() -> None:
    reg = WorkflowRegistry()
    reg.register(WorkflowSpec(name="x", fn=lambda c, i: None, description="d"))
    v = reg.version
    assert reg.unregister("x") is True
    assert reg.get("x") is None
    assert reg.version > v  # removal also bumps so the agent rebuilds
    assert reg.unregister("x") is False  # idempotent
