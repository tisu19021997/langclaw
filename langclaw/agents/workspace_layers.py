"""Layered workspace prompt assembly — Business Workspaces, Phase 1 (read path).

This is the spine of ``docs/BUSINESS_WORKSPACES.md``: the single point where a
request's *effective* system prompt is composed from an OverlayFS-style stack of
``org ▸ team ▸ employee`` layers. It replaces the lone flat ``AGENTS.md`` read in
``builder.py`` when ``workspace_layers.enabled`` is on, and is a no-op (returns
the legacy prompt) otherwise — every existing single-workspace deployment is
unchanged.

Two-phase merge-then-validate (Kubernetes mutating→validating model):

* **Phase 1 — merge (mutating).** Read each layer in precedence order and wrap
  every present file in its authority-tag banner. Authority is decided by
  *layer + filename* — data, not trust:

  - org ``POLICY.md`` (and other ``sealed_files`` ``.md``) → ``sealed:`` (binding)
  - org ``SOUL.md`` / ``AGENTS.md``                        → ``authoritative:``
  - team ``PLAYBOOK.md``                                   → ``default:`` (team)
  - employee ``SOUL.md`` + top-k ``memories/``            → ``default:`` (personal)

  A user's ``masks.yaml`` may suppress blocks, but **only ``default:`` ones** —
  org ``sealed:`` / ``authoritative:`` blocks are structurally immune.

* **Phase 2 — validate (validating).** Re-assert every machine-checkable
  ``sealed:`` rule declared in ``policy.yaml``: any rule whose text is not
  already present in the merged prompt is appended under a re-assert banner, so
  a mask, a deleted file, or a future line-level edit can never strip a binding
  constraint. Authority is enforced *in code*, not by trusting the LLM.

Honest Phase-1 limits (see the doc's phased rollout):

- Employee/team layers are fully implemented and tested here, but the *builder*
  only wires the **org** layer today — per-user prompts need the per-``(agent,
  user)`` instance seam, which lands in a later PR. ``assemble_system_prompt``
  already accepts ``team_dir`` / ``user_dir`` so that wiring is a call-site change.
- Employee memories are bounded to the first ``employee_memory_top_k`` files by
  name. Semantic top-k retrieval against the live request is future work (it
  needs the per-request ``user_id`` that the build-time path lacks).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from langclaw.config.schema import WorkspaceLayerConfig

# Authority-tag banners (verbatim from the design's §4 merge table).
SEALED_BANNER = "[SEALED — BINDING, never override]"
AUTHORITATIVE_BANNER = "[AUTHORITATIVE — org]"
TEAM_BANNER = "[TEAM DEFAULT]"
PERSONAL_BANNER = "[PERSONAL — refines, never relaxes]"
REASSERT_BANNER = "[SEALED — RE-ASSERTED — machine-checked from policy.yaml]"

# Org files that carry org-rule authority (win on conflict, never maskable).
_AUTHORITATIVE_ORG_FILES = ("SOUL.md", "AGENTS.md")

# Authority levels.
_SEALED = "sealed"
_AUTHORITATIVE = "authoritative"
_DEFAULT = "default"


@dataclass
class _Block:
    """One prompt fragment with its source identity and authority level."""

    block_id: str  # filename, used for mask matching ("SOUL.md", "mem_01.md")
    authority: str  # _SEALED | _AUTHORITATIVE | _DEFAULT
    banner: str
    text: str

    def render(self) -> str:
        return f"{self.banner}\n{self.text.rstrip()}"


def _read(path: Path) -> str | None:
    """Read a UTF-8 file, returning ``None`` when it's absent or unreadable."""
    try:
        if path.is_file():
            return path.read_text("utf-8")
    except OSError as exc:  # pragma: no cover - defensive
        logger.warning(f"workspace_layers: could not read {path}: {exc}")
    return None


def _load_yaml(path: Path) -> object | None:
    """Parse a YAML file, returning ``None`` on absence/parse error (never raises)."""
    text = _read(path)
    if text is None:
        return None
    try:
        import yaml

        return yaml.safe_load(text)
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning(f"workspace_layers: could not parse YAML {path}: {exc}")
        return None


def _mask_ids(user_dir: Path | None) -> set[str]:
    """Block ids the user suppresses, from ``users/<id>/masks.yaml``.

    Accepts either a top-level list (``- OLD.md``) or a ``masks:`` key.
    """
    if user_dir is None:
        return set()
    data = _load_yaml(user_dir / "masks.yaml")
    if isinstance(data, dict):
        data = data.get("masks")
    if isinstance(data, list):
        return {str(item).strip() for item in data if str(item).strip()}
    return set()


