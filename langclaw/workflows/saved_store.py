"""
File-backed store for *saved* workflows (runtime authoring).

A saved workflow is just a JavaScript file the agent writes into the workspace
``workflows/`` folder with its ordinary ``write_file`` tool — there is no bespoke
"save" tool. The gateway watches that folder, and any ``<name>.js`` it finds is
loaded as a ``mode="saved"`` :class:`WorkflowSpec` and surfaced as a
``workflow_<name>`` tool (the same surface as an ``@app.workflow``), reloaded on
change and on restart.

The ``.js`` file *is* the source of truth — readable, editable, and
version-controllable. Metadata travels inline as leading JS comments so it stays
a single, valid script:

    // @description Summarize today's top Hacker News posts into the vault.
    // @uses web_fetch, write_file
    const posts = await tools.webFetch({ url: "https://news.ycombinator.com" });
    await tools.output({ result: posts });

- ``name`` is the filename stem; it must match ``[A-Za-z0-9_]+`` (snake_case,
  also the ``workflow_<name>`` tool suffix), which keeps it a single path segment
  that can never escape the folder *and* a clean camelCase identifier inside the
  sandbox (``tools.workflowMyFlow``). Hyphens are rejected — see ``_SAFE_NAME``.
- ``@description`` (optional) becomes the tool description the LLM reads.
- ``@uses`` (optional, comma/space separated) narrows the sandbox capability set;
  omitted ⇒ the body inherits the same read-only ``eval`` PTC allowlist.

This is deliberately **not** the :class:`~langclaw.workflows.authored.ScriptStore`
(per-run, keyed by ``run_id``, in the checkpointer DB). A saved workflow is named
and reusable; the file on disk is canonical.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from loguru import logger

#: A saved workflow name must be a safe single path segment (also a valid
#: ``workflow_<name>`` tool suffix). Restricted to snake_case — letters, digits,
#: and underscore — *no hyphen*: a hyphenated name is un-callable inside a script
#: (``tools.workflow_my-flow`` parses as subtraction) and ``my-flow``/``my_flow``
#: would collapse to the same camelCase identifier. Underscore-only keeps the
#: in-sandbox name (``tools.workflowMyFlow``) unambiguous.
_SAFE_NAME = re.compile(r"^[A-Za-z0-9_]+$")

#: Inline metadata directives, matched on leading ``// @key value`` comment lines.
_DESCRIPTION_RE = re.compile(r"^\s*//\s*@description\s+(.*?)\s*$", re.MULTILINE)
_USES_RE = re.compile(r"^\s*//\s*@uses(?:_tools)?\s+(.*?)\s*$", re.MULTILINE)


@dataclass(slots=True)
class SavedWorkflow:
    """A workflow authored at runtime and persisted to disk.

    Attributes:
        name:        Unique handle (``workflow_<name>`` tool, ``/workflows run``).
        script:      The JavaScript body (the whole file; comments are valid JS),
                     run in the same QuickJS sandbox as ``eval``.
        description: One-line summary parsed from ``// @description``.
        uses_tools:  Tool names parsed from ``// @uses``; empty ⇒ inherit the
                     default eval PTC allowlist.
    """

    name: str
    script: str
    description: str = ""
    uses_tools: list[str] = field(default_factory=list)


def validate_saved_name(name: str) -> str:
    """Return *name* if it is a safe single path segment, else raise ``ValueError``.

    Rejects empty names, path separators, ``..`` traversal, and whitespace — so a
    saved name can never escape the ``workflows/`` folder.
    """
    if not name or not _SAFE_NAME.match(name):
        raise ValueError(
            f"Invalid workflow name {name!r}: use snake_case — letters, digits and "
            "underscores only (e.g. 'hn_ai_digest'). No hyphens, spaces, slashes, or "
            "'..': a hyphen makes the in-sandbox name 'tools.workflow_my-flow' "
            "un-callable."
        )
    return name


def parse_metadata(script: str) -> tuple[str, list[str]]:
    """Extract ``(description, uses_tools)`` from a saved script's ``// @`` header.

    Tolerant: a script with no header yields ``("", [])``. Only the comment
    directives are read — the body is never modified.
    """
    desc_match = _DESCRIPTION_RE.search(script)
    description = desc_match.group(1).strip() if desc_match else ""
    uses_match = _USES_RE.search(script)
    uses_tools: list[str] = []
    if uses_match:
        uses_tools = [t for t in re.split(r"[,\s]+", uses_match.group(1).strip()) if t]
    return description, uses_tools


def render_saved_file(
    script: str, *, description: str = "", uses_tools: list[str] | None = None
) -> str:
    """Compose the canonical on-disk form: a ``// @`` header + the body.

    Used by :meth:`SavedWorkflowStore.save` and mirrors exactly what the prompt
    tells the agent to write, so a file written either way parses identically.
    """
    header: list[str] = []
    if description:
        header.append(f"// @description {description}")
    if uses_tools:
        header.append(f"// @uses {', '.join(uses_tools)}")
    if header:
        return "\n".join(header) + "\n" + script
    return script


class SavedWorkflowStore:
    """Read (and, for tests/programmatic use, write) saved workflows in one directory."""

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
        """Write ``<name>.js`` (header + body), overwriting any same-named file.

        The agent normally writes this file itself with ``write_file``; this method
        is the programmatic equivalent (used by tests and tooling) and emits the
        same canonical format the loader parses.
        """
        validate_saved_name(name)
        self._dir.mkdir(parents=True, exist_ok=True)
        content = render_saved_file(script, description=description, uses_tools=uses_tools)
        (self._dir / f"{name}.js").write_text(content, encoding="utf-8")
        logger.info(f"Saved workflow {name!r} to {self._dir}")
        return SavedWorkflow(
            name=name,
            script=content,
            description=description,
            uses_tools=list(uses_tools or []),
        )

    def load_all(self) -> list[SavedWorkflow]:
        """Return every ``*.js`` workflow, sorted by name. Empty if dir missing.

        A file whose stem is not a valid name is skipped with a warning that
        carries the reason — e.g. a legacy hyphenated ``my-flow.js`` is now
        invalid and the warning tells you to rename it to ``my_flow.js``.
        """
        if not self._dir.is_dir():
            return []
        out: list[SavedWorkflow] = []
        for js in sorted(self._dir.glob("*.js")):
            name = js.stem
            try:
                validate_saved_name(name)
            except ValueError as exc:
                logger.warning(f"Skipping saved workflow file {js.name!r}: {exc}")
                continue
            script = js.read_text(encoding="utf-8")
            description, uses_tools = parse_metadata(script)
            out.append(
                SavedWorkflow(
                    name=name, script=script, description=description, uses_tools=uses_tools
                )
            )
        return out

    def delete(self, name: str) -> bool:
        """Remove ``<name>.js``. Returns ``True`` if a file was deleted."""
        validate_saved_name(name)
        path = self._dir / f"{name}.js"
        if path.exists():
            path.unlink()
            return True
        return False
