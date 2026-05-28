"""WorkflowRunner — executes registered workflows over the gateway.

The runner is the bridge between a triggered :class:`InboundMessage` (carrying
``metadata["workflow_name"]``) and a registered workflow function. It builds a
:class:`WorkflowContext`, wires its ``run``/``emit`` callbacks to the live agent
map and originating channel, runs the function, and delivers the final result.

Delivery depends on *who* spawned the workflow:

  - **Human-triggered** (``/workflow``) or **cron** — the final result is sent
    straight to the originating channel as an ordinary ``OutboundMessage``.
  - **Agent-spawned** (``metadata["notify_agent"]`` set by the ``run_workflow``
    / ``orchestrate`` tools) — the result is persisted (optionally to a file)
    and a completion ``InboundMessage`` is published back to the bus *to the
    agent*, so the agent is notified ("✅ workflow done, result at …") and can
    relay or act on it. The completion message deliberately omits
    ``workflow_name`` so it is never re-dispatched as a new workflow.

Progress always streams to the channel as ``tool_progress`` regardless of who
triggered the run — every channel renders it through the same path it already
uses for tool progress, so no channel changes are required.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from loguru import logger

from langclaw.bus.base import InboundMessage, OutboundMessage
from langclaw.gateway.base import BaseChannel
from langclaw.workflows.context import WorkflowContext

# Provided by the gateway: run a prompt on a named agent for a given inbound
# message and return the agent's final text reply.
AgentRunner = Callable[[str, str, InboundMessage], Awaitable[str]]

# Provided by the gateway: publish a message back onto the bus (``bus.publish``).
Notifier = Callable[[InboundMessage], Awaitable[None]]


class WorkflowRunner:
    """Owns the workflow registry and runs workflows on demand."""

    def __init__(
        self,
        specs: dict[str, dict[str, Any]],
        *,
        run_agent: AgentRunner,
        notify: Notifier | None = None,
        result_dir: Path | None = None,
        max_concurrency: int = 8,
    ) -> None:
        """Initialise the runner.

        Args:
            specs:           Mapping of workflow name -> spec dict with keys
                             ``name``, ``description``, ``fn``.
            run_agent:       Callback that runs a prompt on a named agent.
            notify:          Optional ``bus.publish`` used to send a completion
                             message back to the agent for agent-spawned runs.
            result_dir:      Optional directory; when set, an agent-spawned
                             workflow's full result is written here and the
                             completion notice references the file path.
            max_concurrency: Ceiling on concurrent agent calls across all of a
                             single workflow run's ``run`` invocations.
        """
        self._specs = specs
        self._run_agent = run_agent
        self._notify = notify
        self._result_dir = result_dir
        self._semaphore = asyncio.Semaphore(max_concurrency)

    def __contains__(self, name: str) -> bool:
        return name in self._specs

    def names(self) -> list[str]:
        return list(self._specs)

    def describe(self, name: str) -> str:
        return self._specs.get(name, {}).get("description", "")

    async def dispatch(
        self,
        name: str,
        msg: InboundMessage,
        channel: BaseChannel,
    ) -> None:
        """Run workflow ``name`` for ``msg`` and deliver its output.

        Progress streams to ``channel``; the final result is either sent to the
        channel (human/cron trigger) or routed back to the agent as a completion
        notification (agent trigger — ``metadata["notify_agent"]``).
        """
        spec = self._specs.get(name)
        if spec is None:
            logger.warning(f"No workflow registered for '{name}' — dropping message.")
            return

        async def emit(content: str, phase: str) -> None:
            await channel.send(
                OutboundMessage(
                    channel=msg.channel,
                    user_id=msg.user_id,
                    context_id=msg.context_id,
                    chat_id=msg.chat_id,
                    content=content,
                    type="tool_progress",
                    metadata={"workflow": name, "phase": phase},
                )
            )

        async def run_agent(agent_name: str, prompt: str) -> str:
            async with self._semaphore:
                return await self._run_agent(agent_name, prompt, msg)

        ctx = WorkflowContext(
            input=msg.content,
            metadata=msg.metadata or {},
            run_agent=run_agent,
            emit=emit,
        )

        logger.info(f"Workflow start | name={name} channel={msg.channel} user={msg.user_id}")
        try:
            result = await spec["fn"](ctx)
        except Exception:
            logger.exception(f"Workflow '{name}' failed")
            await self._deliver_failure(name, msg, channel)
            return

        logger.info(f"Workflow done | name={name}")
        await self._deliver_result(name, str(result or ""), msg, channel)

    # ------------------------------------------------------------------
    # Delivery
    # ------------------------------------------------------------------

    def _wants_agent_callback(self, msg: InboundMessage) -> bool:
        """True when the run was agent-spawned and a notifier is available."""
        return bool((msg.metadata or {}).get("notify_agent")) and self._notify is not None

    async def _deliver_result(
        self, name: str, result: str, msg: InboundMessage, channel: BaseChannel
    ) -> None:
        if self._wants_agent_callback(msg):
            await self._notify_agent(name, result, msg)
            return
        # Human/cron trigger: deliver straight to the channel (empty = silent).
        if result:
            await channel.send(self._ai_message(name, result, msg))

    async def _deliver_failure(self, name: str, msg: InboundMessage, channel: BaseChannel) -> None:
        notice = f"Workflow '{name}' failed. Please try again."
        if self._wants_agent_callback(msg):
            await self._notify(self._completion_message(name, notice, msg, result_path=None))
            return
        await channel.send(self._ai_message(name, notice, msg))

    async def _notify_agent(self, name: str, result: str, msg: InboundMessage) -> None:
        """Persist the result and publish a completion message to the agent."""
        path = self._persist_result(name, result)
        if path is not None:
            notice = (
                f"✅ Workflow '{name}' finished. Full result saved to: {path}\n\n"
                "Read that file if you need the details, then update the user."
            )
        else:
            notice = f"✅ Workflow '{name}' finished. Result:\n\n{result}"
        await self._notify(self._completion_message(name, notice, msg, result_path=path))

    def _persist_result(self, name: str, result: str) -> Path | None:
        """Write *result* to ``result_dir`` and return the path (or None)."""
        if self._result_dir is None or not result:
            return None
        try:
            self._result_dir.mkdir(parents=True, exist_ok=True)
            path = self._result_dir / f"{name}-{uuid.uuid4().hex[:8]}.md"
            path.write_text(result, encoding="utf-8")
            return path
        except OSError as exc:
            logger.error(f"Failed to persist workflow result for '{name}': {exc}")
            return None

    def _completion_message(
        self, name: str, content: str, msg: InboundMessage, *, result_path: Path | None
    ) -> InboundMessage:
        """Build the agent-bound completion message.

        Crucially omits ``workflow_name`` so ``_handle`` routes it to the agent
        instead of dispatching it as a new workflow. Carries ``_depth`` forward
        so the recursion guard keeps counting across the callback.
        """
        return InboundMessage(
            channel=msg.channel,
            user_id=msg.user_id,
            context_id=msg.context_id,
            chat_id=msg.chat_id,
            content=content,
            origin="workflow",
            to="agent",
            metadata={
                "workflow": name,
                "workflow_result_path": str(result_path) if result_path else "",
                "_depth": (msg.metadata or {}).get("_depth", 0),
            },
        )

    def _ai_message(self, name: str, content: str, msg: InboundMessage) -> OutboundMessage:
        return OutboundMessage(
            channel=msg.channel,
            user_id=msg.user_id,
            context_id=msg.context_id,
            chat_id=msg.chat_id,
            content=content,
            type="ai",
            metadata={"workflow": name},
        )
