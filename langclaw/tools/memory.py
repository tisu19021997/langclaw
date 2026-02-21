"""
MemoryTool — persistent memory for langclaw agents.

The agent uses this tool to read and write files in the memories directory,
allowing it to accumulate knowledge across conversations.

All paths are scoped to the configured ``memories_dir``; traversal attempts
(``..``, ``~``, absolute paths that escape the directory) raise ``ValueError``.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# System prompt fragment injected into the agent
# ---------------------------------------------------------------------------

MEMORY_SYSTEM_PROMPT = """\
## Memory
You have a persistent memory directory. ALWAYS check it before starting any task.

Protocol:
1. Call the `memory` tool with command "view" on "/memories" at the very start.
2. As you work, write down useful context: user preferences, ongoing project state,
   decisions made, or anything that would help you pick up where you left off.
3. Keep memory files tidy — update or delete stale files rather than accumulating clutter.
4. Never store secrets (API keys, passwords, tokens) in memory.

Memory is NOT a conversation log. Store facts and state, not dialogue.
"""

# ---------------------------------------------------------------------------
# Input schema
# ---------------------------------------------------------------------------


class MemoryInput(BaseModel):
    """Input schema for the memory tool.

    Exactly one command is executed per call. Fields irrelevant to the chosen
    command are ignored.
    """

    command: str = Field(
        description=(
            "Action to perform. One of: "
            "view, create, str_replace, insert, delete, rename."
        )
    )
    path: str = Field(
        default="",
        description=(
            "Virtual path within /memories (e.g. '/memories/notes.txt'). "
            "Required for all commands except rename."
        ),
    )
    file_text: str | None = Field(
        default=None,
        description="File content. Required for: create.",
    )
    old_str: str | None = Field(
        default=None,
        description="Exact text to replace. Required for: str_replace.",
    )
    new_str: str | None = Field(
        default=None,
        description="Replacement text. Required for: str_replace.",
    )
    insert_line: int | None = Field(
        default=None,
        description="0-based line index to insert before. Required for: insert.",
    )
    insert_text: str | None = Field(
        default=None,
        description="Text to insert. Required for: insert.",
    )
    old_path: str | None = Field(
        default=None,
        description="Source path. Required for: rename.",
    )
    new_path: str | None = Field(
        default=None,
        description="Destination path. Required for: rename.",
    )
    view_range: list[int] | None = Field(
        default=None,
        description="[start_line, end_line] (1-indexed) to limit view output.",
    )


# ---------------------------------------------------------------------------
# Tool implementation
# ---------------------------------------------------------------------------


class MemoryTool(BaseTool):
    """Read and write persistent memory files for the agent.

    All operations are scoped to ``memories_dir``; path traversal is blocked.
    """

    name: str = "memory"
    description: str = (
        "Read and write persistent memory files so you can remember information "
        "across conversations. Supported commands: view, create, str_replace, "
        "insert, delete, rename. Always view /memories before starting a task."
    )
    args_schema: type[BaseModel] = MemoryInput

    memories_dir: Path

    model_config = {"arbitrary_types_allowed": True}

    # ------------------------------------------------------------------
    # Path helpers
    # ------------------------------------------------------------------

    def _resolve(self, path: str) -> Path:
        """Resolve a virtual /memories/* path to a real filesystem path.

        Raises ``ValueError`` for traversal attempts or paths that escape
        ``memories_dir``.
        """
        if not path:
            raise ValueError("Path must not be empty.")
        # Normalise: treat anything under /memories as relative
        vpath = path if path.startswith("/") else "/" + path
        if ".." in vpath or vpath.startswith("~"):
            raise ValueError("Path traversal is not allowed.")

        # Strip leading /memories prefix if present so callers can use either
        # /memories/file.txt or /file.txt
        if vpath.startswith("/memories"):
            relative = vpath[len("/memories") :].lstrip("/")
        else:
            relative = vpath.lstrip("/")

        full = (
            (self.memories_dir / relative).resolve()
            if relative
            else self.memories_dir.resolve()
        )

        try:
            full.relative_to(self.memories_dir.resolve())
        except ValueError:
            raise ValueError(f"Path '{path}' escapes the memories directory.") from None

        return full

    # ------------------------------------------------------------------
    # Commands
    # ------------------------------------------------------------------

    def _view(self, path: str, view_range: list[int] | None) -> str:
        full = self._resolve(path)
        if not full.exists():
            return f"The path {path} does not exist. Please provide a valid path."

        if full.is_dir():
            items = sorted(full.iterdir(), key=lambda p: p.name)
            lines = [f"Directory listing for {path}:"]
            for item in items:
                if item.name.startswith("."):
                    continue
                suffix = "/" if item.is_dir() else ""
                try:
                    size = item.stat().st_size
                    lines.append(f"  {size:>8}  {item.name}{suffix}")
                except OSError:
                    lines.append(f"           {item.name}{suffix}")
            return "\n".join(lines) if len(lines) > 1 else f"{path} is empty."

        # File view
        try:
            content = full.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            return f"Error reading {path}: {exc}"

        all_lines = content.splitlines()
        if view_range:
            start = max(1, view_range[0]) - 1
            end = view_range[1] if view_range[1] != -1 else len(all_lines)
            selected = all_lines[start:end]
            start_num = start + 1
        else:
            selected = all_lines
            start_num = 1

        numbered = [f"{i + start_num:6d}\t{line}" for i, line in enumerate(selected)]
        header = f"Here's the content of {path} with line numbers:\n"
        return header + "\n".join(numbered)

    def _create(self, path: str, file_text: str) -> str:
        full = self._resolve(path)
        if full.exists():
            return f"Error: File {path} already exists."
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(file_text, encoding="utf-8")
        return f"File created successfully at: {path}"

    def _str_replace(self, path: str, old_str: str, new_str: str) -> str:
        full = self._resolve(path)
        if not full.is_file():
            return (
                f"Error: The path {path} does not exist. Please provide a valid path."
            )

        content = full.read_text(encoding="utf-8")
        count = content.count(old_str)
        if count == 0:
            return (
                f"No replacement was performed, old_str `{old_str}` "
                f"did not appear verbatim in {path}."
            )
        if count > 1:
            lines = [
                str(i + 1)
                for i, line in enumerate(content.splitlines())
                if old_str in line
            ]
            return (
                f"No replacement was performed. Multiple occurrences of old_str "
                f"`{old_str}` in lines: {', '.join(lines)}. Please ensure it is unique."
            )

        new_content = content.replace(old_str, new_str, 1)
        full.write_text(new_content, encoding="utf-8")
        snippet_lines = new_content.splitlines()
        snippet = "\n".join(f"{i+1:6d}\t{line}" for i, line in enumerate(snippet_lines[:20]))
        return f"The memory file has been edited.\n{snippet}"

    def _insert(self, path: str, insert_line: int, insert_text: str) -> str:
        full = self._resolve(path)
        if not full.is_file():
            return f"Error: The path {path} does not exist."

        lines = full.read_text(encoding="utf-8").splitlines()
        n = len(lines)
        if insert_line < 0 or insert_line > n:
            return (
                f"Error: Invalid `insert_line` parameter: {insert_line}. "
                f"It should be within the range of lines of the file: [0, {n}]"
            )

        lines.insert(insert_line, insert_text.rstrip("\n"))
        full.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return f"The file {path} has been edited."

    def _delete(self, path: str) -> str:
        full = self._resolve(path)
        if not full.exists():
            return f"Error: The path {path} does not exist."
        if full == self.memories_dir.resolve():
            return "Error: Cannot delete the root memories directory."

        if full.is_file():
            full.unlink()
        else:
            shutil.rmtree(full)
        return f"Successfully deleted {path}"

    def _rename(self, old_path: str, new_path: str) -> str:
        old_full = self._resolve(old_path)
        new_full = self._resolve(new_path)
        if not old_full.exists():
            return f"Error: The path {old_path} does not exist."
        if new_full.exists():
            return f"Error: The destination {new_path} already exists."
        new_full.parent.mkdir(parents=True, exist_ok=True)
        old_full.rename(new_full)
        return f"Successfully renamed {old_path} to {new_path}"

    # ------------------------------------------------------------------
    # BaseTool interface
    # ------------------------------------------------------------------

    def _run(
        self,
        command: str,
        path: str = "",
        file_text: str | None = None,
        old_str: str | None = None,
        new_str: str | None = None,
        insert_line: int | None = None,
        insert_text: str | None = None,
        old_path: str | None = None,
        new_path: str | None = None,
        view_range: list[int] | None = None,
        **_: Any,
    ) -> str:
        self.memories_dir.mkdir(parents=True, exist_ok=True)

        match command:
            case "view":
                return self._view(path or "/memories", view_range)
            case "create":
                if file_text is None:
                    return "Error: `file_text` is required for create."
                return self._create(path, file_text)
            case "str_replace":
                if old_str is None or new_str is None:
                    return (
                        "Error: `old_str` and `new_str` are required for str_replace."
                    )
                return self._str_replace(path, old_str, new_str)
            case "insert":
                if insert_line is None or insert_text is None:
                    return "Error: `insert_line` and `insert_text` are required for insert."
                return self._insert(path, insert_line, insert_text)
            case "delete":
                return self._delete(path)
            case "rename":
                if old_path is None or new_path is None:
                    return "Error: `old_path` and `new_path` are required for rename."
                return self._rename(old_path, new_path)
            case _:
                return (
                    f"Unknown command '{command}'. "
                    "Valid commands: view, create, str_replace, insert, delete, rename."
                )
