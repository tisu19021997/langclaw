"""
Workflow registry and ``@app.workflow()`` binding (issue #38, Phase 1).

A :class:`WorkflowRegistry` stores :class:`WorkflowSpec` entries — one per
operator-authored workflow — and refuses to register a name that collides with
an existing workflow or any reserved name (tools, subagents, named agents,
commands), so dispatch is never ambiguous.

This module is pure and import-light: it depends on nothing in the runtime or
bus, so it can be unit-tested in isolation.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

#: The orchestration authoring modes a :class:`WorkflowSpec` may declare.
#:
#: - ``python``       — operator authors the async body (``@app.workflow``).
#: - ``llm_authored`` — the LLM authors the body fresh per run from the contract.
#: - ``saved``        — a body authored at runtime (a ``workflows/<name>.js`` file
#:                      the agent writes) and frozen to disk; reused verbatim.
_VALID_MODES = frozenset({"python", "llm_authored", "saved"})


@dataclass(slots=True)
class WorkflowSpec:
    """A registered workflow.

    Attributes:
        name:         Unique handle used to invoke the workflow (tool name
                      ``workflow_<name>``, ``/workflows run <name>``, cron, PTC).
        fn:           The async body — ``async def (ctx, inp) -> output``.
        description:  One-line human/LLM-facing summary.
        input_model:  Optional Pydantic model validating the run input.
        output_model: Optional Pydantic model validating the run output.
        mode:         ``"python"`` (operator-authored, Phase 1) or
                      ``"llm_authored"`` (Mode 2, later phase).
        max_steps:        Per-workflow step-count budget (``None`` → use global).
        max_concurrency:  Per-workflow fan-out width for ``ctx.parallel``.
        timeout_s:        Per-run wall-clock budget in seconds (``None`` → none).
    """

    name: str
    fn: Callable[..., Any]
    description: str = ""
    input_model: type | None = None
    output_model: type | None = None
    mode: str = "python"
    max_steps: int | None = None
    max_concurrency: int = 8
    timeout_s: float | None = None

    uses_tools: list[str] = field(default_factory=list)
    """Tool names this workflow declares it needs, validated at registration."""

    script: str | None = None
    """The frozen JS body for ``mode="saved"`` (authored at runtime). ``None`` for
    other modes."""

    def __post_init__(self) -> None:
        if self.mode not in _VALID_MODES:
            raise ValueError(
                f"Workflow {self.name!r}: unknown mode {self.mode!r}. "
                f"Expected one of {sorted(_VALID_MODES)}."
            )
        # An llm_authored workflow has no Python body — the description is the
        # spec the LLM writes the body from, so it must be present.
        if self.mode == "llm_authored" and not (self.description or "").strip():
            raise ValueError(
                f"Workflow {self.name!r}: mode='llm_authored' requires a non-empty "
                "`description` — it is the spec the LLM authors the body from."
            )
        # A saved workflow's body is its frozen `script`; without one there is
        # nothing to run.
        if self.mode == "saved" and not (self.script or "").strip():
            raise ValueError(
                f"Workflow {self.name!r}: mode='saved' requires a non-empty `script` "
                "— the frozen JS body authored at runtime."
            )

    def validate_input(self, value: Any) -> Any:
        """Coerce/validate *value* against ``input_model`` when declared.

        Returns the validated model instance (or the raw value when no model is
        declared).  Raises whatever the Pydantic model raises on bad input —
        callers at the run boundary convert that to a clean error.
        """
        if self.input_model is None:
            return value
        if isinstance(value, self.input_model):
            return value
        if isinstance(value, dict):
            return self.input_model(**value)
        return self.input_model.model_validate(value)


class WorkflowRegistry:
    """An ordered, collision-checked store of :class:`WorkflowSpec`."""

    def __init__(self) -> None:
        self._by_name: dict[str, WorkflowSpec] = {}
        # Monotonic counter bumped on every successful (de)registration. The
        # gateway compares it to detect a runtime-authored (file-written)
        # registration and rebuild the agent so the new `workflow_<name>` tool
        # goes live in the same session — the registry-native analogue of the
        # AGENTS.md content hash.
        self._version = 0

    @property
    def version(self) -> int:
        """Monotonic registry version — changes whenever a workflow is added."""
        return self._version

    def register(
        self,
        spec: WorkflowSpec,
        *,
        reserved_names: Iterable[str] = (),
    ) -> WorkflowSpec:
        """Register *spec*, rejecting name collisions.

        Args:
            spec:           The workflow to register.
            reserved_names: Names already claimed by tools, subagents, named
                            agents, or commands.  A clash raises ``ValueError``
                            so ambiguous dispatch is impossible.

        Raises:
            ValueError: If the name is empty, already registered, or reserved.
        """
        name = spec.name
        if not name or not name.strip():
            raise ValueError("Workflow name must be a non-empty string.")
        if name in self._by_name:
            raise ValueError(f"Workflow {name!r} is already registered.")
        reserved = set(reserved_names)
        if name in reserved:
            raise ValueError(
                f"Workflow name {name!r} collides with an existing tool, "
                "subagent, agent, or command. Choose a unique name."
            )
        self._by_name[name] = spec
        self._version += 1
        return spec

    def unregister(self, name: str) -> bool:
        """Remove the workflow registered under *name*; return whether one existed.

        Bumps the version (like :meth:`register`) so a removal also triggers an
        agent rebuild.  Used to roll back a runtime registration whose on-disk
        persistence failed, so the registry never holds an unpersisted workflow.
        """
        if name in self._by_name:
            del self._by_name[name]
            self._version += 1
            return True
        return False

    def get(self, name: str) -> WorkflowSpec | None:
        """Return the spec registered under *name*, or ``None``."""
        return self._by_name.get(name)

    def names(self) -> list[str]:
        """Registered workflow names, in registration order."""
        return list(self._by_name)

    def specs(self) -> list[WorkflowSpec]:
        """All registered specs, in registration order."""
        return list(self._by_name.values())

    def __contains__(self, name: object) -> bool:
        return name in self._by_name

    def __len__(self) -> int:
        return len(self._by_name)
