"""
Unified capability RBAC — one resolver, one seam, one place a new axis plugs in.

Issue #37: the three permission axes (tools, subagents, workflows) used to be
three ad-hoc helpers with *divergent* semantics (tools pass-through for unknown
roles; subagents/workflows default-deny) and *three* enforcement seams that had
to stay in lockstep by hand.  These tests pin the unified model:

1. A :class:`~langclaw.rbac.CapabilityAxis` descriptor declares each axis once —
   its ``RoleConfig`` field and the single default-deny-vs-pass-through decision.
2. :func:`~langclaw.rbac.resolve_capability` is the one resolver every axis (and
   every legacy helper) routes through, so they cannot drift.
3. :func:`~langclaw.middleware.permissions.build_capability_filter_middleware`
   is the single ``wrap_model_call`` seam governing every tool-name-mapped axis
   (tools + workflows) in one pass.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest


def _perms(roles: dict, *, enabled: bool = True, default_role: str = "viewer"):
    from langclaw.config.schema import PermissionsConfig

    return PermissionsConfig(enabled=enabled, roles=roles, default_role=default_role)


def _role(**kw):
    from langclaw.config.schema import RoleConfig

    return RoleConfig(**kw)


# ---------------------------------------------------------------------------
# The registry — every axis declared once, with its semantics flag
# ---------------------------------------------------------------------------


def test_registry_declares_three_axes_once():
    from langclaw.rbac import CAPABILITY_AXES, SUBAGENTS, TOOLS, WORKFLOWS

    by_name = {a.name: a for a in CAPABILITY_AXES}
    assert set(by_name) == {"tools", "subagents", "workflows"}
    # Each axis is in the registry exactly once.
    assert len(CAPABILITY_AXES) == 3
    assert by_name["tools"] is TOOLS
    assert by_name["subagents"] is SUBAGENTS
    assert by_name["workflows"] is WORKFLOWS


def test_semantics_decision_is_a_single_per_axis_flag():
    from langclaw.rbac import SUBAGENTS, TOOLS, WORKFLOWS

    # The ONE divergence — unknown-role pass-through vs default-deny — is now a
    # single boolean on each axis, not scattered control flow.
    assert TOOLS.unknown_role_grants_all is True
    assert SUBAGENTS.unknown_role_grants_all is False
    assert WORKFLOWS.unknown_role_grants_all is False


def test_each_axis_maps_to_a_role_config_field():
    from langclaw.config.schema import RoleConfig
    from langclaw.rbac import CAPABILITY_AXES

    for axis in CAPABILITY_AXES:
        assert hasattr(RoleConfig(), axis.role_field), axis.name


def test_only_one_residual_tool_axis():
    """Exactly one axis owns the bare (un-prefixed) tool namespace."""
    from langclaw.rbac import CAPABILITY_AXES, TOOLS

    residual = [a for a in CAPABILITY_AXES if a.is_residual_tool_axis]
    assert residual == [TOOLS]


def test_workflow_axis_is_classified_by_the_workflow_prefix():
    from langclaw.naming import WORKFLOW_TOOL_PREFIX
    from langclaw.rbac import SUBAGENTS, WORKFLOWS

    assert WORKFLOWS.tool_prefix == WORKFLOW_TOOL_PREFIX
    # Subagents are gated per-argument on the `task` call, not by a tool name —
    # so the axis maps to no tool-list bucket at all.
    assert SUBAGENTS.maps_to_tools is False
    assert WORKFLOWS.maps_to_tools is True


# ---------------------------------------------------------------------------
# resolve_capability — the single resolver, one behaviour per semantics flag
# ---------------------------------------------------------------------------


def test_pass_through_axis_unknown_role_grants_universe():
    from langclaw.rbac import TOOLS, resolve_capability

    cfg = _perms({})  # no roles → every role is "unknown"
    out = resolve_capability(TOOLS, cfg, "nobody", universe=["a", "b", "c"])
    assert out == {"a", "b", "c"}


def test_default_deny_axis_unknown_role_grants_nothing():
    from langclaw.rbac import WORKFLOWS, resolve_capability

    cfg = _perms({})
    out = resolve_capability(WORKFLOWS, cfg, "nobody", universe=["x", "y"])
    assert out == set()


def test_wildcard_grants_full_universe():
    from langclaw.rbac import TOOLS, WORKFLOWS, resolve_capability

    cfg = _perms({"r": _role(tools=["*"], workflows=["*"])})
    assert resolve_capability(TOOLS, cfg, "r", universe=["a", "b"]) == {"a", "b"}
    assert resolve_capability(WORKFLOWS, cfg, "r", universe=["w1", "w2"]) == {"w1", "w2"}


def test_listed_grants_intersect_universe():
    from langclaw.rbac import TOOLS, resolve_capability

    cfg = _perms({"r": _role(tools=["a", "ghost"])})
    # "ghost" is not on offer → dropped; only available grants survive.
    assert resolve_capability(TOOLS, cfg, "r", universe=["a", "b"]) == {"a"}


def test_universeless_axis_returns_grants_verbatim():
    """Subagents resolve with no universe — '*' is preserved for later checking."""
    from langclaw.rbac import SUBAGENTS, resolve_capability

    cfg = _perms({"r": _role(subagents=["*"])})
    assert resolve_capability(SUBAGENTS, cfg, "r") == {"*"}

    cfg2 = _perms({"r": _role(subagents=["researcher"])})
    assert resolve_capability(SUBAGENTS, cfg2, "r") == {"researcher"}

    # Unknown role is still default-deny even without a universe.
    assert resolve_capability(SUBAGENTS, _perms({}), "nobody") == set()


# ---------------------------------------------------------------------------
# Legacy helpers must be exact delegations — they cannot drift from the core
# ---------------------------------------------------------------------------


def test_legacy_helpers_equal_resolve_capability():
    from langclaw.middleware.permissions import (
        allowed_subagents,
        allowed_tool_names,
        allowed_workflow_names,
    )
    from langclaw.rbac import SUBAGENTS, TOOLS, WORKFLOWS, resolve_capability

    cfg = _perms(
        {
            "power": _role(tools=["*"], subagents=["researcher"], workflows=["digest"]),
            "viewer": _role(tools=["web_search"]),
        }
    )
    tool_universe = ["web_search", "delete_file"]
    wf_universe = ["digest", "secret"]

    for role in ("power", "viewer", "ghost"):
        assert allowed_tool_names(cfg, role, tool_universe) == resolve_capability(
            TOOLS, cfg, role, tool_universe
        )
        assert allowed_subagents(cfg, role) == resolve_capability(SUBAGENTS, cfg, role)
        assert allowed_workflow_names(cfg, role, wf_universe) == resolve_capability(
            WORKFLOWS, cfg, role, wf_universe
        )


# ---------------------------------------------------------------------------
# The single enforcement seam — one filter governs every tool-mapped axis
# ---------------------------------------------------------------------------


def _tool(name):
    return SimpleNamespace(name=name)


def _run_filter(mw, tools, role):
    runtime = SimpleNamespace(context=SimpleNamespace(user_role=role))
    request = SimpleNamespace(
        runtime=runtime,
        tools=tools,
        override=lambda **kw: SimpleNamespace(**{"tools": tools, "runtime": runtime, **kw}),
    )
    captured = {}

    async def handler(req):
        captured["tools"] = req.tools
        return "ok"

    asyncio.run(mw.awrap_model_call(request, handler))
    return [t.name for t in captured["tools"]]


def test_unified_filter_governs_tools_and_workflows_in_one_pass():
    from langclaw.middleware.permissions import build_capability_filter_middleware

    cfg = _perms({"power": _role(tools=["web_search"], workflows=["digest"])})
    mw = build_capability_filter_middleware(cfg)

    tools = [
        _tool("web_search"),
        _tool("delete_file"),
        _tool("workflow_digest"),
        _tool("workflow_secret"),
    ]
    names = _run_filter(mw, tools, "power")
    # tool axis: only web_search; workflow axis (default-deny): only digest.
    assert names == ["web_search", "workflow_digest"]


def test_unified_filter_preserves_order():
    from langclaw.middleware.permissions import build_capability_filter_middleware

    cfg = _perms({"r": _role(tools=["*"], workflows=["*"])})
    mw = build_capability_filter_middleware(cfg)
    tools = [_tool("b"), _tool("workflow_z"), _tool("a")]
    assert _run_filter(mw, tools, "r") == ["b", "workflow_z", "a"]


def test_unified_filter_default_deny_strips_unlisted_workflow():
    """A role with tools=['*'] still cannot reach a workflow it was not granted."""
    from langclaw.middleware.permissions import build_capability_filter_middleware

    cfg = _perms({"viewer": _role(tools=["*"])})  # no workflows granted
    mw = build_capability_filter_middleware(cfg)
    tools = [_tool("web_search"), _tool("workflow_digest")]
    assert _run_filter(mw, tools, "viewer") == ["web_search"]


def test_unified_filter_passes_through_when_role_allows_all():
    from langclaw.middleware.permissions import build_capability_filter_middleware

    cfg = _perms({"admin": _role(tools=["*"], workflows=["*"])})
    mw = build_capability_filter_middleware(cfg)
    tools = [_tool("web_search"), _tool("workflow_digest")]
    # Nothing stripped → same list object passed through (no override).
    assert _run_filter(mw, tools, "admin") == ["web_search", "workflow_digest"]


@pytest.mark.parametrize("legacy", ["tool", "workflow"])
def test_legacy_middleware_builders_alias_the_unified_seam(legacy):
    """Old per-axis builders stay importable but now return the unified filter."""
    from langclaw.middleware import permissions as P

    builder = (
        P.build_tool_permission_middleware
        if legacy == "tool"
        else P.build_workflow_permission_middleware
    )
    cfg = _perms({"power": _role(tools=["web_search"], workflows=["digest"])})
    mw = builder(cfg)
    tools = [
        _tool("web_search"),
        _tool("delete_file"),
        _tool("workflow_digest"),
        _tool("workflow_secret"),
    ]
    # Both aliases enforce *both* axes now (single seam).
    assert _run_filter(mw, tools, "power") == ["web_search", "workflow_digest"]
