"""
Durable step persistence backed by LangGraph's ``BaseStore``.

:mod:`langclaw.workflows.resume` defines the :class:`StepStore` *protocol*
(``get(run_id, step_id)`` / ``put(run_id, step_id, value)``) and a dict-backed
in-process implementation.  This module supplies the **durable** implementation
by adapting LangGraph's namespaced key-value store (``BaseStore``):

- :class:`StoreStepStore` — a ~thin adapter satisfying the ``StepStore`` protocol
  over any ``BaseStore`` (in-memory, SQLite, or Postgres). Namespace is
  ``("workflow_steps", run_id)`` and the key is the deterministic ``step_id``.
- :func:`make_step_store_backend` — a lifecycle-managed backend factory mirroring
  :mod:`langclaw.checkpointer` (abstract base + factory), so dev → prod is one
  config field.

Why the store and not the checkpointer: a workflow is **not** a LangGraph graph,
so it has no channel-state to snapshot — only per-step return values to stash by
key.  ``BaseCheckpointSaver`` persists graph checkpoints keyed by
``(thread_id, checkpoint_ns, checkpoint_id)`` and is the wrong shape; ``BaseStore``
*is* a namespaced KV store, which is exactly the ``StepStore`` contract.

This wires step *persistence*.  The resume *trigger* lives elsewhere: the run
journal (:mod:`langclaw.workflows.run_store`) records each run's status, and
``resume_on_startup`` replays runs left ``"running"`` by a crash via
``WorkflowRuntime.resume_incomplete`` — completed steps replay from this store
and only the unfinished tail re-executes.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from langgraph.store.base import BaseStore

#: LangGraph store namespace prefix for workflow step results. The full
#: namespace is ``(_NAMESPACE_PREFIX, run_id)`` so distinct runs never collide.
_NAMESPACE_PREFIX = "workflow_steps"


class StoreStepStore:
    """Adapt a LangGraph ``BaseStore`` to the workflow ``StepStore`` protocol.

    Each step result is stored at namespace ``("workflow_steps", run_id)`` under
    key ``step_id``.  Values are wrapped as ``{"value": <result>}`` because
    ``BaseStore.aput`` requires a dict — the result itself may be any
    JSON-serializable value (the runtime's memoizer already enforces that).
    """

    def __init__(self, store: BaseStore) -> None:
        self._store = store

    async def get(self, run_id: str, step_id: str) -> tuple[bool, Any]:
        item = await self._store.aget((_NAMESPACE_PREFIX, run_id), step_id)
        if item is None:
            return False, None
        return True, item.value["value"]

    async def put(self, run_id: str, step_id: str, value: Any) -> None:
        await self._store.aput((_NAMESPACE_PREFIX, run_id), step_id, {"value": value})


class StepStoreBackend(ABC):
    """Lifecycle-managed factory for a ``BaseStore`` (use as an async CM).

    Mirrors :class:`langclaw.checkpointer.base.BaseCheckpointerBackend`::

        async with make_step_store_backend("sqlite", db_path=path) as backend:
            step_store = StoreStepStore(backend.get_store())
            runtime = WorkflowRuntime(cfg, step_store=step_store)
    """

    _store: BaseStore | None = None

    @abstractmethod
    async def _open(self) -> BaseStore:
        """Open the underlying storage and return a ready store."""
        ...

    @abstractmethod
    async def _close(self) -> None:
        """Release resources held by the store."""
        ...

    async def __aenter__(self) -> StepStoreBackend:
        self._store = await self._open()
        return self

    async def __aexit__(self, *_: object) -> None:
        await self._close()
        self._store = None

    def get_store(self) -> BaseStore:
        """Return the active store (must be called inside the context manager)."""
        if self._store is None:
            raise RuntimeError("Step store not initialised — use 'async with backend:' first.")
        return self._store


class MemoryStepStoreBackend(StepStoreBackend):
    """In-process ``InMemoryStore`` backend — durable only within one process."""

    async def _open(self) -> BaseStore:
        from langgraph.store.memory import InMemoryStore

        return InMemoryStore()

    async def _close(self) -> None:
        pass


class _ContextManagedStoreBackend(StepStoreBackend):
    """Backend for stores exposed as an async context manager + ``setup()``.

    Covers both ``AsyncSqliteStore`` and ``AsyncPostgresStore``, whose
    ``from_conn_string`` returns an async CM and whose tables are created by an
    idempotent ``setup()`` call.
    """

    def __init__(self, cm_factory: Any) -> None:
        self._cm_factory = cm_factory
        self._cm: AsyncIterator[BaseStore] | None = None

    async def _open(self) -> BaseStore:
        self._cm = self._cm_factory()
        store = await self._cm.__aenter__()
        await store.setup()  # idempotent CREATE TABLE IF NOT EXISTS
        return store

    async def _close(self) -> None:
        if self._cm is not None:
            await self._cm.__aexit__(None, None, None)
            self._cm = None


def make_step_store_backend(
    backend: str,
    *,
    db_path: str = "",
    dsn: str = "",
) -> StepStoreBackend:
    """Return the right :class:`StepStoreBackend` for a config string.

    Args:
        backend: One of ``"memory"``, ``"sqlite"``, or ``"postgres"``.
        db_path: SQLite file path (``~`` expanded).  Use a path *distinct* from
            the checkpointer's DB file to avoid SQLite write-lock contention
            between two connections.
        dsn:     Postgres connection string (shares fine with the checkpointer).

    Raises:
        ValueError: for an unknown backend name.
    """
    if backend == "memory":
        return MemoryStepStoreBackend()
    if backend == "sqlite":
        from langgraph.store.sqlite import AsyncSqliteStore

        path = str(Path(db_path).expanduser())
        return _ContextManagedStoreBackend(lambda: AsyncSqliteStore.from_conn_string(path))
    if backend == "postgres":
        from langgraph.store.postgres import AsyncPostgresStore

        return _ContextManagedStoreBackend(lambda: AsyncPostgresStore.from_conn_string(dsn))
    raise ValueError(
        f"Unknown step store backend: {backend!r}. Choose 'memory', 'sqlite', or 'postgres'."
    )
