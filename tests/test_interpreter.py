"""
Tests for the scripted code-interpreter feature (issue #33).

Covers the four deep modules in isolation, asserting external behaviour:

1. RBAC sharing + subagent-type gate (``langclaw.middleware.permissions``)
2. PTC allowlist resolver (``langclaw.interpreter``)
3. Interpreter middleware factory (``langclaw.interpreter``)
4. Enablement gate + config (``langclaw.config.schema`` / ``langclaw.app``)
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest


def _tool(name: str) -> SimpleNamespace:
    """Minimal stand-in for a ``BaseTool`` — only ``.name`` is read."""
    return SimpleNamespace(name=name)


# ---------------------------------------------------------------------------
# Module 4 — Config + enablement gate
# ---------------------------------------------------------------------------


def test_interpreter_config_defaults():
    from langclaw.config.schema import InterpreterConfig

    cfg = InterpreterConfig()
    assert cfg.enabled is False
    assert cfg.timeout == 5.0
    assert cfg.memory_limit == 64 * 1024 * 1024
    assert cfg.max_ptc_calls == 256
    assert cfg.max_result_chars == 4000
    # Fresh context per eval by default — avoids cross-call `const` redeclaration.
    assert cfg.snapshot_between_turns is False
    assert cfg.allow_tools == []


def test_interpreter_config_on_root():
    from langclaw.config.schema import LangclawConfig

    # Force off via init kwargs (overrides any ambient .env) so this asserts the
    # field is wired onto the root, independent of the developer's environment.
    cfg = LangclawConfig(interpreter={"enabled": False})
    assert cfg.interpreter.enabled is False


def test_interpreter_enabled_via_env(monkeypatch):
    monkeypatch.setenv("LANGCLAW__INTERPRETER__ENABLED", "true")
    monkeypatch.setenv("LANGCLAW__INTERPRETER__TIMEOUT", "12.5")
    from langclaw.config.schema import LangclawConfig

    cfg = LangclawConfig()
    assert cfg.interpreter.enabled is True
    assert cfg.interpreter.timeout == 12.5


def test_role_config_subagents_default_empty():
    from langclaw.config.schema import RoleConfig

    assert RoleConfig().subagents == []
    assert RoleConfig(subagents=["researcher"]).subagents == ["researcher"]


def test_app_interpreter_inactive_by_default():
    from langclaw.app import Langclaw
    from langclaw.config.schema import LangclawConfig

    app = Langclaw(config=LangclawConfig(interpreter={"enabled": False}))
    assert app._interpreter_active() is False


def test_app_interpreter_active_via_constructor_flag():
    from langclaw.app import Langclaw
    from langclaw.config.schema import LangclawConfig

    app = Langclaw(config=LangclawConfig(), enable_interpreter=True)
    assert app._interpreter_active() is True


def test_app_interpreter_active_via_config():
    from langclaw.app import Langclaw
    from langclaw.config.schema import InterpreterConfig, LangclawConfig

    cfg = LangclawConfig()
    cfg.interpreter = InterpreterConfig(enabled=True)
    app = Langclaw(config=cfg)
    assert app._interpreter_active() is True


def test_app_constructor_flag_flips_effective_config():
    """The constructor flag should make the effective config report enabled."""
    from langclaw.app import Langclaw
    from langclaw.config.schema import LangclawConfig

    app = Langclaw(config=LangclawConfig(), enable_interpreter=True)
    eff = app._build_effective_config()
    assert eff.interpreter.enabled is True


# ---------------------------------------------------------------------------
# Module 3 — RBAC sharing + subagent-type gate
# ---------------------------------------------------------------------------


def test_allowed_tool_names_wildcard_expands():
    from langclaw.config.schema import PermissionsConfig, RoleConfig
    from langclaw.middleware.permissions import allowed_tool_names

    cfg = PermissionsConfig(enabled=True, roles={"admin": RoleConfig(tools=["*"])})
    universe = ["web_search", "web_fetch", "delete_file"]
    assert allowed_tool_names(cfg, "admin", universe) == set(universe)


def test_allowed_tool_names_restricted_role():
    from langclaw.config.schema import PermissionsConfig, RoleConfig
    from langclaw.middleware.permissions import allowed_tool_names

    cfg = PermissionsConfig(enabled=True, roles={"viewer": RoleConfig(tools=["web_search"])})
    universe = ["web_search", "web_fetch", "delete_file"]
    assert allowed_tool_names(cfg, "viewer", universe) == {"web_search"}


def test_allowed_tool_names_unknown_role_allows_all():
    """Mirrors the middleware: an undefined role is not filtered."""
    from langclaw.config.schema import PermissionsConfig
    from langclaw.middleware.permissions import allowed_tool_names

    cfg = PermissionsConfig(enabled=True, roles={})
    universe = ["web_search", "web_fetch"]
    assert allowed_tool_names(cfg, "ghost", universe) == set(universe)


def test_allowed_subagents_default_deny():
    from langclaw.config.schema import PermissionsConfig, RoleConfig
    from langclaw.middleware.permissions import allowed_subagents

    cfg = PermissionsConfig(
        enabled=True,
        roles={
            "viewer": RoleConfig(tools=["web_search"]),  # no subagents listed
            "power": RoleConfig(tools=["*"], subagents=["researcher"]),
        },
    )
    assert allowed_subagents(cfg, "viewer") == set()
    assert allowed_subagents(cfg, "power") == {"researcher"}
    assert allowed_subagents(cfg, "ghost") == set()


def test_check_subagent_permission_rejects_out_of_allowlist():
    from langclaw.middleware.permissions import check_subagent_permission

    # Allowed → no error
    assert check_subagent_permission("researcher", {"researcher"}) is None
    # Not allowed → error string
    err = check_subagent_permission("admin_agent", {"researcher"})
    assert err is not None and "admin_agent" in err


def test_check_subagent_permission_wildcard():
    from langclaw.middleware.permissions import check_subagent_permission

    assert check_subagent_permission("anything", {"*"}) is None


def test_permission_middleware_uses_shared_helper(monkeypatch):
    """The middleware must filter via the same allowed_tool_names contract."""
    import asyncio

    from langclaw.config.schema import PermissionsConfig, RoleConfig
    from langclaw.middleware.permissions import build_tool_permission_middleware

    cfg = PermissionsConfig(enabled=True, roles={"viewer": RoleConfig(tools=["web_search"])})
    mw = build_tool_permission_middleware(cfg)

    tools = [_tool("web_search"), _tool("delete_file")]
    runtime = SimpleNamespace(context=SimpleNamespace(user_role="viewer"))
    request = SimpleNamespace(
        runtime=runtime,
        tools=tools,
        override=lambda **kw: SimpleNamespace(**{"tools": tools, **kw}),
    )

    captured = {}

    async def handler(req):
        captured["tools"] = req.tools
        return "ok"

    asyncio.run(mw.awrap_model_call(request, handler))
    names = {t.name for t in captured["tools"]}
    assert names == {"web_search"}


# ---------------------------------------------------------------------------
# Module 2 — PTC allowlist resolver
# ---------------------------------------------------------------------------


def test_resolve_ptc_disabled_returns_empty():
    from langclaw.config.schema import InterpreterConfig
    from langclaw.interpreter import resolve_ptc_allowlist

    out = resolve_ptc_allowlist(
        [_tool("web_search")],
        interpreter_config=InterpreterConfig(enabled=False),
    )
    assert out == []


def test_resolve_ptc_defaults_to_readonly():
    """Only read-only built-ins are exposed by default; mutating ones are not."""
    from langclaw.config.schema import InterpreterConfig
    from langclaw.interpreter import resolve_ptc_allowlist

    tools = [_tool("web_search"), _tool("web_fetch"), _tool("delete_file")]
    out = resolve_ptc_allowlist(tools, interpreter_config=InterpreterConfig(enabled=True))
    assert set(out) == {"web_search", "web_fetch"}
    assert "delete_file" not in out


def test_resolve_ptc_operator_opt_in_adds_mutating_tool():
    from langclaw.config.schema import InterpreterConfig
    from langclaw.interpreter import resolve_ptc_allowlist

    tools = [_tool("web_search"), _tool("delete_file")]
    out = resolve_ptc_allowlist(
        tools,
        interpreter_config=InterpreterConfig(enabled=True, allow_tools=["delete_file"]),
    )
    assert set(out) == {"web_search", "delete_file"}


def test_resolve_ptc_wildcard_opt_in_exposes_everything():
    from langclaw.config.schema import InterpreterConfig
    from langclaw.interpreter import resolve_ptc_allowlist

    tools = [_tool("web_search"), _tool("delete_file"), _tool("move_file")]
    out = resolve_ptc_allowlist(
        tools, interpreter_config=InterpreterConfig(enabled=True, allow_tools=["*"])
    )
    assert set(out) == {"web_search", "delete_file", "move_file"}


def test_resolve_ptc_narrows_to_role():
    from langclaw.config.schema import (
        InterpreterConfig,
        PermissionsConfig,
        RoleConfig,
    )
    from langclaw.interpreter import resolve_ptc_allowlist

    tools = [_tool("web_search"), _tool("web_fetch")]
    perms = PermissionsConfig(enabled=True, roles={"viewer": RoleConfig(tools=["web_search"])})
    out = resolve_ptc_allowlist(
        tools,
        interpreter_config=InterpreterConfig(enabled=True),
        permissions_config=perms,
        role="viewer",
    )
    assert set(out) == {"web_search"}


def test_resolve_ptc_consistency_with_permission_helper():
    """Resolver surface must be a subset of the role's allowed tools."""
    from langclaw.config.schema import (
        InterpreterConfig,
        PermissionsConfig,
        RoleConfig,
    )
    from langclaw.interpreter import resolve_ptc_allowlist
    from langclaw.middleware.permissions import allowed_tool_names

    tools = [_tool("web_search"), _tool("web_fetch"), _tool("delete_file")]
    names = [t.name for t in tools]
    perms = PermissionsConfig(
        enabled=True, roles={"viewer": RoleConfig(tools=["web_search", "web_fetch"])}
    )
    icfg = InterpreterConfig(enabled=True, allow_tools=["*"])
    resolved = set(
        resolve_ptc_allowlist(
            tools, interpreter_config=icfg, permissions_config=perms, role="viewer"
        )
    )
    role_allowed = allowed_tool_names(perms, "viewer", names)
    assert resolved <= role_allowed


