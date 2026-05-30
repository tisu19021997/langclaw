"""
Mode 2 (``llm_authored``) — persist the LLM-authored body so resume replays it
(issue #38, Phase 3).

A ``@app.workflow(mode="llm_authored")`` declares the *typed contract*, budget,
and capability allowlist; the LLM authors the *body* (a PTC script) on first
fire.  For the run to stay deterministic across a process restart — and for
cron-fired runs to be audit-stable — that authored script must be frozen: the
first run authors and persists it; every resume of the *same* run replays the
persisted script verbatim instead of asking the LLM again (User Story 26).

This is the Mode-2 analogue of :mod:`langclaw.workflows.resume`: where the
:class:`~langclaw.workflows.resume.StepMemoizer` memoizes each completed *step*
result keyed ``(run_id, step_id)``, :class:`AuthoredScriptResolver` memoizes the
whole authored *script* keyed ``(workflow_name, run_id)``.  The persistence
backend is abstracted behind :class:`ScriptStore`; an in-memory implementation
ships here for tests, and the production wiring persists via the existing
checkpointer under ``thread_id = workflow:{name}:{run_id}`` (deferred to the
bus-integration slice).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Protocol, runtime_checkable

#: An author produces the workflow body as a PTC script string.  Async so the
#: production implementation can call the model; tests pass a canned coroutine.
ScriptAuthor = Callable[[], Awaitable[str]]


@runtime_checkable
class ScriptStore(Protocol):
    """Persistence seam for an llm_authored workflow's frozen body."""

    async def get(self, workflow_name: str, run_id: str) -> tuple[bool, str | None]:
        """Return ``(hit, script)`` — ``hit`` is ``False`` when not stored."""
        ...

    async def put(self, workflow_name: str, run_id: str, script: str) -> None:
        """Persist *script* for ``(workflow_name, run_id)``."""
        ...


class InMemoryScriptStore:
    """A dict-backed :class:`ScriptStore` for tests and single-process runs."""

    def __init__(self) -> None:
        self._data: dict[tuple[str, str], str] = {}

    async def get(self, workflow_name: str, run_id: str) -> tuple[bool, str | None]:
        key = (workflow_name, run_id)
        if key in self._data:
            return True, self._data[key]
        return False, None

    async def put(self, workflow_name: str, run_id: str, script: str) -> None:
        self._data[(workflow_name, run_id)] = script

    def __len__(self) -> int:
        return len(self._data)


class AuthoredScriptResolver:
    """Resolve an llm_authored workflow's body: author once, replay thereafter.

    Bind to a :class:`ScriptStore`; call :meth:`resolve` at the start of each run.
    """

    def __init__(self, store: ScriptStore) -> None:
        self._store = store

    async def resolve(
        self,
        workflow_name: str,
        run_id: str,
        author: ScriptAuthor,
    ) -> tuple[str, bool]:
        """Return ``(script, freshly_authored)`` for ``(workflow_name, run_id)``.

        ``freshly_authored`` reports whether authoring happened *on this call*:
        on a store hit the persisted script is replayed (``freshly_authored`` is
        ``False``) and *author* is never called; on a miss *author* runs once,
        its result is validated and persisted, and ``freshly_authored`` is
        ``True``.  It lets the caller distinguish a first fire from a resume
        (e.g. log/emit the authoring event exactly once).

        Args:
            workflow_name: The llm_authored workflow's name (identity prefix).
            run_id:        Stable identifier for this run (resume key).
            author:        Async callable producing the body as a PTC script.

        Raises:
            ValueError: If *author* returns a blank or non-string script — a
                useless body is rejected rather than persisted (which would make
                every resume replay garbage).
        """
        hit, script = await self._store.get(workflow_name, run_id)
        if hit and script is not None:
            return script, False

        authored = await author()
        if not isinstance(authored, str) or not authored.strip():
            raise ValueError(
                f"llm_authored workflow {workflow_name!r} author must return a "
                "non-empty script string"
            )
        await self._store.put(workflow_name, run_id, authored)
        return authored, True
