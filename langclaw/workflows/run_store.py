"""
Run journal — durable record of workflow run lifecycle, for resume on startup.

The step store (:mod:`langclaw.workflows.step_store`) remembers per-step results
but knows nothing about *which* runs exist or whether they finished.  Resuming a
crashed run on startup needs exactly that: a record of each run's spec, input,
and status.  :class:`StoreRunStore` keeps it over a LangGraph ``BaseStore`` under
namespace ``("workflow_runs",)``, keyed by ``run_id``.

Status transitions:

- ``mark_running`` when a run starts (records spec name + input for replay),
- ``mark_completed`` on success, ``mark_failed`` on a clean exception.

A run left in ``running`` is one whose process died before either terminal mark —
the crash case :meth:`list_incomplete` surfaces for replay.  A clean failure is
``failed`` (excluded), so a deterministically-broken run is not retried forever.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from langgraph.store.base import BaseStore

_NAMESPACE = ("workflow_runs",)
_PAGE = 100


@runtime_checkable
class RunStore(Protocol):
    """Persistence seam for the workflow run journal (resume on startup)."""

    async def mark_running(self, run_id: str, spec_name: str, run_input: Any) -> None: ...

    async def mark_completed(self, run_id: str) -> None: ...

    async def mark_failed(self, run_id: str) -> None: ...

    async def list_incomplete(self) -> list[dict[str, Any]]:
        """Return every run still in ``"running"`` (crashed mid-flight)."""
        ...

    async def list_all(self) -> list[dict[str, Any]]:
        """Return every recorded run (any status), for operator introspection."""
        ...


class StoreRunStore:
    """Run journal over a LangGraph ``BaseStore``.

    A record is ``{"run_id", "spec_name", "input", "status"}`` where ``status``
    is one of ``"running"`` | ``"completed"`` | ``"failed"``.
    """

    def __init__(self, store: BaseStore) -> None:
        self._store = store

    async def mark_running(self, run_id: str, spec_name: str, run_input: Any) -> None:
        await self._store.aput(
            _NAMESPACE,
            run_id,
            {"run_id": run_id, "spec_name": spec_name, "input": run_input, "status": "running"},
        )

    async def mark_completed(self, run_id: str) -> None:
        await self._set_status(run_id, "completed")

    async def mark_failed(self, run_id: str) -> None:
        await self._set_status(run_id, "failed")

    async def _set_status(self, run_id: str, status: str) -> None:
        item = await self._store.aget(_NAMESPACE, run_id)
        record = dict(item.value) if item is not None else {"run_id": run_id}
        record["status"] = status
        await self._store.aput(_NAMESPACE, run_id, record)

    async def list_incomplete(self) -> list[dict[str, Any]]:
        """Return every run still in ``"running"`` (crashed mid-flight)."""
        return [r for r in await self.list_all() if r.get("status") == "running"]

    async def list_all(self) -> list[dict[str, Any]]:
        """Return every recorded run (any status)."""
        out: list[dict[str, Any]] = []
        offset = 0
        while True:
            page = await self._store.asearch(_NAMESPACE, limit=_PAGE, offset=offset)
            out.extend(item.value for item in page)
            if len(page) < _PAGE:
                return out
            offset += _PAGE