def test_resolve_ptc_detects_camelcase_collision():
    """Two names that camelCase to the same identifier must be flagged."""
    from langclaw.config.schema import InterpreterConfig
    from langclaw.interpreter import resolve_ptc_allowlist

    # "web_search" and "web__search" / "webSearch" collide → webSearch
    tools = [_tool("web_search"), _tool("webSearch")]
    with pytest.raises(ValueError, match="(?i)collision|collide|identifier"):
        resolve_ptc_allowlist(
            tools,
            interpreter_config=InterpreterConfig(
                enabled=True, allow_tools=["web_search", "webSearch"]
            ),
        )


def test_resolve_ptc_includes_runtime_injected_names():
    """Runtime-injected tools (e.g. ``task``) pass even if not build-time tools."""
    from langclaw.config.schema import InterpreterConfig
    from langclaw.interpreter import resolve_ptc_allowlist

    out = resolve_ptc_allowlist(
        [_tool("web_search")],
        interpreter_config=InterpreterConfig(enabled=True),
        runtime_tool_names={"task"},
    )
    assert "task" in out


# ---------------------------------------------------------------------------
# Module 1 — Interpreter middleware factory
# ---------------------------------------------------------------------------


def test_factory_returns_none_when_disabled():
    from langclaw.config.schema import LangclawConfig
    from langclaw.interpreter import build_interpreter_middleware

    cfg = LangclawConfig(interpreter={"enabled": False})  # disabled, independent of .env
    assert build_interpreter_middleware(cfg, [_tool("web_search")]) is None


