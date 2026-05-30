"""
``WorkflowRuntime`` — run lifecycle and budget enforcement (issue #38, Phase 1).

The runtime drives a :class:`~langclaw.workflows.registry.WorkflowSpec`'s body to
completion:

1. Validate the run input against the spec's ``input_model``.
2. Enforce the global ``max_concurrent_runs`` ceiling.
3. Build a :class:`~langclaw.workflows.context.WorkflowContext` wired to the
   injected step executor, a per-run concurrency semaphore, the step-count
   backstop, the phase callback, and (when given) a
   :class:`~langclaw.workflows.resume.StepMemoizer`.
4. Apply the per-run ``timeout_s`` budget.
5. Validate the output against the spec's ``output_model``.

The step executor is injected, so the runtime is testable without a bus: pass a
fake ``async def (StepRequest) -> result``.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from loguru import logger

from langclaw.workflows.context import StepExecutor, WorkflowContext
from langclaw.workflows.resume import StepMemoizer, StepStore

if TYPE_CHECKING:
    from langclaw.config.schema import WorkflowsConfig
    from langclaw.workflows.registry import WorkflowSpec


class WorkflowRuntime:
    """Owns workflow run execution and global resource ceilings.

    Args:
        config:        The resolved :class:`WorkflowsConfig`.
        step_store:    Optional durable :class:`StepStore` enabling resume.
        phase_cb_factory: Optional factory ``(run_id) -> (phase_name -> None)``
                       producing a per-run phase callback (e.g. to project
                       progress to a channel).
    """

    def __init__(
        self,
        config: WorkflowsConfig,
        *,
        step_store: StepStore | None = None,
        phase_cb_factory: Callable[[str], Callable[[str], None]] | None = None,
    ) -> None:
        self._config = config
        self._step_store = step_store
        self._phase_cb_factory = phase_cb_factory
        self._run_gate = asyncio.Semaphore(max(1, config.max_concurrent_runs))

    async def start_run(
        self,
        spec: WorkflowSpec,
        run_input: Any,
        *,
        run_id: str,
        executor: StepExecutor,
    ) -> Any:
        """Execute *spec* to completion and return its (validated) output.

        Args:
            spec:      The workflow to run.
            run_input: Raw input; validated against ``spec.input_model``.
            run_id:    Stable identifier for this run (resume + correlation).
            executor:  Async callable performing each
                       :class:`~langclaw.workflows.context.StepRequest`.

        Raises:
            Exception: Whatever input validation or the body raises; the caller
                       (gateway/tool bridge) converts it to a channel-safe error.
        """
        validated_input = spec.validate_input(run_input)

        max_steps = spec.max_steps or self._config.max_steps_per_run
        semaphore = asyncio.Semaphore(max(1, spec.max_concurrency))
        memoize = None
        if self._step_store is not None:
            memoize = StepMemoizer(self._step_store, run_id).wrap
        phase_cb = self._phase_cb_factory(run_id) if self._phase_cb_factory else None

        ctx = WorkflowContext(
            executor=executor,
            memoize=memoize,
            phase_cb=phase_cb,
            max_steps=max_steps,
            semaphore=semaphore,
        )

        async with self._run_gate:
            logger.info(f"Workflow {spec.name!r} run {run_id} started")
            coro = self._invoke_body(spec, ctx, validated_input)
            if spec.timeout_s is not None:
                output = await asyncio.wait_for(coro, timeout=spec.timeout_s)
            else:
                output = await coro

        output = self._validate_output(spec, output)
        logger.info(f"Workflow {spec.name!r} run {run_id} completed")
        return output

    @staticmethod
    async def _invoke_body(spec: WorkflowSpec, ctx: WorkflowContext, inp: Any) -> Any:
        result = spec.fn(ctx, inp)
        if isinstance(result, Awaitable):
            return await result
        return result

    @staticmethod
    def _validate_output(spec: WorkflowSpec, output: Any) -> Any:
        model = spec.output_model
        if model is None:
            return output
        if isinstance(output, model):
            return output
        if isinstance(output, dict):
            return model(**output)
        if hasattr(model, "model_validate"):
            return model.model_validate(output)
        return model(output)
