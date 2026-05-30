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
import re
import uuid
from collections.abc import Awaitable, Callable, Sequence
from typing import TYPE_CHECKING, Any

from langclaw.workflows.context import WorkflowStepError

if TYPE_CHECKING:
    from langchain_core.tools import BaseTool

    from langclaw.workflows.registry import WorkflowSpec

#: A script runner executes a resolved body against the validated input.
ScriptRunnerFn = Callable[[str, Any], Awaitable[Any]]
#: An author produces a workflow's JS body from its spec + validated input.
ScriptAuthorFn = Callable[["WorkflowSpec", Any], Awaitable[str]]

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


def build_workflow_author(
    model: Any,
    *,
    ptc_tool_names: Sequence[str] = (),
) -> ScriptAuthorFn:
    """Build an ``author`` that asks *model* to write a workflow's JS body.

    The Mode-2 counterpart to :func:`build_workflow_script_runner`: it renders
    the workflow's contract (task description, input value + schema, output
    schema, allowed tools, and the sandbox ABI) into a prompt, calls the model
    once, and returns the extracted JavaScript body.

    Args:
        model:          A chat model exposing ``await model.ainvoke(prompt)``.
        ptc_tool_names: Bare tool names the body may call; rendered camelCased
                        (``tools.<camelName>``) so they match the runner's PTC
                        surface. Pass the role-filtered, ``uses_tools``-narrowed set.

    Returns:
        An async ``(spec, validated_input) -> script`` callable suitable as the
        ``author`` argument of ``WorkflowRuntime.start_run``.
    """

    async def author(spec: WorkflowSpec, validated_input: Any) -> str:
        prompt = _render_authoring_prompt(spec, validated_input, ptc_tool_names)
        response = await model.ainvoke(prompt)
        return _extract_js(_message_text(response))

    return author


def _render_authoring_prompt(
    spec: WorkflowSpec,
    validated_input: Any,
    ptc_tool_names: Sequence[str],
) -> str:
    """Render the contract + sandbox ABI into a single authoring prompt."""
    tools_line = (
        ", ".join(f"tools.{_camel(n)}({{...}})" for n in ptc_tool_names)
        if ptc_tool_names
        else "(none — use only `inp` and plain JavaScript)"
    )
    out_schema = (
        json.dumps(spec.output_model.model_json_schema())
        if getattr(spec.output_model, "model_json_schema", None)
        else "(any JSON-serializable value)"
    )
    in_value = json.dumps(_to_jsonable(validated_input))
    return (
        "Write the body of a workflow as a sandboxed JavaScript program.\n\n"
        f"## Task\n{spec.description}\n\n"
        f"## Input\nThe run input is available as the global `inp`:\n{in_value}\n\n"
        f"## Allowed tools\nCall ONLY these (await each):\n{tools_line}\n\n"
        f"## Required output\nThe program's final expression must produce this "
        f"shape:\n{out_schema}\n\n"
        "## Rules (the sandbox ABI)\n"
        "- Tools live on `tools` in camelCase; await every call.\n"
        "- No filesystem, network, real clock, Date, or Math.random.\n"
        "- End on a single final expression — NOT a `return` (top-level return "
        "is a syntax error).\n"
        "- For structured output, end on `JSON.stringify(value)`; a bare object "
        "marshals to a non-JSON string.\n\n"
        "Output ONLY the JavaScript body — no prose, no markdown fences."
    )


def _message_text(response: Any) -> str:
    """Extract text from a chat-model response (AIMessage-like or str)."""
    content = getattr(response, "content", response)
    if isinstance(content, str):
        return content
    # Some models return a list of content blocks; concatenate text parts.
    if isinstance(content, list):
        parts = [
            block.get("text", "") if isinstance(block, dict) else str(block) for block in content
        ]
        return "".join(parts)
    return str(content)


_FENCE_RE = re.compile(r"```(?:[a-zA-Z]+)?\s*\n(.*?)\n```", re.DOTALL)


def _extract_js(text: str) -> str:
    """Return the JS body, stripping a single markdown code fence if present."""
    match = _FENCE_RE.search(text)
    if match:
        return match.group(1).strip()
    return text.strip()


def _camel(name: str) -> str:
    """``snake_case`` / ``kebab-case`` → ``camelCase`` (matches the PTC surface)."""
    return re.sub(r"[-_]+(\w)", lambda m: m.group(1).upper(), name)


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
