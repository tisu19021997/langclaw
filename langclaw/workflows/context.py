"""
``WorkflowContext`` — the step surface handed to a workflow body (issue #38).

A workflow body is ``async def (ctx, inp) -> output``.  Every external action
goes through ``ctx`` so the runtime can (a) assign each step a *deterministic*
ID for durable resume, (b) memoize completed steps, (c) bound concurrency and
total step count, and (d) project phase/step boundaries to channels.

The context is deliberately thin: it turns each ``ctx.agent`` / ``ctx.subagent``
/ ``ctx.tool`` call into a :class:`StepRequest` and hands it to an injected
*step executor* coroutine.  In production the executor publishes to the bus and
awaits the reply; in tests it is a plain async function returning canned data.
That seam is what makes the orchestration logic unit-testable without a bus.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

StepExecutor = Callable[["StepRequest"], Awaitable[Any]]
PhaseCallback = Callable[[str], None]


class WorkflowStepError(RuntimeError):
    """Raised when a step fails (e.g. schema validation of an agent reply)."""


class WorkflowBudgetExceeded(RuntimeError):
    """Raised when a run exceeds its step-count budget."""


@dataclass(slots=True)
class StepRequest:
    """One unit of work a workflow body asks the runtime to perform.

    Attributes:
        kind:     ``"agent"`` | ``"subagent"`` | ``"tool"``.
        target:   Agent name, subagent type, or tool name.
        payload:  Prompt string (agent/subagent) or kwargs dict (tool).
        step_id:  Deterministic ID — stable across re-runs of the same body so
                  the memoizer can match a completed step on resume.
        phase:    The active phase label when this step was issued.
        schema:   Optional Pydantic model the result must validate against.
    """

    kind: str
    target: str
    payload: Any
    step_id: str
    phase: str = ""
    schema: type | None = None


class WorkflowContext:
    """Step surface for a workflow body.

    Args:
        executor:     Async callable that performs a :class:`StepRequest`.
        memoize:      Optional async callable ``(StepRequest, runner) -> result``
                      that returns a cached result or runs ``runner`` and
                      persists it (durable resume).  When ``None``, every step
                      executes live.
        phase_cb:     Optional callback invoked on each :meth:`phase` change.
        max_steps:    Step-count backstop for the whole run.
        semaphore:    Shared concurrency limiter for ``parallel`` fan-out.
        _phase / _prefix / _counter: internal deterministic-ID state (also used
                      to spawn child contexts inside :meth:`parallel`).
    """

    def __init__(
        self,
        *,
        executor: StepExecutor,
        memoize: Callable[[StepRequest, Callable[[], Awaitable[Any]]], Awaitable[Any]]
        | None = None,
        phase_cb: PhaseCallback | None = None,
        max_steps: int = 1000,
        semaphore: asyncio.Semaphore | None = None,
        _phase: str = "default",
        _prefix: str = "",
        _counter: list[int] | None = None,
    ) -> None:
        self._executor = executor
        self._memoize = memoize
        self._phase_cb = phase_cb
        self._max_steps = max_steps
        self._semaphore = semaphore or asyncio.Semaphore(8)
        self._phase = _phase
        self._prefix = _prefix
        # Shared mutable step counter so the global backstop counts every step
        # across the run (and across child contexts spawned by parallel()).
        self._counter = _counter if _counter is not None else [0]
        # Per-(context) sequence for deterministic, run-stable step IDs.
        self._seq = 0

    # -- phases -------------------------------------------------------------

    def phase(self, name: str) -> None:
        """Start a new named phase; subsequent steps are grouped under it."""
        self._phase = name
        if self._phase_cb is not None:
            self._phase_cb(name)

    @property
    def current_phase(self) -> str:
        return self._phase

    # -- steps --------------------------------------------------------------

    async def agent(self, name: str, prompt: str, *, schema: type | None = None) -> Any:
        """Run a step against a named agent, returning its reply.

        When *schema* is given the reply is validated against it; a validation
        failure raises :class:`WorkflowStepError`.
        """
        return await self._run_step("agent", name, prompt, schema=schema)

    async def subagent(self, subagent_type: str, prompt: str) -> Any:
        """Delegate to a subagent type, returning its reply."""
        return await self._run_step("subagent", subagent_type, prompt)

    async def tool(self, name: str, **kwargs: Any) -> Any:
        """Invoke a tool by name with keyword arguments."""
        return await self._run_step("tool", name, kwargs)

    async def parallel(
        self, thunks: list[Callable[[WorkflowContext], Awaitable[Any]]]
    ) -> list[Any]:
        """Run *thunks* concurrently, bounded by the run's concurrency budget.

        Each thunk receives its own child context whose step IDs are prefixed by
        the branch index, so IDs stay deterministic across re-runs (required for
        durable resume).  Returns results in input order.
        """
        base = self._next_id()  # consume one slot to anchor the batch deterministically

        async def _run_branch(
            index: int, thunk: Callable[[WorkflowContext], Awaitable[Any]]
        ) -> Any:
            child = WorkflowContext(
                executor=self._executor,
                memoize=self._memoize,
                phase_cb=self._phase_cb,
                max_steps=self._max_steps,
                semaphore=self._semaphore,
                _phase=self._phase,
                _prefix=f"{base}:b{index}/",
                _counter=self._counter,
            )
            async with self._semaphore:
                return await thunk(child)

        return await asyncio.gather(*(_run_branch(i, t) for i, t in enumerate(thunks)))

    # -- internals ----------------------------------------------------------

    def _next_id(self) -> str:
        step_id = f"{self._prefix}{self._phase}#{self._seq}"
        self._seq += 1
        return step_id

    async def _run_step(
        self, kind: str, target: str, payload: Any, *, schema: type | None = None
    ) -> Any:
        self._counter[0] += 1
        if self._counter[0] > self._max_steps:
            raise WorkflowBudgetExceeded(
                f"Workflow exceeded its step budget of {self._max_steps} steps."
            )
        request = StepRequest(
            kind=kind,
            target=target,
            payload=payload,
            step_id=self._next_id(),
            phase=self._phase,
            schema=schema,
        )

        async def _runner() -> Any:
            return await self._executor(request)

        if self._memoize is not None:
            result = await self._memoize(request, _runner)
        else:
            result = await _runner()

        return self._coerce(result, schema)

    @staticmethod
    def _coerce(result: Any, schema: type | None) -> Any:
        if schema is None:
            return result
        try:
            if isinstance(result, schema):
                return result
            if isinstance(result, dict):
                return schema(**result)
            if hasattr(schema, "model_validate"):
                return schema.model_validate(result)
            return schema(result)
        except Exception as exc:  # noqa: BLE001 — surfaced as a step error
            raise WorkflowStepError(
                f"Step result did not match schema {getattr(schema, '__name__', schema)!r}: {exc}"
            ) from exc
