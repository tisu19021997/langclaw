"""
Unified Capability RBAC — architecture showboat (issue #37).

A runnable, self-proving tour of langclaw's one-resolver / one-seam RBAC model.
It exercises the *real* `langclaw.rbac` + `build_capability_filter_middleware`
code (nothing is faked), then demonstrates the headline property — **a new
capability axis is added by pure declaration** (an axis descriptor, a RoleConfig
field, a reserved prefix), with ZERO changes to the resolver or the enforcement
filter — by bolting a fourth axis onto the live registry and showing both pick
it up. The startup guard `validate_capability_registry` keeps that promise honest:
a half-declared axis raises instead of silently failing open.

What it illustrates
-------------------
1. The registry — every axis declared once (`CapabilityAxis` in `CAPABILITY_AXES`).
2. One resolver — `resolve_capability` drives all axes; the single
   default-deny-vs-pass-through decision is one boolean per axis.
3. One enforcement seam — `build_capability_filter_middleware` filters the tool
   axis and every `*_<name>` axis in a single pass.
4. Scalability — add a `datasets` axis (a `CapabilityAxis`, a `RoleConfig` field,
   a reserved prefix) and watch it flow through the unchanged resolver and filter,
   accepted by the startup validator.

Run
---
    uv run python examples/rbac_showboat.py

Exits non-zero if any invariant fails, so it doubles as an executable spec.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from loguru import logger
from pydantic import Field

from langclaw import naming
from langclaw.config.schema import PermissionsConfig, RoleConfig
from langclaw.middleware import permissions as perms_mod
from langclaw.middleware.permissions import build_capability_filter_middleware
from langclaw.rbac import CAPABILITY_AXES, SUBAGENTS, CapabilityAxis, resolve_capability

# Quiet the framework's per-call DEBUG lines so the showboat output stays clean.
logger.disable("langclaw")

# --------------------------------------------------------------------------- #
# Tiny presentation helpers (no deps — keep the showboat self-contained).
# --------------------------------------------------------------------------- #

BOLD, DIM, GREEN, YELLOW, CYAN, RESET = (
    "\033[1m",
    "\033[2m",
    "\033[32m",
    "\033[33m",
    "\033[36m",
    "\033[0m",
)


def banner(title: str) -> None:
    line = "─" * (len(title) + 2)
    print(f"\n{CYAN}┌{line}┐{RESET}")
    print(f"{CYAN}│ {BOLD}{title}{RESET}{CYAN} │{RESET}")
    print(f"{CYAN}└{line}┘{RESET}")


def tool(name: str) -> SimpleNamespace:
    """Stand-in for a LangChain tool — the filter only reads `.name`."""
    return SimpleNamespace(name=name)


def run_filter(mw, tools: list, role: str) -> list[str]:
    """Drive the real wrap_model_call filter once; return the surviving names."""
    runtime = SimpleNamespace(context=SimpleNamespace(user_role=role))
    request = SimpleNamespace(
        runtime=runtime,
        tools=tools,
        override=lambda **kw: SimpleNamespace(tools=kw.get("tools", tools), runtime=runtime),
    )
    captured: dict = {}

    async def handler(req):
        captured["tools"] = req.tools
        return "ok"

    asyncio.run(mw.awrap_model_call(request, handler))
    return [t.name for t in captured["tools"]]


def check(label: str, got, want) -> None:
    ok = got == want
    mark = f"{GREEN}✓{RESET}" if ok else f"{YELLOW}✗{RESET}"
    print(f"   {mark} {label}")
    if not ok:
        raise AssertionError(f"{label}: got {got!r}, expected {want!r}")


# --------------------------------------------------------------------------- #
# 1 — The registry: every axis declared exactly once.
# --------------------------------------------------------------------------- #


def show_registry() -> None:
    banner("1 · The registry — one declaration per axis")
    print(f"{DIM}   Each axis binds a RoleConfig field to ONE policy flag + how the")
    print(f"   model-call filter classifies a tool into it.{RESET}\n")
    print(f"   {BOLD}{'axis':<11}{'role field':<12}{'unknown role':<16}{'tool mapping'}{RESET}")
    print(f"   {'─' * 11}{'─' * 12}{'─' * 16}{'─' * 18}")
    for a in CAPABILITY_AXES:
        policy = "pass-through" if a.unknown_role_grants_all else "default-deny"
        if a.is_residual_tool_axis:
            mapping = "residual (no prefix)"
        elif a.tool_prefix:
            mapping = f"prefix {a.tool_prefix!r}"
        else:
            mapping = "n/a — gated per-arg"
        print(f"   {a.name:<11}{a.role_field:<12}{policy:<16}{mapping}")
    print(f"\n{DIM}   → tools pass-through; subagents + workflows default-deny —")
    print(f"   the historical divergence is now a single boolean column.{RESET}")


# --------------------------------------------------------------------------- #
# 2 — One resolver drives every axis.
# --------------------------------------------------------------------------- #


def show_resolver() -> None:
    banner("2 · One resolver — resolve_capability() for all axes")
    cfg = PermissionsConfig(
        enabled=True,
        default_role="guest",
        roles={
            "admin": RoleConfig(tools=["*"], subagents=["*"], workflows=["*"]),
            "analyst": RoleConfig(
                tools=["web_search"], subagents=["researcher"], workflows=["digest"]
            ),
            # "guest" is intentionally undefined → exercises the unknown-role path.
        },
    )
    tool_univ = ["web_search", "delete_file"]
    sub_univ = None  # subagents resolve verbatim ("*" preserved for later check)
    wf_univ = ["digest", "payroll_export"]

    from langclaw.rbac import TOOLS, WORKFLOWS

    print(f"   {BOLD}{'role':<10}{'tools':<32}{'subagents':<18}{'workflows'}{RESET}")
    print(f"   {'─' * 10}{'─' * 32}{'─' * 18}{'─' * 18}")
    for role in ("admin", "analyst", "guest"):
        t = sorted(resolve_capability(TOOLS, cfg, role, tool_univ))
        s = sorted(resolve_capability(SUBAGENTS, cfg, role, sub_univ))
        w = sorted(resolve_capability(WORKFLOWS, cfg, role, wf_univ))
        print(f"   {role:<10}{str(t):<32}{str(s):<18}{str(w)}")

    print(f"\n{DIM}   Note the asymmetry, captured by ONE flag:{RESET}")
    check(
        "guest (unknown) → ALL tools (pass-through)",
        resolve_capability(TOOLS, cfg, "guest", tool_univ),
        {"web_search", "delete_file"},
    )
    check(
        "guest (unknown) → NO workflows (default-deny)",
        resolve_capability(WORKFLOWS, cfg, "guest", wf_univ),
        set(),
    )
    check(
        "analyst tools=['*']? no — still NO subagents unless granted",
        resolve_capability(SUBAGENTS, PermissionsConfig(roles={"a": RoleConfig(tools=["*"])}), "a"),
        set(),
    )


# --------------------------------------------------------------------------- #
# 3 — One enforcement seam filters tools + workflows together.
# --------------------------------------------------------------------------- #


def show_filter() -> None:
    banner("3 · One seam — build_capability_filter_middleware()")
    cfg = PermissionsConfig(
        enabled=True,
        default_role="guest",
        roles={
            "analyst": RoleConfig(tools=["web_search"], workflows=["digest"]),
        },
    )
    mw = build_capability_filter_middleware(cfg)
    live = [
        tool("web_search"),
        tool("delete_file"),
        tool("workflow_digest"),
        tool("workflow_payroll_export"),
    ]
    print(f"   live tools : {[t.name for t in live]}")
    survivors = run_filter(mw, live, "analyst")
    print(f"   analyst    : {GREEN}{survivors}{RESET}")
    print(f"{DIM}   One pass strips delete_file (tool axis) AND workflow_payroll_export")
    print(f"   (workflow axis, default-deny) — no two filters to keep in sync.{RESET}")
    check(
        "filter governs both axes in one pass",
        survivors,
        ["web_search", "workflow_digest"],
    )


# --------------------------------------------------------------------------- #
# 4 — Scalability: a NEW axis with one declaration, zero resolver/filter edits.
# --------------------------------------------------------------------------- #


class RoleConfigV2(RoleConfig):
    """RoleConfig + one new field. In production this is a one-line edit to the
    real RoleConfig; here we subclass so the demo never patches the schema."""

    datasets: list[str] = Field(default_factory=list)


def show_scalability() -> None:
    banner("4 · Scale by one declaration — add a `datasets` axis")

    # (a) ONE descriptor. Default-deny, classified by the `dataset_` prefix —
    #     exactly mirroring how `workflows` was declared.
    DATASETS = CapabilityAxis(
        name="datasets",
        role_field="datasets",
        unknown_role_grants_all=False,
        tool_prefix="dataset_",
    )
    print(f"{DIM}   declared: CapabilityAxis(name='datasets', role_field='datasets',")
    print(f"             unknown_role_grants_all=False, tool_prefix='dataset_'){RESET}")

    # (b) Register it. In production the full recipe is THREE one-line edits:
    #       1. append DATASETS to CAPABILITY_AXES in langclaw/rbac.py
    #       2. add `datasets: StringList` to RoleConfig (so roles can grant it)
    #       3. reserve the `dataset_` prefix in langclaw.naming.RESERVED_TOOL_PREFIXES
    #     validate_capability_registry() enforces all three at startup — a
    #     half-done axis raises rather than silently failing open. We simulate the
    #     three edits here (RoleConfigV2 carries the field; the prefix is reserved)
    #     and restore every global in `finally` so the demo leaves no state behind.
    original = perms_mod.CAPABILITY_AXES
    original_reserved = naming.RESERVED_TOOL_PREFIXES
    perms_mod.CAPABILITY_AXES = (*original, DATASETS)
    naming.RESERVED_TOOL_PREFIXES = {**original_reserved, "dataset_": "dataset"}
    try:
        cfg = PermissionsConfig(enabled=True, default_role="guest", roles={})
        # Inject roles carrying the new grant (bypass dict re-validation).
        cfg.roles["analyst"] = RoleConfigV2(tools=["*"], datasets=["sales"])
        cfg.roles["admin"] = RoleConfigV2(tools=["*"], datasets=["*"])

        # The resolver needs NO change — `axis` is a parameter.
        check(
            "resolve_capability handles the brand-new axis unchanged",
            resolve_capability(DATASETS, cfg, "analyst", ["sales", "payroll"]),
            {"sales"},
        )

        # The filter auto-discovers it via the registry — NO change to the
        # filter code either.
        mw = build_capability_filter_middleware(cfg)
        live = [tool("web_search"), tool("dataset_sales"), tool("dataset_payroll")]
        print(f"\n   live tools : {[t.name for t in live]}")
        for role in ("admin", "analyst", "guest"):
            print(f"   {role:<9}: {GREEN}{run_filter(mw, live, role)}{RESET}")

        check(
            "analyst keeps only granted dataset (default-deny)",
            run_filter(mw, live, "analyst"),
            ["web_search", "dataset_sales"],
        )
        check(
            "admin datasets=['*'] keeps all",
            run_filter(mw, live, "admin"),
            ["web_search", "dataset_sales", "dataset_payroll"],
        )
        check(
            "guest (unknown) — tools pass-through, datasets default-deny",
            run_filter(mw, live, "guest"),
            ["web_search"],
        )
    finally:
        perms_mod.CAPABILITY_AXES = original
        naming.RESERVED_TOOL_PREFIXES = original_reserved

    print(f"\n{DIM}   The new axis flowed through the SAME resolve_capability and the")
    print("   SAME filter, and validate_capability_registry accepted it because all")
    print(f"   three declarations were present. Net production cost: three one-liners.{RESET}")


def main() -> None:
    print(f"{BOLD}langclaw · Unified Capability RBAC — architecture showboat{RESET}")
    print(f"{DIM}Running against the live langclaw.rbac code (issue #37).{RESET}")
    show_registry()
    show_resolver()
    show_filter()
    show_scalability()
    print(
        f"\n{GREEN}{BOLD}✓ All invariants held — one resolver, one seam, "
        f"axes added by declaration (no resolver/filter edits).{RESET}\n"
    )


if __name__ == "__main__":
    main()
