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
- :mod:`resume`    — ``StepMemoizer``: persist per-step results so a restart
  replays completed steps instead of re-running them.
"""

from __future__ import annotations

from langclaw.workflows.bridge import build_toolset_executor, make_workflow_tools
from langclaw.workflows.context import (
    StepRequest,
    WorkflowBudgetExceeded,
    WorkflowContext,
    WorkflowStepError,
)
from langclaw.workflows.registry import WorkflowRegistry, WorkflowSpec
from langclaw.workflows.resume import InMemoryStepStore, StepMemoizer
from langclaw.workflows.runtime import WorkflowRuntime

__all__ = [
    "InMemoryStepStore",
    "StepMemoizer",
    "StepRequest",
    "WorkflowBudgetExceeded",
    "WorkflowContext",
    "WorkflowRegistry",
    "WorkflowRuntime",
    "WorkflowSpec",
    "WorkflowStepError",
    "build_toolset_executor",
    "make_workflow_tools",
]
