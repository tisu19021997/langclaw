"""
Workflow progress projection (issue #38).

A workflow runs *inside* the ``workflow_<name>`` tool call, so its phases and
(for Mode 2) its authored body are invisible to the gateway's normal stream of
graph updates. To surface them to a channel, the runtime emits progress *events*
through a request-scoped sink held in a :class:`~contextvars.ContextVar`:

- the gateway installs a sink per inbound message (it has the channel + routing),
  via :func:`set_progress_sink` / :func:`reset_progress_sink`;
- the runtime, deep inside the tool, calls :func:`emit_progress` — no plumbing
  threaded through the agent build.

The sink is synchronous and best-effort: it must not block or raise into the
workflow. A gateway sink typically schedules an ``OutboundMessage`` send and
returns immediately. With no sink installed, :func:`emit_progress` is a no-op.

Event shape (a plain dict):
    {"kind": "phase",    "workflow": str, "run_id": str, "phase": str}
    {"kind": "log",      "workflow": str, "run_id": str, "message": str}
    {"kind": "authored", "workflow": str, "run_id": str, "script": str}
"""

from __future__ import annotations

from collections.abc import Callable
from contextvars import ContextVar, Token
from typing import Any

from loguru import logger

#: Receives a progress event dict. Synchronous and best-effort.
ProgressSink = Callable[[dict[str, Any]], None]

_sink: ContextVar[ProgressSink | None] = ContextVar("workflow_progress_sink", default=None)


def set_progress_sink(sink: ProgressSink) -> Token:
    """Install *sink* for the current async context; returns a reset token."""
    return _sink.set(sink)


def reset_progress_sink(token: Token) -> None:
    """Restore the sink to its prior value (pair with :func:`set_progress_sink`)."""
    _sink.reset(token)


def emit_progress(event: dict[str, Any]) -> None:
    """Send *event* to the installed sink, if any. Never raises."""
    sink = _sink.get()
    if sink is None:
        return
    try:
        sink(event)
    except Exception as exc:  # noqa: BLE001 — progress is best-effort, never fatal
        logger.debug(f"Workflow progress sink raised (ignored): {exc}")


def render_workflow_progress(event: dict[str, Any]) -> str | None:
    """Render a progress *event* as a user-facing line, or ``None`` to skip.

    Channel-agnostic: a gateway sink wraps this in an ``OutboundMessage`` with no
    ``tool_call_id`` so channels show it as a standalone progress line.
    """
    kind = event.get("kind")
    workflow = event.get("workflow", "workflow")
    if kind == "phase":
        return f"⚙️ {workflow}: {event.get('phase', '')}"
    if kind == "log":
        return f"   {workflow}: {event.get('message', '')}"
    if kind == "authored":
        return f"📝 {workflow} — generated script:\n{event.get('script', '')}"
    return None
