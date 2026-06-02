"""
Unified capability RBAC — one resolver for every permission axis (issue #37).

langclaw gates three independent capability *axes* behind a role:

- **tools**     — which ``@app.tool`` functions a role may invoke.
- **subagents** — which ``app.subagent`` types a role may reach via ``tools.task``.
- **workflows** — which registered workflows a role may run (``workflow_<name>``).

These used to be three hand-written helpers with *divergent* semantics
(unknown-role pass-through for tools vs default-deny for the other two) and
three enforcement seams that had to be kept in lockstep by eye — every new axis
multiplied that drift surface.

This module collapses the *resolution* model to one declaration per axis:

- :class:`CapabilityAxis` — a frozen descriptor binding an axis name to its
  ``RoleConfig`` field and the **single** policy decision
  (:attr:`~CapabilityAxis.unknown_role_grants_all`), plus how the model-call
  tool filter classifies a tool into the axis (prefix / residual / none).
- :data:`CAPABILITY_AXES` — the registry. A new axis is added by declaring one
  ``CapabilityAxis`` here and one field on ``RoleConfig``; every resolver and the
  unified filter pick it up for free.
- :func:`resolve_capability` — the one pure function every axis routes through.

Like :mod:`langclaw.naming`, this module imports nothing from langclaw at
runtime (only ``RoleConfig``/``PermissionsConfig`` types under ``TYPE_CHECKING``)
so any layer — middleware, interpreter, bridge, gateway — can depend on it
without import cycles.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from langclaw.naming import WORKFLOW_TOOL_PREFIX

if TYPE_CHECKING:
    from collections.abc import Iterable

    from langclaw.config.schema import PermissionsConfig


@dataclass(frozen=True)
class CapabilityAxis:
    """One RBAC axis, declared once.

    Attributes:
        name: Stable identifier (also the human label in logs).
        role_field: The :class:`~langclaw.config.schema.RoleConfig` attribute
            holding this axis's grant list (e.g. ``"tools"``).
        unknown_role_grants_all: The **single** semantics decision. ``True`` →
            an unknown role (not in ``config.roles``) is *not* filtered and gets
            the whole universe (pass-through, as the tool axis has always
            behaved). ``False`` → an unknown role gets nothing (default-deny, as
            subagents and workflows do).
        tool_prefix: For axes enforced by the model-call tool filter: tools whose
            ``.name`` starts with this prefix belong to this axis. ``None`` for
            the residual tool axis and for axes not enforced by that filter.
        is_residual_tool_axis: ``True`` for the one axis that owns every tool
            matched by *no* prefix (the bare tool namespace). Exactly one axis
            sets this.
    """

    name: str
    role_field: str
    unknown_role_grants_all: bool
    tool_prefix: str | None = None
    is_residual_tool_axis: bool = False

    @property
    def maps_to_tools(self) -> bool:
        """Whether this axis is enforced by the model-call tool-list filter.

        ``False`` for axes gated by another mechanism — e.g. ``subagents`` is
        checked per-argument on the ``task`` call, not by filtering a tool list.
        """
        return self.tool_prefix is not None or self.is_residual_tool_axis


#: The tool axis — every plain ``@app.tool``. **Pass-through** for unknown roles
#: (an unrecognised role sees the full toolset), matching langclaw's original
#: ``allowed_tool_names`` contract. Owns the residual (un-prefixed) namespace.
TOOLS = CapabilityAxis(
    name="tools",
    role_field="tools",
    unknown_role_grants_all=True,
    is_residual_tool_axis=True,
)

#: The subagent axis — ``tools.task({subagent_type})`` targets. **Default-deny**;
#: enforced per-argument on the ``task`` call (and coarsely in the PTC surface),
#: not by filtering a tool list, so it maps to no tool bucket.
SUBAGENTS = CapabilityAxis(
    name="subagents",
    role_field="subagents",
    unknown_role_grants_all=False,
)

#: The workflow axis — registered workflows exposed as ``workflow_<name>`` tools.
#: **Default-deny** (a workflow composes tools + subagents, so ``tools=["*"]``
#: must not implicitly grant it). Classified by the ``workflow_`` prefix.
WORKFLOWS = CapabilityAxis(
    name="workflows",
    role_field="workflows",
    unknown_role_grants_all=False,
    tool_prefix=WORKFLOW_TOOL_PREFIX,
)

#: The registry. Add a new axis here (+ a field on ``RoleConfig``) and every
#: resolver and the unified filter handle it automatically.
CAPABILITY_AXES: tuple[CapabilityAxis, ...] = (TOOLS, SUBAGENTS, WORKFLOWS)


def resolve_capability(
    axis: CapabilityAxis,
    config: PermissionsConfig,
    role: str,
    universe: Iterable[str] | None = None,
) -> set[str]:
    """Resolve the set of names *role* may use on *axis*, given the live universe.

    The single source of truth for every RBAC axis. Both the enforcement seams
    (the unified model-call filter, the ``task`` subagent gate) and the PTC
    allowlist resolvers route through here, so the axes cannot drift.

    Resolution:

    1. **Unknown role** (not in ``config.roles``) → the whole *universe* when
       :attr:`~CapabilityAxis.unknown_role_grants_all`, else the empty set.
    2. A grant list containing ``"*"`` → the whole *universe*.
    3. Otherwise the role's listed names, intersected with *universe*.

    When *universe* is ``None`` (the subagent axis, whose universe of registered
    types isn't available at resolve time) the role's grants are returned
    verbatim — ``"*"`` preserved for the caller to interpret — and an unknown
    role still yields the empty set under default-deny.

    Args:
        axis:     The capability axis descriptor.
        config:   RBAC definitions.
        role:     Resolved user role.
        universe: Names currently on offer for this axis, or ``None``.

    Returns:
        The allowed subset of *universe* (or the verbatim grants when
        *universe* is ``None``).
    """
    universe_set = None if universe is None else set(universe)

    role_cfg = config.roles.get(role)
    if role_cfg is None:
        if axis.unknown_role_grants_all:
            return set(universe_set) if universe_set is not None else set()
        return set()

    grants = set(getattr(role_cfg, axis.role_field, None) or ())
    if universe_set is None:
        return grants
    if "*" in grants:
        return set(universe_set)
    return grants & universe_set


__all__ = [
    "CAPABILITY_AXES",
    "SUBAGENTS",
    "TOOLS",
    "WORKFLOWS",
    "CapabilityAxis",
    "resolve_capability",
]