def _sealed_rules(org_dir: Path, layers: WorkspaceLayerConfig) -> list[str]:
    """Machine-checkable sealed-rule texts from the approver-map YAML.

    Expects ``sealed: [{id, rule}, ...]``; tolerates a bare string list. Returns
    the human-readable rule texts the validate pass guarantees in the prompt.
    """
    data = _load_yaml(org_dir / layers.approver_map_file)
    if not isinstance(data, dict):
        return []
    rules: list[str] = []
    for item in data.get("sealed") or []:
        if isinstance(item, dict):
            text = item.get("rule") or item.get("id")
            if text:
                rules.append(str(text))
        elif isinstance(item, str) and item.strip():
            rules.append(item.strip())
    return rules


def _merge(
    org_dir: Path,
    team_dir: Path | None,
    user_dir: Path | None,
    layers: WorkspaceLayerConfig,
) -> list[_Block]:
    """Phase 1: read layers in precedence order into authority-tagged blocks."""
    blocks: list[_Block] = []

    # SEALED — org .md sealed files (prose). .yaml sealed files enter via Phase 2.
    for name in layers.sealed_files:
        if name.endswith((".yaml", ".yml")):
            continue
        text = _read(org_dir / name)
        if text:
            blocks.append(_Block(name, _SEALED, SEALED_BANNER, text))

    # AUTHORITATIVE — org persona + playbook.
    for name in _AUTHORITATIVE_ORG_FILES:
        text = _read(org_dir / name)
        if text:
            blocks.append(_Block(name, _AUTHORITATIVE, AUTHORITATIVE_BANNER, text))

    # TEAM DEFAULT — optional middle tier refinement.
    if team_dir is not None:
        text = _read(team_dir / "PLAYBOOK.md")
        if text:
            blocks.append(_Block("PLAYBOOK.md", _DEFAULT, TEAM_BANNER, text))

    # PERSONAL DEFAULT — employee persona + bounded top-k memories.
    if user_dir is not None:
        text = _read(user_dir / "SOUL.md")
        if text:
            blocks.append(_Block("SOUL.md", _DEFAULT, PERSONAL_BANNER, text))
        memories_dir = user_dir / "memories"
        if memories_dir.is_dir():
            mem_files = sorted(p for p in memories_dir.glob("*.md") if p.is_file())
            for path in mem_files[: max(0, layers.employee_memory_top_k)]:
                text = _read(path)
                if text:
                    blocks.append(_Block(path.name, _DEFAULT, PERSONAL_BANNER, text))

    return blocks


def assemble_system_prompt(
    *,
    layers: WorkspaceLayerConfig,
    org_dir: Path | None,
    team_dir: Path | None = None,
    user_dir: Path | None = None,
    legacy_prompt: str = "",
) -> str:
    """Compose a request's effective system prompt from the layered workspace.

    Args:
        layers: The ``WorkspaceLayerConfig`` controlling assembly. When
            ``enabled`` is ``False`` this function returns ``legacy_prompt``
            unchanged (the degenerate single-workspace case — zero behavior
            change for existing deployments).
        org_dir: ORG layer directory (``workspace/<agent>/org``). When ``None``
            or absent on disk, falls back to ``legacy_prompt`` so enabling on an
            un-migrated flat tree is non-breaking.
        team_dir: Optional TEAM layer directory.
        user_dir: Optional EMPLOYEE layer directory.
        legacy_prompt: The flat ``AGENTS.md`` text, used verbatim when layering
            is disabled or no ``org/`` tree exists.

    Returns:
        The merged, sealed-validated system prompt (Phase 1 + Phase 2), or
        ``legacy_prompt`` in the disabled / un-migrated cases.
    """
    if not layers.enabled:
        return legacy_prompt

    if org_dir is None or not org_dir.is_dir():
        # Enabled but no layered tree yet — keep the flat deployment working.
        return legacy_prompt

    # Phase 1 — merge.
    blocks = _merge(org_dir, team_dir, user_dir, layers)

    # Apply masks: a user's masks.yaml suppresses default: blocks ONLY. Org
    # sealed:/authoritative: blocks are immune even if named — authority is
    # data, and a lower layer cannot whiteout a higher one.
    masks = _mask_ids(user_dir)
    if masks:
        blocks = [b for b in blocks if not (b.authority == _DEFAULT and b.block_id in masks)]

    if not blocks:
        # Org dir exists but held nothing readable — don't silently emit "".
        return legacy_prompt

    merged = "\n\n".join(b.render() for b in blocks)

    # Phase 2 — validate. Re-assert sealed rules from policy.yaml that the merge
    # did not already surface (so masks/deletions can't strip a binding rule).
    missing = [rule for rule in _sealed_rules(org_dir, layers) if rule not in merged]
    if missing:
        reasserted = "\n".join(f"- {rule}" for rule in missing)
        merged = f"{merged}\n\n{REASSERT_BANNER}\n{reasserted}"

    return merged
