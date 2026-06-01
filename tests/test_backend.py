"""Tests for the pluggable deepagents backend factory and builder wiring."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from langclaw.agents.backend import backend_root_dir, make_backend
from langclaw.config.schema import BackendConfig, LangclawConfig

# ---------------------------------------------------------------------------
# make_backend factory
# ---------------------------------------------------------------------------


def test_default_backend_is_local_shell(tmp_path: Path) -> None:
    """The default backend enables the ``execute`` tool (LocalShellBackend)."""
    from deepagents.backends import FilesystemBackend, LocalShellBackend

    backend = make_backend(BackendConfig(), tmp_path)

    assert isinstance(backend, LocalShellBackend)
    # LocalShellBackend extends FilesystemBackend, so all file tools still work.
    assert isinstance(backend, FilesystemBackend)
    # The execute tool is what we are after.
    assert hasattr(backend, "execute")


def test_filesystem_backend_has_no_execute(tmp_path: Path) -> None:
    from deepagents.backends import FilesystemBackend, LocalShellBackend

    backend = make_backend(BackendConfig(backend="filesystem"), tmp_path)

    assert isinstance(backend, FilesystemBackend)
    assert not isinstance(backend, LocalShellBackend)


def test_state_backend(tmp_path: Path) -> None:
    from deepagents.backends import StateBackend

    backend = make_backend(BackendConfig(backend="state"), tmp_path)
    assert isinstance(backend, StateBackend)


def test_store_backend(tmp_path: Path) -> None:
    from deepagents.backends import StoreBackend

    backend = make_backend(BackendConfig(backend="store"), tmp_path)
    assert isinstance(backend, StoreBackend)


def test_unknown_backend_raises(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Unknown agent backend"):
        make_backend(BackendConfig.model_construct(backend="redis"), tmp_path)


def test_backend_uses_workspace_dir_as_root(tmp_path: Path) -> None:
    backend = make_backend(BackendConfig(), tmp_path)
    assert backend_root_dir(backend) == tmp_path


def test_explicit_root_dir_overrides_workspace(tmp_path: Path) -> None:
    other = tmp_path / "custom"
    backend = make_backend(BackendConfig(root_dir=str(other)), tmp_path)
    assert backend_root_dir(backend) == other


# ---------------------------------------------------------------------------
# backend_root_dir helper
# ---------------------------------------------------------------------------


def test_root_dir_none_for_non_filesystem_backend(tmp_path: Path) -> None:
    backend = make_backend(BackendConfig(backend="state"), tmp_path)
    assert backend_root_dir(backend) is None


# ---------------------------------------------------------------------------
# config schema
# ---------------------------------------------------------------------------


def test_backend_config_defaults() -> None:
    cfg = LangclawConfig()
    assert cfg.agents.backend.backend == "local_shell"
    assert cfg.agents.backend.virtual_mode is True


# ---------------------------------------------------------------------------
# builder wiring — the backend is threaded into create_deep_agent
# ---------------------------------------------------------------------------


class _FakeModel:
    """Stand-in chat model so the builder never touches a live provider."""

    def bind_tools(self, *_a: Any, **_k: Any) -> _FakeModel:
        return self


def _capture_create_deep_agent(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Patch ``deepagents.create_deep_agent`` and capture its kwargs."""
    import deepagents

    captured: dict[str, Any] = {}

    def fake_create_deep_agent(**kwargs: Any) -> str:
        captured.update(kwargs)
        return "SENTINEL_AGENT"

    monkeypatch.setattr(deepagents, "create_deep_agent", fake_create_deep_agent)
    return captured


def test_builder_default_backend_is_local_shell(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from deepagents.backends import LocalShellBackend

    from langclaw.agents.builder import create_claw_agent

    captured = _capture_create_deep_agent(monkeypatch)
    cfg = LangclawConfig(agents={"root_dir": str(tmp_path)})

    agent = create_claw_agent(cfg, model=_FakeModel())

    assert agent == "SENTINEL_AGENT"
    assert isinstance(captured["backend"], LocalShellBackend)


def test_builder_respects_explicit_backend(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from deepagents.backends import StateBackend

    from langclaw.agents.builder import create_claw_agent

    captured = _capture_create_deep_agent(monkeypatch)
    cfg = LangclawConfig(agents={"root_dir": str(tmp_path)})
    explicit = StateBackend()

    create_claw_agent(cfg, model=_FakeModel(), backend=explicit)

    assert captured["backend"] is explicit


def test_builder_skips_local_fs_tools_for_non_filesystem_backend(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """move_file/delete_file are local-FS only — omitted for state/store backends."""
    from deepagents.backends import StateBackend

    from langclaw.agents.builder import create_claw_agent

    captured = _capture_create_deep_agent(monkeypatch)
    cfg = LangclawConfig(agents={"root_dir": str(tmp_path)})

    create_claw_agent(cfg, model=_FakeModel(), backend=StateBackend())

    tool_names = {getattr(t, "name", None) for t in captured["tools"]}
    assert "move_file" not in tool_names
    assert "delete_file" not in tool_names


def test_builder_includes_local_fs_tools_for_filesystem_backend(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from langclaw.agents.builder import create_claw_agent

    captured = _capture_create_deep_agent(monkeypatch)
    cfg = LangclawConfig(agents={"root_dir": str(tmp_path)})

    # Default backend (local_shell) is filesystem-rooted.
    create_claw_agent(cfg, model=_FakeModel())

    tool_names = {getattr(t, "name", None) for t in captured["tools"]}
    assert "move_file" in tool_names
    assert "delete_file" in tool_names
