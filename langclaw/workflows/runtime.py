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
import json
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from loguru import logger

from langclaw.workflows.authored import (
    AuthoredScriptResolver,
    InMemoryScriptStore,
    ScriptStore,
)
from langclaw.workflows.context import StepExecutor, WorkflowContext
from langclaw.workflows.progress import emit_progress
from langclaw.workflows.resume import StepMemoizer, StepStore
from langclaw.workflows.run_store import RunStore

if TYPE_CHECKING:
    from langclaw.config.schema import WorkflowsConfig
    from langclaw.workflows.registry import WorkflowSpec

#: An author produces an llm_authored workflow's body from its spec + input.
ScriptAuthorFn = Callable[["WorkflowSpec", Any], Awaitable[str]]
#: A script runner executes a resolved body against the validated input.
ScriptRunnerFn = Callable[[str, Any], Awaitable[Any]]


def _serialize_input(run_input: Any) -> Any:
    """Make the run input JSON-storable for the run journal (replayed verbatim).

    Pydantic models are dumped; everything else must already be JSON-serializable.
    A non-serializable input fails here with a clear message rather than an opaque
    error deep inside the store's ``aput``.
    """
    if hasattr(run_input, "model_dump"):
        return run_input.model_dump()
    try:
        json.dumps(run_input)
    except (TypeError, ValueError) as exc:
        raise TypeError(
            "Workflow run input is not JSON-serializable and cannot be journaled "
            f"for resume: {type(run_input).__name__}. Pass a dict, Pydantic model, "
            "or other JSON-compatible value."
        ) from exc
    return run_input


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
        run_store: RunStore | None = None,
        phase_cb_factory: Callable[[str], Callable[[str], None]] | None = None,
    ) -> None:
        self._config = config
        self._step_store = step_store
        self._script_store = script_store
        self._run_store = run_store
        self._phase_cb_factory = phase_cb_factory
        self._run_gate = asyncio.Semaphore(max(1, config.max_concurrent_runs))
        # Set by the agent builder so non-tool entry points (bus dispatch, cron,
        # startup resume) can rebuild the step executor / Mode-2 callables over
        # the default agent's live, role-filtered toolset.
        self._resume_executor_factory: Callable[[Any], Any] | None = None
        self._author_factory: Callable[[Any], Any] | None = None
        self._script_runner_factory: Callable[[Any], Any] | None = None

    def set_resume_executor_factory(self, factory: Callable[[Any], Any]) -> None:
        """Register the executor factory used to build a resume-time step executor."""
        self._resume_executor_factory = factory

    def set_authoring_factories(
        self,
        *,
        author_factory: Callable[[Any], Any],
        script_runner_factory: Callable[[Any], Any],
    ) -> None:
        """Register the Mode-2 author / script-runner factories.

        Lets :meth:`run_registered` (bus dispatch, cron, resume) run
        ``llm_authored`` workflows without the per-call tool bridge.  Each
        factory takes a spec and returns the author / script-runner closing over
        the live toolset (built by the agent builder).
        """
        self._author_factory = author_factory
        self._script_runner_factory = script_runner_factory

    async def run_registered(self, spec: WorkflowSpec, run_input: Any, *, run_id: str) -> Any:
        """Run *spec* using the factories registered at agent-build time.

        The entry point for runs started **outside** the per-message tool bridge —
        bus dispatch (``origin="workflow"``), cron-fired workflows, and startup
        resume.  Dispatches on ``spec.mode``:

        - ``"python"`` builds a step executor via the resume executor factory;
        - ``"llm_authored"`` builds the author + script-runner via the authoring
          factories (and, with a durable script store, replays a frozen body).

        Raises:
            RuntimeError: when the factory required for the spec's mode was never
                registered (e.g. workflows enabled but the builder did not wire
                them).
        """
        if spec.mode == "saved":
            if self._script_runner_factory is None:
                raise RuntimeError(
                    f"Cannot run saved workflow {spec.name!r}: no script-runner "
                    "factory registered (needs the interpreter extra)."
                )
            return await self.start_run(
                spec,
                run_input,
                run_id=run_id,
                script_runner=self._script_runner_factory(spec),
            )
        if spec.mode == "llm_authored":
            if self._author_factory is None or self._script_runner_factory is None:
                raise RuntimeError(
                    f"Cannot run llm_authored workflow {spec.name!r}: no authoring "
                    "factories registered (needs a model + interpreter extra)."
                )
            return await self.start_run(
                spec,
                run_input,
                run_id=run_id,
                author=self._author_factory(spec),
                script_runner=self._script_runner_factory(spec),
            )
        if self._resume_executor_factory is None:
            raise RuntimeError(
                f"Cannot run workflow {spec.name!r}: no resume executor factory registered."
            )
        maybe = self._resume_executor_factory(None)
        executor = await maybe if isinstance(maybe, Awaitable) else maybe
        return await self.start_run(spec, run_input, run_id=run_id, executor=executor)

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
        if self._run_store is not None:
            await self._run_store.mark_running(run_id, spec.name, _serialize_input(run_input))
        try:
            if spec.mode == "saved":
                output = await self._run_saved(
                    spec, validated_input, run_id=run_id, script_runner=script_runner
                )
            elif spec.mode == "llm_authored":
                output = await self._run_authored(
                    spec, validated_input, run_id=run_id, author=author, script_runner=script_runner
                )
            else:
                output = await self._run_python(
                    spec, validated_input, run_id=run_id, executor=executor
                )
        except Exception:
            if self._run_store is not None:
                await self._run_store.mark_failed(run_id)
            raise
        if self._run_store is not None:
            await self._run_store.mark_completed(run_id)
        return output

    async def resume_incomplete(
        self,
        *,
        get_spec: Callable[[str], WorkflowSpec | None],
        make_executor: Callable[[WorkflowSpec], Any] | None = None,
    ) -> list[str]:
        """Re-run every run left ``"running"`` in the run store (crash recovery).

        Each incomplete run is re-invoked with its stored input and original
        ``run_id``, so already-completed steps replay from the step store and only
        the unfinished tail executes.  Returns the resumed ``run_id``s.

        A run whose spec is no longer registered is skipped (logged).  Python-mode
        runs replay from the durable step store (only the unfinished tail
        re-executes); ``llm_authored`` runs replay their **frozen body** from the
        durable script store and re-execute it (individual Mode-2 steps are not
        memoized) — skipped only if the authoring factories were never registered.

        *make_executor* (python-mode only; used by tests) may be sync or async and
        builds the step executor per spec.  When omitted, the registered factories
        drive both modes via :meth:`run_registered`.
        """
        if self._run_store is None:
            return []
        resumed: list[str] = []
        for record in await self._run_store.list_incomplete():
            run_id = record["run_id"]
            spec = get_spec(record["spec_name"])
            if spec is None:
                logger.warning(f"Cannot resume {run_id}: workflow {record['spec_name']!r} gone.")
                continue
            if spec.mode == "llm_authored" and make_executor is not None:
                # Explicit python-only executor given; cannot drive Mode 2.
                logger.warning(f"Cannot resume {run_id}: llm_authored needs authoring factories.")
                continue
            logger.info(f"Resuming incomplete workflow run {run_id} ({spec.name!r})")
            if make_executor is not None:
                maybe = make_executor(spec)
                executor = await maybe if isinstance(maybe, Awaitable) else maybe
                await self.start_run(spec, record["input"], run_id=run_id, executor=executor)
            else:
                try:
                    await self.run_registered(spec, record["input"], run_id=run_id)
                except RuntimeError as exc:
                    logger.warning(f"Cannot resume {run_id}: {exc}")
                    continue
            resumed.append(run_id)
        return resumed

    def _progress_callbacks(
        self, spec: WorkflowSpec, run_id: str
    ) -> tuple[Callable[[str], None], Callable[[str], None]]:
        """Build the ``(phase_cb, log_cb)`` pair shared by every run mode.

        Each callback projects a progress event onto the request-scoped sink
        (rendered to the invoking channel) so a phase/log narration looks the
        same whether it came from a python body's ``ctx.phase``/``ctx.log`` or a
        saved/authored body's ``tools.phase``/``tools.log``.
        """
        factory_cb = self._phase_cb_factory(run_id) if self._phase_cb_factory else None

        def phase_cb(name: str) -> None:
            emit_progress({"kind": "phase", "workflow": spec.name, "run_id": run_id, "phase": name})
            if factory_cb is not None:
                factory_cb(name)

        def log_cb(message: str) -> None:
            emit_progress(
                {"kind": "log", "workflow": spec.name, "run_id": run_id, "message": message}
            )

        return phase_cb, log_cb

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
        phase_cb, log_cb = self._progress_callbacks(spec, run_id)

        ctx = WorkflowContext(
            executor=executor,
            memoize=memoize,
            phase_cb=phase_cb,
            log_cb=log_cb,
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

        # Durable store freezes the body per run_id (replayed on resume); none →
        # author fresh. `is not None`: an empty store is falsy, `or` would drop it.
        store = self._script_store if self._script_store is not None else InMemoryScriptStore()
        resolver = AuthoredScriptResolver(store)

        async def _author() -> str:
            return await author(spec, validated_input)

        async with self._run_gate:
            script, freshly_authored = await resolver.resolve(spec.name, run_id, _author)
            verb = "authored new body" if freshly_authored else "replaying authored body"
            logger.info(f"Workflow {spec.name!r} run {run_id}: {verb} ({len(script)} chars)")
            emit_progress(
                {"kind": "authored", "workflow": spec.name, "run_id": run_id, "script": script}
            )
            # The body is a Mode-2 run's audit artifact — at DEBUG normally, and
            # at WARNING on failure so a broken body is visible without DEBUG.
            logger.debug(f"Workflow {spec.name!r} run {run_id} body:\n{script}")
            phase_cb, log_cb = self._progress_callbacks(spec, run_id)
            coro = script_runner(script, validated_input, phase_cb=phase_cb, log_cb=log_cb)
            try:
                if spec.timeout_s is not None:
                    output = await asyncio.wait_for(coro, timeout=spec.timeout_s)
                else:
                    output = await coro
            except Exception:
                logger.warning(f"Workflow {spec.name!r} run {run_id} failed; body:\n{script}")
                raise

        output = self._validate_output(spec, output)
        logger.info(f"Workflow {spec.name!r} run {run_id} completed")
        return output

    async def _run_saved(
        self,
        spec: WorkflowSpec,
        validated_input: Any,
        *,
        run_id: str,
        script_runner: ScriptRunnerFn | None,
    ) -> Any:
        """Run a ``mode="saved"`` workflow: execute its frozen body verbatim.

        Unlike ``llm_authored``, there is no author step and no per-run script
        freezing — ``spec.script`` (loaded from the saved-workflow file) *is* the
        source of truth, so the body is handed straight to the script runner.

        Resume is at-least-once and **non-idempotent**: like ``llm_authored``, a
        saved body is not step-memoized, so a crash-resume re-runs the whole
        script. If it calls side-effecting tools (e.g. ``write_file`` or an egress
        tool via ``uses_tools``), those effects repeat. Write saved scripts to
        tolerate re-execution.
        """
        if script_runner is None:
            raise ValueError(f"Workflow {spec.name!r} (mode='saved') requires a `script_runner`.")
        script = spec.script or ""
        async with self._run_gate:
            logger.info(
                f"Workflow {spec.name!r} run {run_id}: running saved body ({len(script)} chars)"
            )
            emit_progress(
                {"kind": "authored", "workflow": spec.name, "run_id": run_id, "script": script}
            )
            phase_cb, log_cb = self._progress_callbacks(spec, run_id)
            coro = script_runner(script, validated_input, phase_cb=phase_cb, log_cb=log_cb)
            try:
                if spec.timeout_s is not None:
                    output = await asyncio.wait_for(coro, timeout=spec.timeout_s)
                else:
                    output = await coro
            except Exception:
                logger.warning(f"Workflow {spec.name!r} run {run_id} failed; body:\n{script}")
                raise

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
