"""
Phase 0 — enforce ``RoleConfig.subagents`` on the deepagents ``task`` tool.

The per-role subagent allowlist (``allowed_subagents`` /
``check_subagent_permission``) was defined and unit-tested but never wired into
production, so ``tools.task`` was effectively ungated.  These tests pin the two
enforcement seams, both fed by the same pure helpers so they cannot drift:

1. **Model path** — ``build_subagent_permission_middleware`` (a ``wrap_tool_call``
   middleware) intercepts a ``task`` call, resolves the caller's role, and
   short-circuits a disallowed ``subagent_type`` with an error ``ToolMessage``
   (never raising into the agent loop).  Full per-type granularity.
2. **PTC path** — ``resolve_ptc_allowlist`` drops ``task`` from a script's tool
   surface when the role may use *zero* subagents (coarse gate; finer per-type
   PTC gating is tracked in #37).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest


def _task_request(subagent_type: str, role: str | None = "viewer"):
    """Build a minimal ``ToolCallRequest``-shaped object for the gate.

    Only the attributes the middleware reads are populated: ``tool_call`` and
    ``runtime.context.user_role``.
    """
    ctx = SimpleNamespace(user_role=role) if role is not None else None
    runtime = SimpleNamespace(context=ctx)
    return SimpleNamespace(
        tool_call={
            "name": "task",
            "args": {"subagent_type": subagent_type, "description": "do it"},
            "id": "call_1",
        },
        runtime=runtime,
    )


def _perms(roles: dict):
    from langclaw.config.schema import PermissionsConfig

    return PermissionsConfig(enabled=True, roles=roles)


# ---------------------------------------------------------------------------
# Model path — wrap_tool_call middleware
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_gate_blocks_disallowed_subagent():
    from langclaw.config.schema import RoleConfig
    from langclaw.middleware.permissions import build_subagent_permission_middleware

    cfg = _perms({"viewer": RoleConfig(tools=["*"], subagents=["researcher"])})
    mw = build_subagent_permission_middleware(cfg)

    called = {"handler": False}

    async def handler(_req):
        called["handler"] = True
        return "ran"

    out = await mw.awrap_tool_call(_task_request("admin_agent"), handler)

    # Denied → handler never runs, an error ToolMessage is returned.
    assert called["handler"] is False
    content = getattr(out, "content", out)
    assert "admin_agent" in str(content)
    assert getattr(out, "status", "error") == "error"


@pytest.mark.asyncio
async def test_gate_allows_permitted_subagent():
    from langclaw.config.schema import RoleConfig
    from langclaw.middleware.permissions import build_subagent_permission_middleware

    cfg = _perms({"viewer": RoleConfig(tools=["*"], subagents=["researcher"])})
    mw = build_subagent_permission_middleware(cfg)

    called = {"handler": False}

    async def handler(_req):
        called["handler"] = True
        return "ran"

    out = await mw.awrap_tool_call(_task_request("researcher"), handler)
    assert called["handler"] is True
    assert out == "ran"


@pytest.mark.asyncio
async def test_gate_wildcard_allows_any_subagent():
    from langclaw.config.schema import RoleConfig
    from langclaw.middleware.permissions import build_subagent_permission_middleware

    cfg = _perms({"power": RoleConfig(tools=["*"], subagents=["*"])})
    mw = build_subagent_permission_middleware(cfg)

    async def handler(_req):
        return "ran"

    out = await mw.awrap_tool_call(_task_request("anything", role="power"), handler)
    assert out == "ran"


@pytest.mark.asyncio
async def test_gate_default_deny_for_role_without_subagents():
    from langclaw.config.schema import RoleConfig
    from langclaw.middleware.permissions import build_subagent_permission_middleware

    # Role lists no subagents → default-deny, even with tools=["*"].
    cfg = _perms({"viewer": RoleConfig(tools=["*"])})
    mw = build_subagent_permission_middleware(cfg)

    called = {"handler": False}

    async def handler(_req):
        called["handler"] = True
        return "ran"

    out = await mw.awrap_tool_call(_task_request("researcher"), handler)
    assert called["handler"] is False
    assert "researcher" in str(getattr(out, "content", out))


@pytest.mark.asyncio
async def test_gate_ignores_non_task_tools():
    from langclaw.config.schema import RoleConfig
    from langclaw.middleware.permissions import build_subagent_permission_middleware

    cfg = _perms({"viewer": RoleConfig(tools=["*"], subagents=["researcher"])})
    mw = build_subagent_permission_middleware(cfg)

    req = SimpleNamespace(
        tool_call={"name": "web_search", "args": {"query": "x"}, "id": "c2"},
        runtime=SimpleNamespace(context=SimpleNamespace(user_role="viewer")),
    )
    called = {"handler": False}

    async def handler(_req):
        called["handler"] = True
        return "searched"

    out = await mw.awrap_tool_call(req, handler)
    # A non-task tool is never gated by the subagent allowlist.
    assert called["handler"] is True
    assert out == "searched"


# ---------------------------------------------------------------------------
# PTC path — coarse gate in resolve_ptc_allowlist
# ---------------------------------------------------------------------------


def test_resolve_ptc_drops_task_when_role_has_no_subagents():
    from langclaw.config.schema import InterpreterConfig, RoleConfig
    from langclaw.interpreter import resolve_ptc_allowlist

    # task is a runtime-injected tool and in the read-only default, but the role
    # may use zero subagents → tools.task must not be exposed to the script.
    perms = _perms({"viewer": RoleConfig(tools=["*"])})
    out = resolve_ptc_allowlist(
        [SimpleNamespace(name="web_search")],
        interpreter_config=InterpreterConfig(enabled=True),
        permissions_config=perms,
        role="viewer",
        runtime_tool_names={"task"},
    )
    assert "task" not in out
    assert "web_search" in out


def test_resolve_ptc_keeps_task_when_subagents_allowed():
    from langclaw.config.schema import InterpreterConfig, RoleConfig
    from langclaw.interpreter import resolve_ptc_allowlist

    perms = _perms({"power": RoleConfig(tools=["*"], subagents=["researcher"])})
    out = resolve_ptc_allowlist(
        [SimpleNamespace(name="web_search")],
        interpreter_config=InterpreterConfig(enabled=True),
        permissions_config=perms,
        role="power",
        runtime_tool_names={"task"},
    )
    assert "task" in out


def test_resolve_ptc_task_unaffected_when_permissions_disabled():
    """With no role/permissions narrowing, the read-only default still has task."""
    from langclaw.config.schema import InterpreterConfig
    from langclaw.interpreter import resolve_ptc_allowlist

    out = resolve_ptc_allowlist(
        [SimpleNamespace(name="web_search")],
        interpreter_config=InterpreterConfig(enabled=True),
        runtime_tool_names={"task"},
    )
    assert "task" in out


# ---------------------------------------------------------------------------
# Wiring — the gate is actually in the builder's middleware stack
# ---------------------------------------------------------------------------


def _capture_deep_agent(monkeypatch):
    import deepagents

    captured: dict = {}

    def fake_create_deep_agent(**kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(deepagents, "create_deep_agent", fake_create_deep_agent)
    return captured


def test_builder_wires_subagent_gate_after_tool_permissions(monkeypatch):
    from langclaw.agents.builder import create_claw_agent
    from langclaw.config.schema import LangclawConfig

    cfg = LangclawConfig(interpreter={"enabled": False})
    cfg.permissions.enabled = True

    captured = _capture_deep_agent(monkeypatch)
    create_claw_agent(cfg, model=object())

    names = [type(m).__name__ for m in captured["middleware"]]
    # The gate is registered as a wrap_tool_call middleware named after its fn,
    # after the unified capability filter (issue #37).
    assert "_subagent_gate" in names
    assert names.index("_capability_filter") < names.index("_subagent_gate")


def test_builder_omits_subagent_gate_when_permissions_disabled(monkeypatch):
    from langclaw.agents.builder import create_claw_agent
    from langclaw.config.schema import LangclawConfig

    cfg = LangclawConfig(interpreter={"enabled": False})
    cfg.permissions.enabled = False

    captured = _capture_deep_agent(monkeypatch)
    create_claw_agent(cfg, model=object())

    names = [type(m).__name__ for m in captured["middleware"]]
    assert "_subagent_gate" not in names
