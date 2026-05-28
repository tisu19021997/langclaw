"""WorkflowContext — the orchestration surface handed to every workflow.

A langclaw workflow is an ordinary ``async def`` function::

    @app.workflow("triage", description="Classify then route an issue")
    async def triage(ctx: WorkflowContext) -> str:
        await ctx.phase("Classify")
        label = await ctx.run(f"Classify this issue: {ctx.input}", agent="classifier")

        await ctx.phase("Draft")
        return await ctx.run(f"Write a reply for a '{label}' issue: {ctx.input}")

Because the body is plain Python, *dynamic* structure — loops, conditionals,
fan-out sized at runtime — is free. There is no sandbox and no custom
mini-language; the primitives below only exist to (a) delegate work to the
app's registered named agents and (b) stream progress back to the user over
the same bus path that tools already use.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Iterable, Sequence
from typing import Any

# A stage receives (previous_result, original_item, index) and returns the next
# value — mirrors the no-barrier pipeline contract used elsewhere in the stack.
Stage = Callable[[Any, Any, int], Awaitable[Any]]

# Injected by the runner: run a prompt on a named agent, return its text reply.
AgentRunner = Callable[[str, str], Awaitable[str]]

# Injected by the runner: emit a progress line to the originating channel.
ProgressEmitter = Callable[[str, str], Awaitable[None]]


class WorkflowContext:
    """The single argument passed to a workflow function.

    Attributes:
        input:    Free-text payload the workflow was triggered with (the text
                  after ``/workflow <name>``, or a cron job's message).
        metadata: Inbound message metadata (``agent_name``, ``user_role``, …).
        phase_title: The most recently announced phase, or ``""``.
    """

    def __init__(
        self,
        *,
        input: str,
        metadata: dict[str, Any],
        run_agent: AgentRunner,
        emit: ProgressEmitter,
        default_agent: str = "default",
    ) -> None:
        self.input = input
        self.metadata = metadata
        self.phase_title = ""
        self._run_agent = run_agent
        self._emit = emit
        self._default_agent = default_agent

    # ------------------------------------------------------------------
    # Progress — rides OutboundMessage(type="tool_progress") to the channel
    # ------------------------------------------------------------------

    async def phase(self, title: str) -> None:
        """Announce a new phase. Shown as a progress header in the channel."""
        self.phase_title = title
        await self._emit(f"▸ {title}", title)

    async def log(self, message: str) -> None:
        """Emit a freeform progress line under the current phase."""
        await self._emit(message, self.phase_title)

    # ------------------------------------------------------------------
    # Delegation — every unit of LLM work goes through a registered agent
    # ------------------------------------------------------------------

    async def run(self, prompt: str, *, agent: str | None = None) -> str:
        """Run ``prompt`` on a named agent and return its text reply.

        Each call executes on a fresh, isolated conversation thread, so
        workflow steps never pollute one another's history.

        Args:
            prompt: The instruction for the agent.
            agent:  Registered named-agent key. ``None`` uses the main agent.

        Returns:
            The agent's final user-facing text.
        """
        target = agent or self._default_agent
        await self._emit(f"⚙ {target}: {_clip(prompt)}", self.phase_title)
        return await self._run_agent(target, prompt)

    # ------------------------------------------------------------------
    # Composition — barrier (parallel) and no-barrier (pipeline)
    # ------------------------------------------------------------------

    async def parallel(self, awaitables: Iterable[Awaitable[Any]]) -> list[Any]:
        """Run awaitables concurrently and return results in order.

        This is a barrier: it waits for every awaitable before returning. Use
        it when a later step genuinely needs all results together::

            drafts = await ctx.parallel([
                ctx.run(f"Draft section: {s}", agent="writer") for s in sections
            ])
        """
        return await asyncio.gather(*awaitables)

    async def pipeline(self, items: Sequence[Any], *stages: Stage) -> list[Any]:
        """Stream each item through all stages independently (no barrier).

        Item A can be in stage 2 while item B is still in stage 1 — wall-clock
        is the slowest single chain, not the sum of per-stage slowest items.
        Each stage is ``async (prev_result, original_item, index) -> next``.
        A stage that raises drops that item to ``None`` and skips the rest.
        """

        async def _chain(item: Any, index: int) -> Any:
            result: Any = item
            for stage in stages:
                try:
                    result = await stage(result, item, index)
                except Exception:  # noqa: BLE001 — isolate one item's failure
                    return None
            return result

        return await asyncio.gather(*(_chain(it, i) for i, it in enumerate(items)))


def _clip(text: str, limit: int = 80) -> str:
    """Trim a prompt for a one-line progress message."""
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"
