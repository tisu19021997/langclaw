"""The ``save_workflow`` built-in tool — runtime workflow authoring.

After the agent runs an ad-hoc job with the ``eval`` interpreter (a JavaScript
program that loops, branches, fans out subagents), the user may say *"save that
workflow"*.  This tool lets the agent persist that JS body as a named, reusable
workflow: it writes the script to the workspace ``workflows/`` folder
(:class:`~langclaw.workflows.saved_store.SavedWorkflowStore`) and registers it as
a ``mode="saved"`` :class:`~langclaw.workflows.registry.WorkflowSpec`.

Registering bumps the registry version; the gateway notices and rebuilds the
agent so the saved workflow goes live as a ``workflow_<name>`` tool in the same
session — exactly the tools the agent already sees for ``@app.workflow``
registrations, but authored at runtime.

Per the project convention, the tool returns a result dict (``{"message": ...}``
on success, ``{"error": ...}`` on failure) and never raises into the agent loop.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from loguru import logger
from pydantic import BaseModel, Field

from langclaw.naming import workflow_tool_name
from langclaw.workflows.saved_store import validate_saved_name

if TYPE_CHECKING:
    from langchain_core.tools import BaseTool

    from langclaw.workflows.registry import WorkflowRegistry
    from langclaw.workflows.saved_store import SavedWorkflowStore


class _SaveWorkflowArgs(BaseModel):
    name: str = Field(
        description=(
            "Short handle for the workflow (letters, digits, '_' or '-'). Becomes "
            "the `workflow_<name>` tool the user can run later."
        )
    )
    script: str = Field(
        description=(
            "The JavaScript body to save — the same sandboxed program you would "
            "pass to `eval`. It receives the run input as the global `inp` and must "
            "emit its result via `await tools.output({ result: <value> })`."
        )
    )
    description: str = Field(
        default="",
        description=(
            "One-line summary of what the workflow does and when to use it. Shown "
            "to you later as the `workflow_<name>` tool description, so write it as "
            "guidance, not a bare label."
        ),
    )
    uses_tools: list[str] = Field(
        default_factory=list,
        description=(
            "Names of the tools the script calls (e.g. ['web_fetch', 'write_file']). "
            "Narrows the sandbox capability set for the saved run."
        ),
    )


def build_save_workflow_tool(
    *,
    registry: WorkflowRegistry,
    store: SavedWorkflowStore,
    reserved_names: set[str],
) -> BaseTool:
    """Build the ``save_workflow`` tool bound to a registry + saved-workflow store.

    Args:
        registry:       The shared :class:`WorkflowRegistry`. A successful save
                        registers a ``mode="saved"`` spec here and bumps its
                        version so the gateway rebuilds the agent.
        store:          File-backed store persisting the JS body to the workspace
                        ``workflows/`` folder.
        reserved_names: Names already claimed by tools, subagents, agents, or
                        commands — a saved workflow may not collide with them.
    """
    from langchain_core.tools import StructuredTool

    from langclaw.workflows.registry import WorkflowSpec

    async def _save(
        name: str,
        script: str,
        description: str = "",
        uses_tools: list[str] | None = None,
    ) -> dict[str, Any]:
        # 1. Name must be a safe single segment (also a valid workflow_<name> tool).
        try:
            validate_saved_name(name)
        except ValueError as exc:
            return {"error": str(exc)}

        # 2. Body must be non-empty — there is nothing to run otherwise.
        if not (script or "").strip():
            return {"error": "Cannot save an empty workflow script."}

        # 3. Name must be free (no existing workflow, tool, subagent, or command).
        if name in registry:
            return {"error": f"A workflow named {name!r} already exists. Pick another name."}

        def _noop(ctx: Any, inp: Any) -> None:
            return None

        try:
            spec = WorkflowSpec(
                name=name,
                fn=_noop,
                description=description,
                mode="saved",
                script=script,
                uses_tools=list(uses_tools or []),
            )
            registry.register(spec, reserved_names=reserved_names)
        except ValueError as exc:
            return {"error": str(exc)}

        # 4. Persist only after the registry accepts it, so disk never holds a
        #    workflow the running app rejected.
        try:
            store.save(
                name,
                script=script,
                description=description,
                uses_tools=list(uses_tools or []),
            )
        except OSError as exc:
            return {"error": f"Registered but failed to persist {name!r} to disk: {exc}"}

        logger.info(f"save_workflow: registered + persisted {name!r}")
        tool_name = workflow_tool_name(name)
        return {
            "message": (
                f"Saved workflow {name!r}. It is now available as the `{tool_name}` "
                "tool and persists across restarts."
            )
        }

    return StructuredTool.from_function(
        coroutine=_save,
        name="save_workflow",
        description=(
            "Save a JavaScript program (the kind you run with `eval`) as a named, "
            "reusable workflow. Use this when the user asks to save or remember a "
            "multi-step task you just ran so it can be re-run later. The saved "
            "workflow becomes a `workflow_<name>` tool and persists across restarts."
        ),
        args_schema=_SaveWorkflowArgs,
    )
