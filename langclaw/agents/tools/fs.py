"""Workspace-sandboxed filesystem tools for langclaw agents."""

from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from langchain_core.tools import tool
from loguru import logger

if TYPE_CHECKING:
    from langchain_core.tools import BaseTool


def _safe_resolve(path_str: str, workspace_dir: Path) -> Path | None:
    """Resolve *path_str* relative to *workspace_dir* and return it only if
    the result stays inside the workspace.

    Returns ``None`` for any path-traversal attempt (e.g. ``../../etc/passwd``).

    Args:
        path_str:      Caller-supplied path, may be relative or absolute.
        workspace_dir: Absolute root that all operations must stay within.
    """
    resolved = (workspace_dir / path_str.lstrip("/")).resolve()
    if resolved.is_relative_to(workspace_dir.resolve()):
        return resolved
    return None


def make_move_file_tool(workspace_dir: Path) -> BaseTool:
    """Return a ``move_file`` tool scoped to *workspace_dir*.

    Args:
        workspace_dir: Absolute path to the agent's workspace root.
            All source and destination paths are validated to stay
            inside this directory.
    """

    @tool
    async def move_file(src: str, dst_dir: str) -> dict[str, Any]:
        """Move a file or folder to a different location inside the workspace.

        Both the source and the destination folder must be inside the
        agent's workspace directory — paths that try to escape it
        (e.g. ``../../etc``) are rejected.  Leading slashes are stripped,
        so ``/memories/foo`` and ``memories/foo`` are treated identically.

        Args:
            src:     Relative path to the file or folder to move
                     (e.g. ``"reports/old.txt"`` or ``"memories/til/quiz"``).
            dst_dir: Relative path to the destination folder
                     (e.g. ``"archive"``).  The folder is created if it
                     does not exist.
        """
        src_path = _safe_resolve(src, workspace_dir)
        if src_path is None:
            logger.warning("move_file: path traversal rejected for src={!r}", src)
            return {"error": f"Path '{src}' is outside the workspace directory."}

        dst_path = _safe_resolve(dst_dir, workspace_dir)
        if dst_path is None:
            logger.warning("move_file: path traversal rejected for dst_dir={!r}", dst_dir)
            return {"error": f"Path '{dst_dir}' is outside the workspace directory."}

        if not src_path.exists():
            return {"error": f"Source path does not exist: '{src_path.relative_to(workspace_dir)}'"}

        try:
            dst_path.mkdir(parents=True, exist_ok=True)
            destination = dst_path / src_path.name
            shutil.move(str(src_path), str(destination))
            logger.debug("move_file: moved '{}' → '{}'", src_path, destination)
            return {
                "status": "moved",
                "from": str(src_path.relative_to(workspace_dir)),
                "to": str(destination.relative_to(workspace_dir)),
            }
        except Exception as exc:
            logger.error("move_file: failed to move '{}': {}", src, exc)
            return {"error": f"Failed to move: {exc}"}

    return move_file


def make_delete_file_tool(workspace_dir: Path) -> BaseTool:
    """Return a ``delete_file`` tool scoped to *workspace_dir*.

    Args:
        workspace_dir: Absolute path to the agent's workspace root.
            The deleted file is moved to ``<workspace_dir>/.trash/``
            rather than permanently erased.
    """

    @tool
    async def delete_file(path: str) -> dict[str, Any]:
        """Move a file to the workspace trash folder instead of deleting it permanently.

        IMPORTANT: This tool does NOT permanently delete files.
        The file is moved to the ``.trash/`` folder inside the workspace
        (e.g. ``<workspace>/.trash/filename.20240101_120000.txt``).
        Files in ``.trash/`` can be recovered manually; permanent removal
        requires deleting them from ``.trash/`` outside of the agent.

        The path must be inside the agent's workspace directory — paths
        that try to escape it (e.g. ``../../etc``) are rejected.

        Args:
            path: Relative path to the file to trash
                  (e.g. ``"reports/old.txt"``).
        """
        file_path = _safe_resolve(path, workspace_dir)
        if file_path is None:
            logger.warning("delete_file: path traversal rejected for path={!r}", path)
            return {"error": f"Path '{path}' is outside the workspace directory."}

        if not file_path.exists():
            return {"error": f"File does not exist: '{path}'"}

        if not file_path.is_file():
            return {"error": f"Path is not a file: '{path}'"}

        try:
            trash_dir = workspace_dir / ".trash"
            trash_dir.mkdir(parents=True, exist_ok=True)

            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            stem = file_path.stem
            suffix = file_path.suffix
            trash_name = f"{stem}.{timestamp}{suffix}"
            trash_path = trash_dir / trash_name

            shutil.move(str(file_path), str(trash_path))
            logger.debug("delete_file: trashed '{}' → '{}'", file_path, trash_path)
            return {
                "status": "trashed",
                "original": str(file_path.relative_to(workspace_dir)),
                "trash_path": str(trash_path.relative_to(workspace_dir)),
            }
        except Exception as exc:
            logger.error("delete_file: failed to trash '{}': {}", path, exc)
            return {"error": f"Failed to trash file: {exc}"}

    return delete_file


def make_fs_tools(workspace_dir: Path) -> list[Any]:
    """Return all filesystem tools scoped to *workspace_dir*.

    Args:
        workspace_dir: Absolute path to the agent's workspace root.
    """
    return [make_move_file_tool(workspace_dir), make_delete_file_tool(workspace_dir)]
