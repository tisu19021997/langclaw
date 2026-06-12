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

from langclaw.naming import reject_camel_collisions, to_camel_case
from langclaw.workflows.context import WorkflowStepError

if TYPE_CHECKING:
    from langchain_core.tools import BaseTool

    from langclaw.workflows.registry import WorkflowSpec

#: A script runner executes a resolved body against the validated input. It
#: accepts optional keyword-only ``phase_cb`` / ``log_cb`` progress callbacks
#: (the JS ``tools.phase`` / ``tools.log`` counterparts of ``ctx.phase`` /
#: ``ctx.log``); a runner that ignores progress may accept ``**_``.
ScriptRunnerFn = Callable[..., Awaitable[Any]]
#: An author produces a workflow's JS body from its spec + validated input.
ScriptAuthorFn = Callable[["WorkflowSpec", Any], Awaitable[str]]

# Sandbox defaults; the workflow's own timeout_s is the outer bound.
_DEFAULT_MEMORY_LIMIT = 64 * 1024 * 1024
_DEFAULT_TIMEOUT_S = 10.0
_DEFAULT_MAX_STDOUT = 16 * 1024

#: The structural output sink. The body emits its result via
#: ``tools.output({ result: <value> })`` instead of relying on the final
#: expression — so the result can't become ``[unmarshalable value]``, and
#: validation happens against a captured Python value, not a marshalled blob.
OUTPUT_SINK_NAME = "output"
OUTPUT_SINK_ARG = "result"

#: Progress sinks — the JS counterpart of ``ctx.phase`` / ``ctx.log`` (Mode 1).
#: A saved or authored body narrates via ``tools.phase({ name })`` /
#: ``tools.log({ message })``; the runner wires them to the injected callbacks,
#: which the runtime turns into the same progress events a python workflow emits.
PHASE_SINK_NAME = "phase"
PHASE_SINK_ARG = "name"
LOG_SINK_NAME = "log"
LOG_SINK_ARG = "message"

#: The model-call sink — the JS counterpart of ``ctx.llm``. A body asks for a
#: one-shot model call via ``tools.llm({ prompt, system?, model? })`` and gets the
#: reply text back. Always present (like ``output``/``phase``/``log``), not part of
#: the ``@uses`` tool allowlist — it's a primitive, not a capability tool.
LLM_SINK_NAME = "llm"
LLM_SINK_ARG = "prompt"


