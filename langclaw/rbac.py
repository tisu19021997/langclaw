"""
Unified capability RBAC — one resolver for every permission axis (issue #37).

langclaw gates three independent capability *axes* behind a role:

- **tools**     — which ``@app.tool`` functions a role may invoke.
- **subagents** — which ``app.subagent`` types a role may reach via ``tools.task``.
- **workflows** — which registered workflows a role may run (``workflow_<name>``).

These used to be three hand-written helpers with *divergent* unknown-role
semantics and three enforcement seams that had to be kept in lockstep by eye —
every new axis multiplied that drift surface. All three axes are now
**default-deny** for unknown roles: enabling RBAC restricts every axis for
unlisted users. The per-axis :attr:`~CapabilityAxis.unknown_role_grants_all`
knob survives (a future "anonymous/public" axis could opt into pass-through) but
no *shipped* axis sets it.

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
            the whole universe (pass-through). ``False`` → an unknown role gets
            nothing (default-deny). Every *shipped* axis sets ``False`` so that
            enabling RBAC restricts unlisted users on every axis; the ``True``
            branch remains for a future opt-in pass-through axis (e.g. an
            anonymous/public capability).
        tool_prefix: For axes enforced by the model-call tool filter: tools whose
            ``.name`` starts with this prefix belong to this axis. ``None`` for
            the residual tool axis and for axes not enforced by that filter.
        is_residual_tool_axis: ``True`` for the one axis that owns every tool
            matched by *no* prefix (the bare tool namespace). Exactly one axis
            sets this.
        arg_gated: ``True`` for an axis enforced by a dedicated *tool-argument*
            gate rather than the tool-list filter — e.g. ``subagents`` is checked
            on ``task``'s ``subagent_type`` argument. This is what makes such an
            axis :attr:`enforceable` despite mapping to no tool name; declaring it
            keeps the enforcement shape explicit (no hard-coded special cases in
            the validator) so a new axis can't silently end up enforced *nowhere*.
    """

    name: str
    role_field: str
    unknown_role_grants_all: bool
    tool_prefix: str | None = None
    is_residual_tool_axis: bool = False
    arg_gated: bool = False

    @property
    def maps_to_tools(self) -> bool:
        """Whether this axis is enforced by the model-call tool-list filter.

        ``False`` for axes gated by another mechanism — e.g. ``subagents`` is
        checked per-argument on the ``task`` call, not by filtering a tool list.
        """
        return self.tool_prefix is not None or self.is_residual_tool_axis

    @property
    def enforceable(self) -> bool:
        """Whether *some* seam actually enforces this axis.

        An axis is enforceable iff it is wired to one of the three enforcement
        shapes: a prefixed tool axis, the residual tool axis, or an arg-gated
        axis. A declared-but-unenforceable axis would resolve to a default-deny
        set that *nothing reads* — i.e. it would silently grant everyone
        everything — so :func:`validate_capability_registry` rejects it.
        """
        return self.maps_to_tools or self.arg_gated


#: The tool axis — every plain ``@app.tool``. **Default-deny** for unknown roles,
#: like every other axis: enabling RBAC restricts tools for *everyone*, including
#: an unlisted user whose ``default_role`` was never registered. (Before this it
#: was unknown-role *pass-through*, which silently granted all tools to unlisted
#: users the moment RBAC was turned on — the opposite of what "enable RBAC"
#: implies.) When RBAC is *disabled* this filter is never installed, so every user
#: still sees every tool — the open-by-default case is handled at the wiring layer
#: (``agents/builder.py``), not by this flag. Owns the residual (un-prefixed)
#: namespace.
TOOLS = CapabilityAxis(
    name="tools",
    role_field="tools",
    unknown_role_grants_all=False,
    is_residual_tool_axis=True,
)

