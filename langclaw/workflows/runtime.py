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

from langclaw.workflows.authored import (
    AuthoredScriptResolver,
    InMemoryScriptStore,
    ScriptStore,
)
from langclaw.workflows.context import StepExecutor, WorkflowContext
from langclaw.workflows.resume import StepMemoizer, StepStore

if TYPE_CHECKING:
    from langclaw.config.schema import WorkflowsConfig
    from langclaw.workflows.registry import WorkflowSpec

#: An author produces an llm_authored workflow's body from its spec + input.
ScriptAuthorFn = Callable[["WorkflowSpec", Any], Awaitable[str]]
#: A script runner executes a resolved body against the validated input.
ScriptRunnerFn = Callable[[str, Any], Awaitable[Any]]


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
        script_store: ScriptStore | None = None,
        phase_cb_factory: Callable[[str], Callable[[str], None]] | None = None,
    ) -> None:
        self._config = config
        self._step_store = step_store
        self._script_store = script_store
        self._phase_cb_factory = phase_cb_factory
        self._run_gate = asyncio.Semaphore(max(1, config.max_concurrent_runs))

    async def start_run(
        self,
        spec: WorkflowSpec,
        run_input: Any,
        *,
        run_id: str,
        executor: StepExecutor | None = None,
        author: ScriptAuthorFn | None = None,
        script_runner: ScriptRunnerFn | None = None,
    ) -> Any:
        """Execute *spec* to completion and return its (validated) output.

        Dispatches on ``spec.mode``:

        - ``"python"`` (default) drives ``spec.fn`` via *executor* (required).
        - ``"llm_authored"`` (Mode 2) resolves the body via *author* (frozen on
          first run, replayed on resume) and runs it via *script_runner* — both
          required. The Python ``spec.fn`` is ignored.

        Args:
            spec:          The workflow to run.
            run_input:     Raw input; validated against ``spec.input_model``.
            run_id:        Stable identifier for this run (resume + correlation).
            executor:      Async callable performing each
                           :class:`~langclaw.workflows.context.StepRequest`
                           (python mode).
            author:        Async ``(spec, input) -> script`` authoring the body
                           (llm_authored mode).
            script_runner: Async ``(script, input) -> output`` executing the
                           resolved body (llm_authored mode).

        Raises:
            ValueError: If the required callables for the spec's mode are missing.
            Exception:  Whatever input validation or the body raises; the caller
                        (gateway/tool bridge) converts it to a channel-safe error.
        """
        validated_input = spec.validate_input(run_input)
        if spec.mode == "llm_authored":
            return await self._run_authored(
                spec, validated_input, run_id=run_id, author=author, script_runner=script_runner
            )
        return await self._run_python(spec, validated_input, run_id=run_id, executor=executor)

    async def _run_python(
        self,
        spec: WorkflowSpec,
        validated_input: Any,
        *,
        run_id: str,
        executor: StepExecutor | None,
    ) -> Any:
        if executor is None:
            raise ValueError(f"Workflow {spec.name!r} (mode='python') requires a step executor.")

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

    async def _run_authored(
        self,
        spec: WorkflowSpec,
        validated_input: Any,
        *,
        run_id: str,
        author: ScriptAuthorFn | None,
        script_runner: ScriptRunnerFn | None,
    ) -> Any:
        if author is None or script_runner is None:
            raise ValueError(
                f"Workflow {spec.name!r} (mode='llm_authored') requires both an "
                "`author` and a `script_runner`."
            )

        # A durable script_store freezes the body so a later run/resume with the
        # same run_id replays it. With no store, each run authors fresh (resume
        # is impossible anyway — same contract as step_store=None).
        # NB: explicit None check — an empty InMemoryScriptStore is falsy
        # (``__len__`` is 0), so ``or`` would silently discard a provided store.
        store = self._script_store if self._script_store is not None else InMemoryScriptStore()
        resolver = AuthoredScriptResolver(store)

        async def _author() -> str:
            return await author(spec, validated_input)

        async with self._run_gate:
            script, freshly_authored = await resolver.resolve(spec.name, run_id, _author)
            logger.info(
                f"Workflow {spec.name!r} run {run_id}: "
                + ("authored new body" if freshly_authored else "replaying authored body")
            )
            coro = script_runner(script, validated_input)
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
