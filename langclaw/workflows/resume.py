"""
Durable resume — per-step memoization (issue #38, Phase 1).

A workflow run is identified by ``run_id``; each step within it has a
deterministic ``step_id`` (see :mod:`langclaw.workflows.context`).  The
:class:`StepMemoizer` persists each completed step's result keyed by
``(run_id, step_id)``.  On resume the body re-executes from the top, but every
already-completed step returns its cached result instantly instead of re-running
the underlying agent/tool — so a process restart costs only the unfinished tail.

The persistence backend is abstracted behind :class:`StepStore`.  An
in-memory implementation ships here for tests; the production wiring stores
results via the existing checkpointer under ``thread_id = workflow:{name}:{run_id}``
(deferred to the bus-integration slice).
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any, Protocol, runtime_checkable

from langclaw.workflows.context import StepRequest


@runtime_checkable
class StepStore(Protocol):
    """Persistence seam for completed workflow steps."""

    async def get(self, run_id: str, step_id: str) -> tuple[bool, Any]:
        """Return ``(hit, value)`` — ``hit`` is ``False`` when not stored."""
        ...

    async def put(self, run_id: str, step_id: str, value: Any) -> None:
        """Persist *value* for ``(run_id, step_id)``."""
        ...


class InMemoryStepStore:
    """A dict-backed :class:`StepStore` for tests and single-process runs."""

    def __init__(self) -> None:
        self._data: dict[tuple[str, str], Any] = {}

    async def get(self, run_id: str, step_id: str) -> tuple[bool, Any]:
        key = (run_id, step_id)
        if key in self._data:
            return True, self._data[key]
        return False, None

    async def put(self, run_id: str, step_id: str, value: Any) -> None:
        self._data[(run_id, step_id)] = value

    def __len__(self) -> int:
        return len(self._data)


class StepMemoizer:
    """Wraps step execution with cached-result replay for one run.

    Bind to a ``run_id`` and pass :meth:`wrap` as the ``memoize`` callable of a
    :class:`~langclaw.workflows.context.WorkflowContext`.
    """

    def __init__(self, store: StepStore, run_id: str) -> None:
        self._store = store
        self._run_id = run_id

    async def wrap(self, request: StepRequest, runner: Callable[[], Awaitable[Any]]) -> Any:
        """Return the cached result for *request*, or run and persist it."""
        hit, value = await self._store.get(self._run_id, request.step_id)
        if hit:
            return value
        result = await runner()
        await self._store.put(self._run_id, request.step_id, _ensure_serializable(result))
        return result


def _ensure_serializable(value: Any) -> Any:
    """Best-effort guard that a step result can survive a durable round-trip.

    Pydantic models are dumped to dicts; everything else must already be
    JSON-serializable.  Non-serializable results raise early (at persist time)
    with a clear message rather than corrupting the resume journal.
    """
    if hasattr(value, "model_dump"):
        return value.model_dump()
    try:
        json.dumps(value)
    except (TypeError, ValueError) as exc:
        raise TypeError(
            "Workflow step result is not JSON-serializable and cannot be "
            f"persisted for resume: {type(value).__name__}. Return a dict, "
            "Pydantic model, or other JSON-compatible value."
        ) from exc
    return value
