"""
File-backed store for *saved* workflows (runtime authoring).

When a user says "save that workflow", the agent calls the ``save_workflow``
tool with the JavaScript body it just authored for ``eval``.  That body is
persisted here as a named, reusable artifact under the workspace ``workflows/``
folder, then registered as a ``mode="saved"`` :class:`WorkflowSpec` so it boots
(and hot-reloads) as a ``workflow_<name>`` tool — exactly like an operator's
``@app.workflow`` registration, but authored at runtime.

This is deliberately **not** the :class:`~langclaw.workflows.authored.ScriptStore`:
that freezes a body *per run* (keyed by ``run_id``, in the checkpointer DB) so a
resume replays the same script.  A *saved* workflow is reusable and named — the
``.js`` file on disk is the source of truth, readable and editable by the
developer and version-controllable alongside the workspace.

Layout (one workflow → two sibling files, so the body stays a clean ``.js``):

    <workflows_dir>/<name>.js      # the JavaScript body
    <workflows_dir>/<name>.json    # {"description": ..., "uses_tools": [...]}
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from loguru import logger

#: A saved workflow name must be a safe single path segment (also a valid
#: ``workflow_<name>`` tool suffix): letters, digits, underscore, hyphen.
_SAFE_NAME = re.compile(r"^[A-Za-z0-9_-]+$")


@dataclass(slots=True)
class SavedWorkflow:
    """A workflow authored at runtime and persisted to disk.

    Attributes:
        name:        Unique handle (``workflow_<name>`` tool, ``/workflows run``).
        script:      The JavaScript body, run in the same QuickJS sandbox as ``eval``.
        description: One-line summary the LLM reads to decide when to call it.
        uses_tools:  Tool names the body needs (narrows the sandbox capability set).
    """

    name: str
    script: str
    description: str = ""
    uses_tools: list[str] = field(default_factory=list)


def validate_saved_name(name: str) -> str:
    """Return *name* if it is a safe single path segment, else raise ``ValueError``.

    Rejects empty names, path separators, ``..`` traversal, and whitespace — so a
    saved name can never escape the ``workflows/`` folder or collide with the
    ``.js`` / ``.json`` sidecar convention.
    """
    if not name or not _SAFE_NAME.match(name):
        raise ValueError(
            f"Invalid workflow name {name!r}: use only letters, digits, '_' or '-' "
            "(no spaces, slashes, or '..')."
        )
    return name


class SavedWorkflowStore:
    """Persist and load runtime-authored workflows under one directory."""

    def __init__(self, directory: Path | str) -> None:
        self._dir = Path(directory)

    @property
    def directory(self) -> Path:
        return self._dir

    def save(
        self,
        name: str,
        *,
        script: str,
        description: str = "",
        uses_tools: list[str] | None = None,
    ) -> SavedWorkflow:
        """Write *name*'s body + metadata, overwriting any existing same-named one."""
        validate_saved_name(name)
        self._dir.mkdir(parents=True, exist_ok=True)
        (self._dir / f"{name}.js").write_text(script, encoding="utf-8")
        meta = {"description": description, "uses_tools": list(uses_tools or [])}
        (self._dir / f"{name}.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
        logger.info(f"Saved workflow {name!r} to {self._dir}")
        return SavedWorkflow(
            name=name,
            script=script,
            description=description,
            uses_tools=list(uses_tools or []),
        )

    def load_all(self) -> list[SavedWorkflow]:
        """Return every persisted workflow, sorted by name. Empty if dir missing.

        A ``.js`` file with no/broken sidecar still loads (empty description / no
        tools) — the body is the source of truth and must never be silently
        dropped because metadata went stale.
        """
        if not self._dir.is_dir():
            return []
        out: list[SavedWorkflow] = []
        for js in sorted(self._dir.glob("*.js")):
            name = js.stem
            try:
                validate_saved_name(name)
            except ValueError:
                logger.warning(f"Skipping saved workflow with unsafe filename: {js.name}")
                continue
            script = js.read_text(encoding="utf-8")
            description, uses_tools = "", []
            meta_path = self._dir / f"{name}.json"
            if meta_path.is_file():
                try:
                    meta = json.loads(meta_path.read_text(encoding="utf-8"))
                    description = str(meta.get("description", ""))
                    uses_tools = list(meta.get("uses_tools", []))
                except (ValueError, TypeError) as exc:
                    logger.warning(f"Bad metadata for saved workflow {name!r}: {exc}")
            out.append(
                SavedWorkflow(
                    name=name, script=script, description=description, uses_tools=uses_tools
                )
            )
        return out

    def delete(self, name: str) -> bool:
        """Remove *name*'s files. Returns ``True`` if anything was deleted."""
        validate_saved_name(name)
        removed = False
        for suffix in (".js", ".json"):
            path = self._dir / f"{name}{suffix}"
            if path.exists():
                path.unlink()
                removed = True
        return removed
