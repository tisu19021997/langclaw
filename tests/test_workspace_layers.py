"""Tests for layered workspace prompt assembly (Business Workspaces, Phase 1).

Red/green TDD for ``langclaw.agents.workspace_layers.assemble_system_prompt`` —
the two-phase merge-then-validate that replaces the flat ``AGENTS.md`` read when
``workspace_layers.enabled`` is on. Everything here is a pure-function test: no
model, no agent build, just a tree on ``tmp_path`` → assembled string.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from langclaw.agents.workspace_layers import (
    AUTHORITATIVE_BANNER,
    PERSONAL_BANNER,
    REASSERT_BANNER,
    SEALED_BANNER,
    TEAM_BANNER,
    assemble_system_prompt,
)
from langclaw.config.schema import LangclawConfig, WorkspaceLayerConfig


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _org(tmp_path: Path) -> Path:
    org = tmp_path / "org"
    _write(org / "POLICY.md", "Sealed: never deploy on Fridays.")
    _write(org / "SOUL.md", "You are Aurora, the brand voice.")
    _write(org / "AGENTS.md", "Playbook: greet, qualify, hand off.")
    return org


# --- backward compatibility --------------------------------------------------


def test_disabled_returns_legacy_prompt_unchanged(tmp_path: Path) -> None:
    """enabled=False → degenerate single-workspace case, zero behavior change."""
    _org(tmp_path)
    layers = WorkspaceLayerConfig(enabled=False)

    out = assemble_system_prompt(
        layers=layers,
        org_dir=tmp_path / "org",
        legacy_prompt="LEGACY FLAT PROMPT",
    )

    assert out == "LEGACY FLAT PROMPT"


def test_enabled_but_no_org_tree_falls_back_to_legacy(tmp_path: Path) -> None:
    """Enabling on an un-migrated flat deployment must not break it."""
    layers = WorkspaceLayerConfig(enabled=True)

    out = assemble_system_prompt(
        layers=layers,
        org_dir=tmp_path / "org",  # does not exist
        legacy_prompt="LEGACY FLAT PROMPT",
    )

    assert out == "LEGACY FLAT PROMPT"


# --- phase 1: merge & precedence --------------------------------------------


def test_org_layer_assembles_with_authority_banners(tmp_path: Path) -> None:
    _org(tmp_path)
    layers = WorkspaceLayerConfig(enabled=True)

    out = assemble_system_prompt(layers=layers, org_dir=tmp_path / "org")

    # All three org files surface, each under its authority banner.
    assert SEALED_BANNER in out
    assert AUTHORITATIVE_BANNER in out
    assert "never deploy on Fridays" in out
    assert "Aurora" in out
    assert "greet, qualify" in out
    # Sealed precedes authoritative (org wins, binding first).
    assert out.index(SEALED_BANNER) < out.index(AUTHORITATIVE_BANNER)
    # The layered prompt replaces the flat read — legacy is not appended.
    assert "LEGACY" not in assemble_system_prompt(
        layers=layers, org_dir=tmp_path / "org", legacy_prompt="LEGACY"
    )


def test_precedence_order_sealed_team_personal(tmp_path: Path) -> None:
    _org(tmp_path)
    team = tmp_path / "teams" / "growth"
    _write(team / "PLAYBOOK.md", "Growth refinement: prefer short hooks.")
    user = tmp_path / "users" / "u1"
    _write(user / "SOUL.md", "Personal: I like emoji.")
    layers = WorkspaceLayerConfig(enabled=True)

    out = assemble_system_prompt(
        layers=layers,
        org_dir=tmp_path / "org",
        team_dir=team,
        user_dir=user,
    )

    # employee ▸ team ▸ org overlay walk renders org-first … personal-last.
    assert (
        out.index(SEALED_BANNER)
        < out.index(AUTHORITATIVE_BANNER)
        < out.index(TEAM_BANNER)
        < out.index(PERSONAL_BANNER)
    )
    assert "prefer short hooks" in out
    assert "I like emoji" in out


def test_employee_memories_bounded_by_top_k(tmp_path: Path) -> None:
    _org(tmp_path)
    user = tmp_path / "users" / "u1"
    _write(user / "SOUL.md", "Personal style.")
    for i in range(20):
        _write(user / "memories" / f"mem_{i:02d}.md", f"memory body {i:02d}")
    layers = WorkspaceLayerConfig(enabled=True, employee_memory_top_k=3)

    out = assemble_system_prompt(layers=layers, org_dir=tmp_path / "org", user_dir=user)

    included = sum(1 for i in range(20) if f"memory body {i:02d}" in out)
    assert included == 3


# --- masks: drop default only ------------------------------------------------


def test_mask_drops_personal_default_but_not_org_authoritative(tmp_path: Path) -> None:
    """masks.yaml suppresses ``default:`` blocks only — org authority is immune."""
    _org(tmp_path)
    user = tmp_path / "users" / "u1"
    # Same filename at both layers: org SOUL is authoritative, personal SOUL is default.
    _write(user / "SOUL.md", "Personal soul note to suppress.")
    _write(user / "masks.yaml", "- SOUL.md\n")
    layers = WorkspaceLayerConfig(enabled=True)

    out = assemble_system_prompt(layers=layers, org_dir=tmp_path / "org", user_dir=user)

    # Personal default block dropped …
    assert "Personal soul note to suppress" not in out
    # … but the org authoritative SOUL with the same name survives.
    assert "Aurora" in out


# --- phase 2: validate re-asserts sealed ------------------------------------


def test_policy_yaml_sealed_rule_reasserted_when_absent(tmp_path: Path) -> None:
    org = _org(tmp_path)
    _write(
        org / "policy.yaml",
        "sealed:\n"
        "  - id: no-pii-export\n"
        '    rule: "Never export customer PII to third parties."\n'
        "approvers:\n"
        "  outbound: marketing-lead\n",
    )
    layers = WorkspaceLayerConfig(enabled=True)

    out = assemble_system_prompt(layers=layers, org_dir=org)

    # The machine-checkable sealed rule enters via the validate pass.
    assert REASSERT_BANNER in out
    assert "Never export customer PII" in out


def test_sealed_rule_not_duplicated_when_already_in_policy_md(tmp_path: Path) -> None:
    org = _org(tmp_path)
    _write(org / "POLICY.md", "Sealed: Never export customer PII to third parties.")
    _write(
        org / "policy.yaml",
        'sealed:\n  - id: no-pii\n    rule: "Never export customer PII to third parties."\n',
    )
    layers = WorkspaceLayerConfig(enabled=True)

    out = assemble_system_prompt(layers=layers, org_dir=org)

    # Already present in prose → validate pass adds nothing.
    assert REASSERT_BANNER not in out
    assert out.count("Never export customer PII to third parties") == 1


# --- builder wiring (the layered prompt actually reaches the agent) ----------


class _FakeModel:
    def bind_tools(self, *_a: Any, **_k: Any) -> _FakeModel:
        return self


def _capture_create_deep_agent(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    import deepagents

    captured: dict[str, Any] = {}

    def fake_create_deep_agent(**kwargs: Any) -> str:
        captured.update(kwargs)
        return "SENTINEL_AGENT"

    monkeypatch.setattr(deepagents, "create_deep_agent", fake_create_deep_agent)
    return captured


def test_builder_uses_layered_org_prompt_when_enabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End-to-end: enabling layering routes the assembled org prompt into the agent."""
    from langclaw.agents.builder import create_claw_agent

    workspace = tmp_path / "workspace"
    _write(workspace / "AGENTS.md", "FLAT LEGACY PLAYBOOK")  # the un-layered fallback
    _org(workspace)  # org/POLICY.md, org/SOUL.md, org/AGENTS.md

    cfg = LangclawConfig(
        agents={
            "root_dir": str(tmp_path),
            "workspace_layers": {"enabled": True},
        },
        interpreter={"enabled": False},  # avoid the optional langchain_quickjs dep
    )
    captured = _capture_create_deep_agent(monkeypatch)

    create_claw_agent(cfg, model=_FakeModel())

    prompt = captured["system_prompt"]
    assert SEALED_BANNER in prompt
    assert AUTHORITATIVE_BANNER in prompt
    assert "never deploy on Fridays" in prompt
    # The flat AGENTS.md is shadowed by the layered org tree.
    assert "FLAT LEGACY PLAYBOOK" not in prompt


def test_builder_keeps_flat_prompt_when_layers_disabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Default (disabled) build is byte-for-byte the flat AGENTS.md — zero change."""
    from langclaw.agents.builder import create_claw_agent

    workspace = tmp_path / "workspace"
    _write(workspace / "AGENTS.md", "FLAT LEGACY PLAYBOOK")
    _org(workspace)  # present but must be ignored when disabled

    cfg = LangclawConfig(
        agents={"root_dir": str(tmp_path)},  # workspace_layers off by default
        interpreter={"enabled": False},
    )
    captured = _capture_create_deep_agent(monkeypatch)

    create_claw_agent(cfg, model=_FakeModel())

    prompt = captured["system_prompt"]
    assert prompt == "FLAT LEGACY PLAYBOOK"
    assert SEALED_BANNER not in prompt