#: The subagent axis — ``tools.task({subagent_type})`` targets. **Default-deny**;
#: enforced per-argument on the ``task`` call (and coarsely in the PTC surface),
#: not by filtering a tool list, so it maps to no tool bucket — ``arg_gated``
#: marks that dedicated enforcement shape.
SUBAGENTS = CapabilityAxis(
    name="subagents",
    role_field="subagents",
    unknown_role_grants_all=False,
    arg_gated=True,
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
    # ``universe_set`` is a fresh set owned by this call, so it can be returned
    # directly — no defensive copy needed.
    universe_set = None if universe is None else set(universe)

    role_cfg = config.roles.get(role)
    if role_cfg is None:
        if axis.unknown_role_grants_all and universe_set is not None:
            return universe_set
        return set()

    grants = set(getattr(role_cfg, axis.role_field, None) or ())
    if universe_set is None:
        return grants
    if "*" in grants:
        return universe_set
    return grants & universe_set


def validate_capability_registry(
    axes: tuple[CapabilityAxis, ...] | None = None,
    role_config_cls: type | None = None,
) -> None:
    """Fail LOUD on a half-wired capability registry — never silently fail-open.

    The whole point of the registry is "add one axis and every seam picks it up".
    The failure mode that subverts that is an axis declared in the registry but
    *not actually reachable by any enforcement seam*, or one whose backing
    ``RoleConfig`` field was never added — both resolve to a default that no code
    reads, silently granting or denying everyone. This guard converts those into a
    startup ``ValueError`` instead. Called by
    :func:`langclaw.middleware.permissions.build_capability_filter_middleware`
    (structural checks, on every agent build) and by
    :func:`langclaw.agents.builder.create_claw_agent` (full checks, incl. fields
    and reserved prefixes).

    Args:
        axes: The registry to validate (defaults to :data:`CAPABILITY_AXES`).
        role_config_cls: When given, also assert every axis's ``role_field`` is a
            real attribute on it — so a forgotten ``RoleConfig`` field is caught
            rather than silently resolving every role to no grant.

    Raises:
        ValueError: If an axis is unenforceable, the residual axis is not unique,
            two axes share a ``tool_prefix``, a prefix is unreserved, or (with
            *role_config_cls*) a ``role_field`` is missing.
    """
    from langclaw.naming import RESERVED_TOOL_PREFIXES

    axes = CAPABILITY_AXES if axes is None else axes

    residual = [a for a in axes if a.is_residual_tool_axis]
    if len(residual) != 1:
        raise ValueError(
            "Capability registry must have exactly one residual tool axis "
            f"(is_residual_tool_axis=True); found {[a.name for a in residual]}."
        )

    seen_prefixes: dict[str, str] = {}
    for axis in axes:
        if not axis.enforceable:
            raise ValueError(
                f"Capability axis {axis.name!r} is declared but enforced nowhere: it "
                "is neither a prefixed tool axis (tool_prefix=...), the residual tool "
                "axis (is_residual_tool_axis=True), nor arg-gated (arg_gated=True). "
                "It would resolve to a permission set no seam reads — silently "
                "granting everyone everything. Wire it to an enforcement seam or "
                "drop it."
            )
        if axis.tool_prefix is not None:
            if axis.tool_prefix in seen_prefixes:
                raise ValueError(
                    f"Capability axes {seen_prefixes[axis.tool_prefix]!r} and "
                    f"{axis.name!r} share tool_prefix {axis.tool_prefix!r}; a tool "
                    "would be classified into both."
                )
            seen_prefixes[axis.tool_prefix] = axis.name
            if axis.tool_prefix not in RESERVED_TOOL_PREFIXES:
                raise ValueError(
                    f"Capability axis {axis.name!r} uses tool_prefix "
                    f"{axis.tool_prefix!r}, which is not reserved in "
                    "langclaw.naming.RESERVED_TOOL_PREFIXES — a developer tool could "
                    "be registered with that prefix and silently fall under RBAC. "
                    "Reserve the prefix there too."
                )

    if role_config_cls is None:
        return

    probe = role_config_cls()
    for axis in axes:
        if not hasattr(probe, axis.role_field):
            raise ValueError(
                f"Capability axis {axis.name!r} declares role_field "
                f"{axis.role_field!r}, but {role_config_cls.__name__} has no such "
                "field — add it so roles can grant this axis (otherwise every role "
                "silently resolves to no grant)."
            )


# Validate the shipped registry's structure at import: a malformed built-in
# registry (e.g. a new axis added without an enforcement shape) fails immediately
# rather than at some later agent build.
validate_capability_registry()


__all__ = [
    "CAPABILITY_AXES",
    "SUBAGENTS",
    "TOOLS",
    "WORKFLOWS",
    "CapabilityAxis",
    "resolve_capability",
    "validate_capability_registry",
]