def test_factory_returns_middleware_when_enabled():
    pytest.importorskip("langchain_quickjs")
    from langchain_quickjs import CodeInterpreterMiddleware

    from langclaw.config.schema import InterpreterConfig, LangclawConfig
    from langclaw.interpreter import build_interpreter_middleware

    cfg = LangclawConfig()
    cfg.interpreter = InterpreterConfig(
        enabled=True, timeout=9.0, max_ptc_calls=7, max_result_chars=123
    )
    mw = build_interpreter_middleware(cfg, [_tool("web_search"), _tool("delete_file")])
    assert isinstance(mw, CodeInterpreterMiddleware)
    assert mw._timeout == 9.0
    assert mw._max_ptc_calls == 7
    assert mw._max_result_chars == 123
    # Read-only default → web_search exposed, mutating delete_file is not.
    assert "web_search" in mw._ptc
    assert "delete_file" not in mw._ptc


def test_factory_raises_clear_hint_when_extra_missing(monkeypatch):
    import builtins

    from langclaw.config.schema import InterpreterConfig, LangclawConfig
    from langclaw.interpreter import build_interpreter_middleware

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "langchain_quickjs" or name.startswith("langchain_quickjs."):
            raise ImportError("No module named 'langchain_quickjs'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    cfg = LangclawConfig()
    cfg.interpreter = InterpreterConfig(enabled=True)
    with pytest.raises(ImportError, match="(?i)interpreter"):
        build_interpreter_middleware(cfg, [_tool("web_search")])


# ---------------------------------------------------------------------------
# Wiring — agent builder integration
# ---------------------------------------------------------------------------


def _capture_deep_agent(monkeypatch):
    """Patch ``deepagents.create_deep_agent`` and capture its kwargs."""
    import deepagents

    captured: dict = {}

    def fake_create_deep_agent(**kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(deepagents, "create_deep_agent", fake_create_deep_agent)
    return captured


def _mw_names(middleware: list) -> list[str]:
    return [type(m).__name__ for m in middleware]


def test_builder_omits_interpreter_when_disabled(monkeypatch):
    pytest.importorskip("langchain_quickjs")
    from langclaw.agents.builder import create_claw_agent
    from langclaw.config.schema import LangclawConfig

    captured = _capture_deep_agent(monkeypatch)
    create_claw_agent(LangclawConfig(interpreter={"enabled": False}), model=object())
    assert "CodeInterpreterMiddleware" not in _mw_names(captured["middleware"])


def test_builder_appends_interpreter_after_permissions(monkeypatch):
    pytest.importorskip("langchain_quickjs")
    from langclaw.agents.builder import create_claw_agent
    from langclaw.config.schema import InterpreterConfig, LangclawConfig

    cfg = LangclawConfig()
    cfg.interpreter = InterpreterConfig(enabled=True)
    cfg.permissions.enabled = True

    captured = _capture_deep_agent(monkeypatch)
    create_claw_agent(cfg, model=object())

    names = _mw_names(captured["middleware"])
    assert "CodeInterpreterMiddleware" in names
    # PTC surface depends on the role-filtered toolset → interpreter must run
    # after the permission filter.
    assert names.index("_tool_permission_filter") < names.index("CodeInterpreterMiddleware")


def test_builder_injects_interpreter_prompt_nudge(monkeypatch):
    pytest.importorskip("langchain_quickjs")
    from langclaw.agents.builder import create_claw_agent
    from langclaw.config.schema import InterpreterConfig, LangclawConfig

    cfg = LangclawConfig()
    cfg.interpreter = InterpreterConfig(enabled=True)

    captured = _capture_deep_agent(monkeypatch)
    create_claw_agent(cfg, model=object())
    assert "eval" in captured["system_prompt"].lower()


def test_ptc_camel_names_are_camelcased_and_drop_snake_and_mutating():
    from langclaw.config.schema import LangclawConfig
    from langclaw.interpreter import ptc_camel_names

    cfg = LangclawConfig()
    cfg.interpreter.enabled = True
    tools = [type("T", (), {"name": n})() for n in ("read_file", "web_search", "delete_file")]
    names = ptc_camel_names(cfg, tools)
    assert "readFile" in names
    assert "webSearch" in names
    assert "read_file" not in names  # snake_case never leaks
    assert "deleteFile" not in names  # mutating tool excluded from read-only default


def test_interpreter_system_prompt_teaches_camelcase_and_lists_real_tools():
    from langclaw.config.schema import LangclawConfig
    from langclaw.interpreter import interpreter_system_prompt

    cfg = LangclawConfig()
    cfg.interpreter.enabled = True
    tools = [type("T", (), {"name": n})() for n in ("read_file", "web_search", "delete_file")]
    prompt = interpreter_system_prompt(cfg, tools)
    assert "eval" in prompt.lower()
    assert "camelCase" in prompt  # explicit rule
    assert "tools.readFile" in prompt  # real resolved name, camelCased
    assert "tools.webSearch" in prompt
    assert "tools.read_file" not in prompt  # never present the snake_case form
    assert "tools.deleteFile" not in prompt  # excluded tool not advertised


def test_interpreter_system_prompt_warns_about_persistence_and_read_shape():
    from langclaw.config.schema import LangclawConfig
    from langclaw.interpreter import interpreter_system_prompt

    cfg = LangclawConfig()
    cfg.interpreter.enabled = True
    prompt = interpreter_system_prompt(cfg, [type("T", (), {"name": "read_file"})()])
    # state does not persist across separate eval calls (fresh context)
    assert "persist" in prompt.lower()
    # fs read tools return a line-numbered string, not an object
    assert "line-numbered" in prompt.lower()
    # general "probe the shape" advice
    assert "console.log" in prompt
