"""Channel-agnostic command router.

Commands (``/start``, ``/reset``, ``/help``, ``/cron``, …) are handled here
instead of inside each channel implementation.  Channels detect commands using
platform conventions (PTB ``CommandHandler`` for Telegram, slash commands for
Discord/Slack, etc.) and delegate execution to the shared ``CommandRouter``.

Commands bypass the message bus entirely — they are fast system operations
that should not pollute conversation history or invoke the LLM.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from langclaw.cron.scheduler import CronManager
    from langclaw.gateway.manager import GatewayManager
    from langclaw.session.manager import SessionManager

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class CommandContext:
    """Everything a command handler needs to execute."""

    channel: str
    user_id: str
    context_id: str
    chat_id: str
    args: list[str] = field(default_factory=list)
    display_name: str = ""


@dataclass
class CommandEntry:
    """A registered command."""

    name: str
    handler: Callable[[CommandContext], Awaitable[str]]
    description: str


# ---------------------------------------------------------------------------
# Built-in command handlers
# ---------------------------------------------------------------------------


async def _cmd_start(ctx: CommandContext) -> str:
    name = ctx.display_name or "there"
    return (
        f"Hi {name}! I'm powered by langclaw.\n\n"
        "Send me a message to get started.\n"
        "Use /reset to start a fresh conversation."
    )


async def _cmd_reset(ctx: CommandContext) -> str:
    router = _ACTIVE_ROUTER
    if router is None or router._session_manager is None:
        return "Reset is not available right now."
    await router._session_manager.delete_thread(
        channel=ctx.channel,
        user_id=ctx.user_id,
        context_id=ctx.context_id,
    )
    return "Conversation reset. Starting fresh!"


async def _cmd_help(ctx: CommandContext) -> str:
    router = _ACTIVE_ROUTER
    if router is None:
        return "Help is not available right now."
    lines: list[str] = []
    for entry in router.list_commands():
        lines.append(f"/{entry.name}  — {entry.description}")
    return "\n".join(lines) or "No commands registered."


async def _cmd_cron(ctx: CommandContext) -> str:
    router = _ACTIVE_ROUTER
    if router is None or router._cron_manager is None:
        return "Cron is not available."

    cron_mgr = router._cron_manager
    sub = ctx.args[0] if ctx.args else "list"

    if sub == "list":
        jobs = await cron_mgr.list_jobs(
            channel=ctx.channel or None,
            user_id=ctx.user_id or None,
        )
        if not jobs:
            return "No active cron jobs."
        lines = ["Active cron jobs:"]
        for j in jobs:
            lines.append(f"  [{j.id}] {j.name!r} — {j.schedule}")
        return "\n".join(lines)

    if sub == "remove":
        if len(ctx.args) < 2:
            return "Usage: /cron remove <job_id>"
        job_id = ctx.args[1]
        removed = await cron_mgr.remove_job(
            job_id,
            channel=ctx.channel or None,
            user_id=ctx.user_id or None,
        )
        if removed:
            return f"Job {job_id} removed."
        return f"Job {job_id} not found."

    return "Usage: /cron [list | remove <job_id>]"


def _tail_log_file(path: Path, n: int = 50, level_filter: str | None = None) -> str:
    """Return the last *n* lines of *path*, optionally filtered by log level."""
    try:
        lines = path.read_text("utf-8").splitlines()
    except OSError as exc:
        return f"Error reading log file: {exc}"
    if level_filter:
        lines = [line for line in lines if f"| {level_filter.upper()}" in line]
    tail = lines[-n:]
    return "\n".join(tail) or "(no matching entries)"


async def _cmd_agentsmd(ctx: CommandContext) -> str:
    router = _ACTIVE_ROUTER
    if router is None or router._gateway_manager is None:
        return "agentsmd command is not available."

    gw = router._gateway_manager
    args = ctx.args

    # /agentsmd [reload] [agent_name]
    reload_mode = False
    agent_name = "default"
    if args:
        if args[0] == "reload":
            reload_mode = True
            agent_name = args[1] if len(args) > 1 else "default"
        else:
            agent_name = args[0]

    if reload_mode:
        gw.invalidate_agent_hash(agent_name)
        return f"Hash cleared for agent '{agent_name}'. Rebuild will happen on next message."

    path = gw.get_agents_md_path(agent_name)
    raw_hash = gw._agents_md_hashes.get(agent_name, "(not yet loaded)")
    short_hash = raw_hash[:12] + "…" if isinstance(raw_hash, str) and len(raw_hash) > 12 else raw_hash

    if not path.exists():
        return f"AGENTS.md not found at: {path}"

    content = path.read_text("utf-8")
    header = f"AGENTS.md — agent: {agent_name}\nPath: {path}\nHash: {short_hash}\n\n"
    return header + content


async def _cmd_logs(ctx: CommandContext) -> str:
    router = _ACTIVE_ROUTER
    if router is None or router._workspace_dir is None:
        return "logs command is not available."

    log_dir = router._workspace_dir / "logs"
    args = ctx.args

    n = 50
    level_filter: str | None = None
    date_str: str | None = None

    if args:
        arg = args[0]
        if arg.lower() == "error":
            level_filter = "ERROR"
        elif re.match(r"^\d{4}-\d{2}-\d{2}$", arg):
            date_str = arg
        elif arg.isdigit():
            n = int(arg)

    if date_str is None:
        date_str = datetime.now().strftime("%Y-%m-%d")

    log_file = log_dir / f"{date_str}.log"
    if not log_file.exists():
        return f"No log file found: {log_file}"

    content = _tail_log_file(log_file, n=n, level_filter=level_filter)
    header = f"Log: {log_file.name}"
    if level_filter:
        header += f" (filter: {level_filter})"
    header += f" — last {n} lines\n\n"
    return header + f"```\n{content}\n```"


async def _cmd_file(ctx: CommandContext) -> str:
    router = _ACTIVE_ROUTER
    if router is None or router._workspace_dir is None:
        return "file command is not available."

    workspace = router._workspace_dir
    args = ctx.args

    # No args → list workspace root
    if not args:
        try:
            entries = sorted(workspace.iterdir(), key=lambda p: (p.is_file(), p.name))
            lines = [f"{'[dir] ' if p.is_dir() else '      '}{p.name}" for p in entries]
            return f"Workspace: {workspace}\n\n" + ("\n".join(lines) or "(empty)")
        except OSError as exc:
            return f"Error listing workspace: {exc}"

    rel_path = args[0]
    n: int | None = None
    if len(args) > 1 and args[1].isdigit():
        n = int(args[1])

    # Resolve and jail to workspace
    try:
        workspace_resolved = workspace.resolve()
        target = (workspace / rel_path).resolve()
        target.relative_to(workspace_resolved)  # raises ValueError if outside
    except ValueError:
        return "Error: path escapes the workspace directory."
    except OSError as exc:
        return f"Error resolving path: {exc}"

    if not target.exists():
        return f"File not found: {rel_path}"

    if target.is_dir():
        try:
            entries = sorted(target.iterdir(), key=lambda p: (p.is_file(), p.name))
            lines = [f"{'[dir] ' if p.is_dir() else '      '}{p.name}" for p in entries]
            return f"Directory: {target.relative_to(workspace_resolved)}\n\n" + ("\n".join(lines) or "(empty)")
        except OSError as exc:
            return f"Error listing directory: {exc}"

    try:
        content_lines = target.read_text("utf-8").splitlines()
    except OSError as exc:
        return f"Error reading file: {exc}"

    if n is not None:
        content_lines = content_lines[-n:]

    header = f"File: {target.relative_to(workspace_resolved)}"
    if n is not None:
        header += f" (last {n} lines)"
    header += "\n\n"
    return header + "\n".join(content_lines)


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

_ACTIVE_ROUTER: CommandRouter | None = None


class CommandRouter:
    """Registry of channel-agnostic commands.

    Created by ``GatewayManager`` and shared with every channel via
    ``BaseChannel.set_command_router()``.
    """

    def __init__(
        self,
        session_manager: SessionManager,
        cron_manager: CronManager | None = None,
        gateway_manager: GatewayManager | None = None,
        workspace_dir: Path | None = None,
    ) -> None:
        self._session_manager = session_manager
        self._cron_manager = cron_manager
        self._gateway_manager = gateway_manager
        self._workspace_dir = workspace_dir
        self._commands: dict[str, CommandEntry] = {}
        self._register_builtins()

        global _ACTIVE_ROUTER  # noqa: PLW0603
        _ACTIVE_ROUTER = self

    def _register_builtins(self) -> None:
        self.register("start", _cmd_start, "say hello")
        self.register("reset", _cmd_reset, "clear conversation history")
        self.register("help", _cmd_help, "show this message")
        if self._cron_manager is not None:
            self.register("cron", _cmd_cron, "list or remove cron jobs")
        if self._gateway_manager is not None:
            self.register("agentsmd", _cmd_agentsmd, "view or reload AGENTS.md [reload] [agent]")
            self.register("logs", _cmd_logs, "tail log file [n|error|YYYY-MM-DD]")
        if self._workspace_dir is not None:
            self.register("file", _cmd_file, "read workspace file [path] [n lines]")

    def register(
        self,
        name: str,
        handler: Callable[[CommandContext], Awaitable[str]],
        description: str,
    ) -> None:
        """Register a command handler."""
        self._commands[name] = CommandEntry(
            name=name,
            handler=handler,
            description=description,
        )

    async def dispatch(
        self,
        name: str,
        ctx: CommandContext,
    ) -> str:
        """Execute a command by name. Returns response text."""
        entry = self._commands.get(name)
        if entry is None:
            return f"Unknown command: /{name}"
        try:
            return await entry.handler(ctx)
        except Exception:
            logger.exception("Command /%s failed", name)
            return f"Command /{name} failed. Please try again."

    def list_commands(self) -> list[CommandEntry]:
        """Return all registered commands in insertion order."""
        return list(self._commands.values())


__all__ = ["CommandContext", "CommandEntry", "CommandRouter", "_tail_log_file"]
