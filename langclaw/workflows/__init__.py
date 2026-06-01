"""
Langclaw-native Workflow primitive (issue #38, Phase 1).

A *workflow* is an operator-authored async Python function, registered with
``@app.workflow()``, that orchestrates multi-step agent work.  Unlike the
interpreter (``eval``), which lets the LLM improvise a one-shot script, a
workflow is durable, typed, named, and RBAC-gated — its steps round-trip
through the same bus → gateway pipeline as ordinary messages, so rate limiting,
channel context, and checkpointing are inherited rather than reimplemented.

Deep, isolation-testable modules:

- :mod:`registry`  — ``WorkflowSpec`` + ``WorkflowRegistry`` (registration,
  name-collision detection, Pydantic I/O binding).
- :mod:`context`   — ``WorkflowContext``: the ``ctx.agent`` / ``ctx.subagent`` /
  ``ctx.tool`` / ``ctx.parallel`` / ``ctx.phase`` step surface, with
  deterministic step IDs for durable resume.
- :mod:`runtime`   — ``WorkflowRuntime``: run lifecycle, per-run concurrency +
  step-count budget, global ``max_concurrent_runs`` ceiling.
- :mod:`resume`    — ``StepMemoizer`` + the ``StepStore`` protocol: memoize
  per-step results so a re-run replays completed steps instead of re-running
  them.  (The resume *trigger* — re-invoking a prior ``run_id`` — is not yet wired.)
- :mod:`step_store` — ``StoreStepStore`` + ``make_step_store_backend``: the
  durable ``StepStore`` over a LangGraph ``BaseStore`` (SQLite/Postgres), opt-in
  via ``workflows.durable_steps``.
- :mod:`authored`  — ``AuthoredScriptResolver``: Mode 2 (``llm_authored``) —
  freeze the LLM-authored body so a run replays the same script on resume.
"""

from __future__ import annotations

from langclaw.workflows.authored import (
    AuthoredScriptResolver,
    InMemoryScriptStore,
    ScriptAuthor,
    ScriptStore,
    StoreScriptStore,
)
from langclaw.workflows.bridge import (
    WORKFLOW_TOOL_PREFIX,
    build_toolset_executor,
    make_workflow_tools,
    resolve_workflow_ptc_names,
    workflow_system_prompt,
)
from langclaw.workflows.context import (
    StepRequest,
    WorkflowBudgetExceeded,
    WorkflowContext,
    WorkflowStepError,
)
from langclaw.workflows.js_runner import (
    build_workflow_author,
    build_workflow_script_runner,
)
from langclaw.workflows.progress import (
    emit_progress,
    render_workflow_progress,
    reset_progress_sink,
    set_progress_sink,
)
from langclaw.workflows.registry import WorkflowRegistry, WorkflowSpec
from langclaw.workflows.resume import InMemoryStepStore, StepMemoizer, StepStore
from langclaw.workflows.run_store import RunStore, StoreRunStore
from langclaw.workflows.runtime import WorkflowRuntime
from langclaw.workflows.saved_store import (
    SavedWorkflow,
    SavedWorkflowStore,
    validate_saved_name,
)
from langclaw.workflows.step_store import (
    MemoryStepStoreBackend,
    StepStoreBackend,
    StoreStepStore,
    make_step_store_backend,
)

__all__ = [
    "AuthoredScriptResolver",
    "InMemoryScriptStore",
    "InMemoryStepStore",
    "MemoryStepStoreBackend",
    "ScriptAuthor",
    "ScriptStore",
    "StepMemoizer",
    "RunStore",
    "SavedWorkflow",
    "SavedWorkflowStore",
    "StepStore",
    "StepStoreBackend",
    "StoreRunStore",
    "StoreScriptStore",
    "StoreStepStore",
    "StepRequest",
    "WorkflowBudgetExceeded",
    "WorkflowContext",
    "WorkflowRegistry",
    "WorkflowRuntime",
    "WORKFLOW_TOOL_PREFIX",
    "WorkflowSpec",
    "WorkflowStepError",
    "build_toolset_executor",
    "build_workflow_author",
    "build_workflow_script_runner",
    "emit_progress",
    "make_step_store_backend",
    "make_workflow_tools",
    "render_workflow_progress",
    "reset_progress_sink",
    "resolve_workflow_ptc_names",
    "set_progress_sink",
    "validate_saved_name",
    "workflow_system_prompt",
]