def build_workflow_script_runner(
    tools: Sequence[BaseTool],
    *,
    default_model: Any | None = None,
    model_resolver: Callable[[str], Any] | None = None,
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
        default_model:    The chat model ``tools.llm`` calls when the body gives no
                          per-call ``model`` override. ``None`` ⇒ ``tools.llm`` errors.
        model_resolver:   ``(model_spec) -> chat model`` resolving a per-call
                          ``tools.llm({model})`` override. ``None`` ⇒ no override.
        memory_limit:     QuickJS runtime memory ceiling (bytes).
        timeout_s:        Per-eval wall-clock budget inside the sandbox.
        max_stdout_chars: Console-capture cap.
        max_ptc_calls:    Max ``tools.*`` bridge calls per run (runaway backstop).

    Returns:
        An async ``(script, validated_input) -> output`` callable suitable as the
        ``script_runner`` argument of ``WorkflowRuntime.start_run``.
    """
    # Lazy import from the private REPL module (see module docstring).
    from langchain_core.tools import StructuredTool
    from langchain_quickjs._repl import _Registry

    registry = _Registry(
        memory_limit=memory_limit,
        timeout=timeout_s,
        capture_console=True,
        max_stdout_chars=max_stdout_chars,
        max_ptc_calls=max_ptc_calls,
    )
    tool_list = list(tools)

    async def run(
        script: str,
        validated_input: Any,
        *,
        phase_cb: Callable[[str], None] | None = None,
        log_cb: Callable[[str], None] | None = None,
    ) -> Any:
        thread_id = f"workflow-eval-{uuid.uuid4().hex}"
        repl = registry.get(thread_id)

        # Per-run output sink: the body calls tools.output({result: ...}) to emit
        # its result as a captured Python value. This is the canonical contract —
        # the script's final expression is irrelevant, so the result can never be
        # the `[unmarshalable value]` sentinel.
        sink: dict[str, Any] = {"set": False, "value": None}

        async def _capture(result: Any = None) -> str:
            sink["set"] = True
            sink["value"] = result
            return "ok"

        output_tool = StructuredTool.from_function(
            coroutine=_capture,
            name=OUTPUT_SINK_NAME,
            description=(
                "Emit the workflow's final result. Call once with "
                f"{{ {OUTPUT_SINK_ARG}: <your result> }}."
            ),
        )

        # Per-run progress sinks — ctx.phase / ctx.log parity. Best-effort: a
        # narrating body must not fail just because no one is listening, so when
        # a callback is absent the sink is a no-op (and a callback that raises is
        # swallowed — progress is never fatal to the run).
        async def _phase(name: str = "") -> str:
            if phase_cb is not None and name:
                try:
                    phase_cb(name)
                except Exception:  # noqa: BLE001 — progress is best-effort
                    pass
            return "ok"

        async def _log(message: str = "") -> str:
            if log_cb is not None and message:
                try:
                    log_cb(message)
                except Exception:  # noqa: BLE001 — progress is best-effort
                    pass
            return "ok"

        # Per-run model-call sink — ctx.llm parity. One model call, no tools, no
        # loop; returns the reply text. The body can JSON.parse it for structure.
        async def _llm(prompt: str = "", system: str = "", model: str = "") -> str:
            chosen = default_model
            if model:
                if model_resolver is None:
                    raise WorkflowStepError(
                        f"tools.llm requested model {model!r} but no model resolver is "
                        "configured for this run."
                    )
                chosen = model_resolver(model)
            if chosen is None:
                raise WorkflowStepError(
                    "tools.llm has no model to call — none was configured for this run."
                )
            messages = ([("system", system)] if system else []) + [("user", prompt)]
            return _message_text(await chosen.ainvoke(messages)).strip()

        phase_tool = StructuredTool.from_function(
            coroutine=_phase,
            name=PHASE_SINK_NAME,
            description=f"Start a named progress phase: {{ {PHASE_SINK_ARG}: <string> }}.",
        )
        log_tool = StructuredTool.from_function(
            coroutine=_log,
            name=LOG_SINK_NAME,
            description=f"Emit a free-text progress line: {{ {LOG_SINK_ARG}: <string> }}.",
        )
        llm_tool = StructuredTool.from_function(
            coroutine=_llm,
            name=LLM_SINK_NAME,
            description=(
                "Make one model call (no tools, no agent loop) and get the reply text: "
                f"{{ {LLM_SINK_ARG}: <string>, system?: <string>, model?: <string> }}."
            ),
        )

        try:
            # Sinks install last so the reserved names win over any same-named tool.
            repl.install_tools([*tool_list, output_tool, phase_tool, log_tool, llm_tool])
            payload = json.dumps(_to_jsonable(validated_input))
            code = f"const inp = {payload};\n{script}"
            outcome = await repl.eval_async(code)
            if outcome.error_type:
                raise WorkflowStepError(
                    f"Authored workflow script failed: "
                    f"{outcome.error_type}: {outcome.error_message}"
                )
            # The sink is the canonical result when the body used it.
            if sink["set"]:
                return sink["value"]
            # Fallback: the last-expression value (legacy / simple primitives).
            # result_kind="handle" means it ended on a non-serializable value —
            # don't pass the `[unmarshalable value]` placeholder through as data.
            if outcome.result_kind == "handle":
                raise WorkflowStepError(
                    f"Authored workflow script produced no result: it neither called "
                    f"tools.{OUTPUT_SINK_NAME}(...) nor ended on a serializable value "
                    "(it ended on a Promise/function). Emit the result via "
                    f"tools.{OUTPUT_SINK_NAME}({{ {OUTPUT_SINK_ARG}: <value> }})."
                )
            return _coerce_result(outcome.result)
        finally:
            registry.evict(thread_id)

    return run


def build_workflow_author(
    model: Any,
    *,
    tools: Sequence[BaseTool] = (),
) -> ScriptAuthorFn:
    """Build an ``author`` that asks *model* to write a workflow's JS body.

    The Mode-2 counterpart to :func:`build_workflow_script_runner`: it renders
    the workflow's contract (task description, input value, allowed-tool
    signatures, output schema, and the sandbox ABI) into a prompt, calls the
    model once, and returns the extracted JavaScript body.

    Args:
        model: A chat model exposing ``await model.ainvoke(prompt)``.
        tools: The tools the body may call — the same objects the runner exposes
               (the spec's ``uses_tools``, role-filtered). Each is rendered as a
               ``tools.<camelName>({args}) — <description>`` signature so the model
               writes correct calls and result handling. Unlike the interpreter,
               a Mode-2 author writes blind in one shot — it cannot ``console.log``
               and inspect — so honest signatures matter.

    Returns:
        An async ``(spec, validated_input) -> script`` callable suitable as the
        ``author`` argument of ``WorkflowRuntime.start_run``.
    """

    async def author(spec: WorkflowSpec, validated_input: Any) -> str:
        prompt = _render_authoring_prompt(spec, validated_input, tools)
        response = await model.ainvoke(prompt)
        return _extract_js(_message_text(response))

    return author


def _render_authoring_prompt(
    spec: WorkflowSpec,
    validated_input: Any,
    tools: Sequence[BaseTool],
) -> str:
    """Render the contract + sandbox ABI into a single authoring prompt."""
    tools_block = (
        "\n".join(f"  - {_render_tool_signature(t)}" for t in tools)
        if tools
        else "  (none — use only `inp` and plain JavaScript)"
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
        f"## Allowed tools\nCall ONLY these (await each):\n{tools_block}\n\n"
        f"## Required output\nEmit the result by calling "
        f"`await tools.{OUTPUT_SINK_NAME}({{ {OUTPUT_SINK_ARG}: <value> }})` exactly "
        f"ONCE, where <value> matches this shape:\n{out_schema}\n"
        "Do NOT rely on the script's final expression for output — only the "
        f"tools.{OUTPUT_SINK_NAME}(...) call is read.\n\n"
        "## Rules (the sandbox ABI)\n"
        "- Tools live on `tools` in camelCase; await every call.\n"
        "- No ambient host APIs: no `fetch`, `require`, `import`, `process`, or "
        "filesystem/network access. The ONLY way out of the sandbox is the `tools.*` "
        "functions above. (`Date`, `Math`, `JSON`, and `console` ARE available — but "
        "a workflow may re-run on resume, so do not let `Date.now()`/`Math.random()` "
        "change the result in a way that matters.)\n"
        f"- `await tools.{OUTPUT_SINK_NAME}({{ {OUTPUT_SINK_ARG}: ... }})` is how you "
        "return — pass a plain JSON value (object/array/string/number), no "
        "`JSON.stringify` needed.\n"
        f"- Optional progress (shown live to the user): "
        f"`await tools.{PHASE_SINK_NAME}({{ {PHASE_SINK_ARG}: 'gather' }})` to start a "
        f"named phase, `await tools.{LOG_SINK_NAME}({{ {LOG_SINK_ARG}: '3/10 done' }})` "
        "for a free-text line. Both are safe no-ops if unobserved — use them to narrate "
        "long runs.\n"
        f"- One-shot model call (for a judgment — classify, score, extract): "
        f"`const text = await tools.{LLM_SINK_NAME}({{ {LLM_SINK_ARG}: '...', system: '...' }})`. "
        "Returns reply text; `JSON.parse(...)` it yourself if you asked for JSON.\n"
        "- You are writing this BLIND — you cannot run it or inspect intermediate "
        "values. Do NOT assume a tool's exact result field names; if a result "
        "shape is uncertain, include the whole value rather than guessing a field, "
        "so no data is silently dropped.\n\n"
        "Output ONLY the JavaScript body — no prose, no markdown fences."
    )


def _render_tool_signature(tool: BaseTool) -> str:
    """``tools.<camel>({args}) — <one-line description>`` from public attributes."""
    name = getattr(tool, "name", "?")
    args = getattr(tool, "args", None) or {}
    arg_str = "{ " + ", ".join(args) + " }" if args else ""
    desc = (getattr(tool, "description", "") or "").strip().splitlines()
    first_line = desc[0] if desc else ""
    sig = f"tools.{to_camel_case(name)}({arg_str})"
    return f"{sig} — {first_line}" if first_line else sig


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


def select_workflow_tools(
    tools: Sequence[BaseTool], uses_tools: Sequence[str] | None
) -> list[BaseTool]:
    """Pick the live tools a workflow's ``@uses`` (or ``uses_tools``) declares.

    ``uses_tools`` names the **sandbox surface** — the camelCase identifiers the
    body actually calls (``tools.webFetch``) — while a live tool's ``.name`` is its
    registered, often snake_case, name (``web_fetch``). Matching the two naïvely
    (``name in wanted``) silently drops every tool whose camelCase differs from its
    snake_case name, so the body fails with ``TypeError: not a function`` on the
    first such call. Match on **either** spelling — the camelCase form via the
    canonical :func:`~langclaw.naming.to_camel_case` (the same mapping PTC installs
    with, so the match cannot drift from the live sandbox surface).

    Args:
        tools:      The live toolset to narrow (already role-filtered).
        uses_tools: The workflow's declared tool names (camelCase or snake_case).

    Returns:
        The subset of *tools* the workflow declared, preserving input order.

    Raises:
        ValueError: if two selected tools map to the same JS identifier (one would
            silently shadow the other in the sandbox).
    """
    wanted = set(uses_tools or [])
    if not wanted:
        return []
    selected = [
        t
        for t in tools
        if (getattr(t, "name", None) in wanted)
        or (to_camel_case(getattr(t, "name", "") or "") in wanted)
    ]
    reject_camel_collisions([getattr(t, "name", "") for t in selected])
    return selected


# Deepagents injects these backend file tools *inside* ``create_deep_agent``, bound
# to an injected LangGraph ``ToolRuntime`` the workflow PTC bridge can't supply.
# They are reachable from the ``eval`` interpreter (it bridges the live per-call
# ``request.tools`` together with that runtime) but NOT from a saved/authored
# workflow, whose ``@uses`` resolves only the langclaw-registered live toolset.
_BACKEND_FILE_TOOLS: frozenset[str] = frozenset(
    {"read_file", "write_file", "edit_file", "ls", "glob", "grep", "execute"}
)


def _available_uses_names(tools: Sequence[BaseTool]) -> set[str]:
    """Every spelling an ``@uses`` entry may use to name a live tool.

    A tool is nameable by its registered ``.name`` (snake_case) *or* its canonical
    camelCase sandbox surface — the two forms :func:`select_workflow_tools` matches.
    """
    names: set[str] = set()
    for t in tools:
        name = getattr(t, "name", "") or ""
        if name:
            names.add(name)
            names.add(to_camel_case(name))
    return names


def _is_backend_file_tool(name: str) -> bool:
    """Whether *name* (snake_case or camelCase) is a deepagents backend file tool."""
    return name in _BACKEND_FILE_TOOLS or name in {to_camel_case(n) for n in _BACKEND_FILE_TOOLS}


def unresolved_workflow_tools(
    tools: Sequence[BaseTool], uses_tools: Sequence[str] | None
) -> list[str]:
    """Names a workflow ``@uses``-declares that match no live tool (declaration order).

    The complement of :func:`select_workflow_tools` (which returns the *selected*
    tools): this returns the *declared-but-missing* names, so a caller can fail fast
    instead of letting the body hit ``TypeError: not a function`` in the sandbox.
    """
    wanted = list(uses_tools or [])
    if not wanted:
        return []
    available = _available_uses_names(tools)
    return [w for w in wanted if w not in available]


def resolve_workflow_tools(
    tools: Sequence[BaseTool],
    uses_tools: Sequence[str] | None,
    *,
    workflow_name: str = "",
) -> list[BaseTool]:
    """Select a workflow's ``@uses`` tools, failing fast if any don't resolve.

    Same selection as :func:`select_workflow_tools`, but a declared name that
    resolves to nothing raises a clear, actionable ``ValueError`` (naming the
    workflow and the offending tools) instead of the cryptic in-sandbox
    ``TypeError: not a function``. The common mistake is declaring a deepagents
    backend file tool (``read_file`` / ``write_file`` / …), which a workflow cannot
    reach — those get a specific hint pointing at ``web_fetch``.

    Args:
        tools:         The live toolset to narrow (already role-filtered).
        uses_tools:    The workflow's declared tool names (camelCase or snake_case).
        workflow_name: The workflow's name, woven into the error message.

    Returns:
        The subset of *tools* the workflow declared, preserving input order.

    Raises:
        ValueError: if any declared name resolves to no live tool.
    """
    missing = unresolved_workflow_tools(tools, uses_tools)
    if not missing:
        return select_workflow_tools(tools, uses_tools)

    label = f"Workflow {workflow_name!r}" if workflow_name else "Workflow"
    if any(_is_backend_file_tool(m) for m in missing):
        hint = (
            " The deepagents backend file tools "
            "(read_file / write_file / edit_file / ls / glob / grep) are NOT reachable "
            "from a workflow's @uses — only langclaw-registered tools are (e.g. "
            "web_search, web_fetch, cron, move_file, delete_file). Use web_fetch to "
            "read a URL or file, or drop the @uses entry."
        )
    else:
        hint = (
            " @uses must name a langclaw-registered tool; check the spelling "
            "(snake_case or camelCase)."
        )
    raise ValueError(f"{label} declares @uses {missing} but they resolve to no live tool.{hint}")


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
