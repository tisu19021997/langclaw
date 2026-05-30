"""
Mode 2 execution — run an LLM-authored body as a sandboxed PTC script
(issue #38, Phase 3).

A ``mode="llm_authored"`` workflow's body is a JavaScript program the model
wrote (and which :class:`~langclaw.workflows.authored.AuthoredScriptResolver`
froze for the run). :func:`build_workflow_script_runner` turns a role-filtered
toolset into the ``script_runner`` the runtime calls to *execute* that body:

- the validated run input is injected as the JS global ``inp`` (JSON);
- each allowlisted tool is exposed as ``tools.<camelName>`` via QuickJS PTC —
  the list passed in **is** the capability boundary (a tool not in it simply
  does not exist in the sandbox);
- the script runs in the same capability-scoped QuickJS sandbox the interpreter
  (``eval``) uses — no filesystem, network, or real clock;
- the body's value (its last expression) is returned; structured output must be
  produced with ``JSON.stringify(...)`` — a bare object marshals to a non-JSON
  string. A JS error becomes a :class:`WorkflowStepError`.

This module is the single point of coupling to ``langchain_quickjs``'s REPL
internals — imported lazily so ``import langclaw.workflows`` works without the
``interpreter`` extra, and consistent with the interpreter module already
depending on ``langchain_quickjs._ptc``.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Awaitable, Callable, Sequence
from typing import TYPE_CHECKING, Any

from langclaw.workflows.context import WorkflowStepError

if TYPE_CHECKING:
    from langchain_core.tools import BaseTool

#: A script runner executes a resolved body against the validated input.
ScriptRunnerFn = Callable[[str, Any], Awaitable[Any]]

# Sandbox defaults — generous; the workflow's own ``timeout_s`` is the outer
# bound (the runtime wraps the run in ``asyncio.wait_for``).
_DEFAULT_MEMORY_LIMIT = 64 * 1024 * 1024
_DEFAULT_TIMEOUT_S = 10.0
_DEFAULT_MAX_STDOUT = 16 * 1024


def build_workflow_script_runner(
    tools: Sequence[BaseTool],
    *,
    memory_limit: int = _DEFAULT_MEMORY_LIMIT,
    timeout_s: float = _DEFAULT_TIMEOUT_S,
    max_stdout_chars: int = _DEFAULT_MAX_STDOUT,
    max_ptc_calls: int | None = 256,
) -> ScriptRunnerFn:
    """Build a ``script_runner`` that executes an authored body over *tools*.

    Args:
        tools:            The tools to expose in the sandbox — the capability
                          allowlist. Pass the role-filtered, ``uses_tools``-narrowed
                          live toolset.
        memory_limit:     QuickJS runtime memory ceiling (bytes).
        timeout_s:        Per-eval wall-clock budget inside the sandbox.
        max_stdout_chars: Console-capture cap.
        max_ptc_calls:    Max ``tools.*`` bridge calls per run (runaway backstop).

    Returns:
        An async ``(script, validated_input) -> output`` callable suitable as the
        ``script_runner`` argument of ``WorkflowRuntime.start_run``.
    """
    # Imported lazily (and from the private REPL module) for the reasons in the
    # module docstring. One registry per runner; each run gets its own slot.
    from langchain_quickjs._repl import _Registry

    registry = _Registry(
        memory_limit=memory_limit,
        timeout=timeout_s,
        capture_console=True,
        max_stdout_chars=max_stdout_chars,
        max_ptc_calls=max_ptc_calls,
    )
    tool_list = list(tools)

    async def run(script: str, validated_input: Any) -> Any:
        thread_id = f"workflow-eval-{uuid.uuid4().hex}"
        repl = registry.get(thread_id)
        try:
            repl.install_tools(tool_list)
            payload = json.dumps(_to_jsonable(validated_input))
            code = f"const inp = {payload};\n{script}"
            outcome = await repl.eval_async(code)
            if outcome.error_type:
                raise WorkflowStepError(
                    f"Authored workflow script failed: "
                    f"{outcome.error_type}: {outcome.error_message}"
                )
            return _coerce_result(outcome.result)
        finally:
            registry.evict(thread_id)

    return run


def _to_jsonable(value: Any) -> Any:
    """Render the run input as a JSON-injectable value for the ``inp`` global."""
    if value is None:
        return None
    if hasattr(value, "model_dump"):  # Pydantic model
        return value.model_dump(mode="json")
    return value


def _coerce_result(result: Any) -> Any:
    """Best-effort decode of the sandbox's marshalled result.

    A script ending on ``JSON.stringify(x)`` yields a JSON string we parse back
    into Python; a primitive (string/number/bool) passes through. A bare object
    return marshals to a non-JSON string, which fails to parse and is returned
    as-is so output validation surfaces the contract violation clearly.
    """
    if isinstance(result, str):
        try:
            return json.loads(result)
        except (ValueError, TypeError):
            return result
    return result
