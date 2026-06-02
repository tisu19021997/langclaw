# Layered org/team/employee prompt assembly (Business Workspaces, read path)

*2026-06-02T20:20:13Z*

This PR lands the first slice of **Business Workspaces** (see `docs/BUSINESS_WORKSPACES.md`): a two-phase *merge-then-validate* that composes a request's effective system prompt from an OverlayFS-style `org ▸ team ▸ employee` stack, replacing the lone flat `AGENTS.md` read in `builder.py`. It is **opt-in and off by default** — existing single-workspace deployments are byte-for-byte unchanged. Each Python block below runs in the project venv via `bash`.

**The new config surface.** Layering is gated by `AgentConfig.workspace_layers` (a `WorkspaceLayerConfig`), and the agent root gains layer-path properties. Defaults keep it inert:

```bash
.venv/bin/python - <<'PY'
from langclaw.config.schema import AgentConfig

cfg = AgentConfig(root_dir="/srv/marketing")
wl = cfg.workspace_layers
print("enabled (default)      :", wl.enabled)
print("sealed_files           :", wl.sealed_files)
print("employee_memory_top_k  :", wl.employee_memory_top_k)
print()
print("org_dir         :", cfg.org_dir.relative_to(cfg.root_dir))
print("team_dir(growth):", cfg.team_dir("growth").relative_to(cfg.root_dir))
print("user_dir(u123)  :", cfg.user_dir("u123").relative_to(cfg.root_dir))
print("proposals_dir   :", cfg.proposals_dir.relative_to(cfg.root_dir))
PY
```

```output
enabled (default)      : False
sealed_files           : ['POLICY.md', 'policy.yaml']
employee_memory_top_k  : 8

org_dir         : workspace/org
team_dir(growth): workspace/teams/growth
user_dir(u123)  : workspace/users/u123
proposals_dir   : workspace/org/_proposals
```

**Backward compatibility first.** With layering disabled (the default), `assemble_system_prompt` returns the flat prompt verbatim — even when a full `org/` tree is present on disk:

```bash
.venv/bin/python - <<'PY'
import tempfile, pathlib
from langclaw.agents.workspace_layers import assemble_system_prompt
from langclaw.config.schema import WorkspaceLayerConfig

with tempfile.TemporaryDirectory() as d:
    org = pathlib.Path(d) / "org"; org.mkdir()
    (org / "POLICY.md").write_text("Sealed: never deploy on Fridays.")
    (org / "AGENTS.md").write_text("Org playbook.")
    out = assemble_system_prompt(
        layers=WorkspaceLayerConfig(enabled=False),
        org_dir=org,
        legacy_prompt="FLAT AGENTS.md PROMPT (today)",
    )
    print(repr(out))
PY
```

```output
'FLAT AGENTS.md PROMPT (today)'
```

**Turn layering on.** The same call now assembles the `org/` tree into authority-tagged blocks — `sealed:` (binding) first, then `authoritative:` (org persona + playbook). The flat fallback is shadowed:

```bash
.venv/bin/python - <<'PY'
import tempfile, pathlib
from langclaw.agents.workspace_layers import assemble_system_prompt
from langclaw.config.schema import WorkspaceLayerConfig

with tempfile.TemporaryDirectory() as d:
    org = pathlib.Path(d) / "org"; org.mkdir()
    (org / "POLICY.md").write_text("Never disclose another customer's data.")
    (org / "SOUL.md").write_text("You are Aurora - warm, concise, on-brand.")
    (org / "AGENTS.md").write_text("Playbook: greet, qualify, hand off to sales.")
    out = assemble_system_prompt(
        layers=WorkspaceLayerConfig(enabled=True),
        org_dir=org,
        legacy_prompt="FLAT AGENTS.md PROMPT (today)",
    )
    print(out)
PY
```

```output
[SEALED — BINDING, never override]
Never disclose another customer's data.

[AUTHORITATIVE — org]
You are Aurora - warm, concise, on-brand.

[AUTHORITATIVE — org]
Playbook: greet, qualify, hand off to sales.
```

**Employee overlay + masks.** Add a team `PLAYBOOK.md` and a personal `SOUL.md`: they append as `default:` blocks *after* the org, so they refine — never outrank — org authority. A user's `masks.yaml` can suppress a `default:` block (here the personal SOUL), but is powerless against org `authoritative:`/`sealed:` blocks. (The function supports this today; the builder feeds `org/`, with per-user wiring landing on the per-(agent,user) seam.)

```bash
.venv/bin/python - <<'PY'
import tempfile, pathlib
from langclaw.agents.workspace_layers import assemble_system_prompt
from langclaw.config.schema import WorkspaceLayerConfig

with tempfile.TemporaryDirectory() as d:
    root = pathlib.Path(d)
    org = root / "org"; org.mkdir()
    (org / "POLICY.md").write_text("Never disclose another customer's data.")
    (org / "SOUL.md").write_text("You are Aurora - warm, concise, on-brand.")
    (org / "AGENTS.md").write_text("Playbook: greet, qualify, hand off to sales.")
    team = root / "teams" / "growth"; team.mkdir(parents=True)
    (team / "PLAYBOOK.md").write_text("Growth refinement: prefer short hooks.")
    user = root / "users" / "u123"; user.mkdir(parents=True)
    (user / "SOUL.md").write_text("Personal: I sign off with a wave emoji.")
    (user / "masks.yaml").write_text("- SOUL.md\n")
    out = assemble_system_prompt(
        layers=WorkspaceLayerConfig(enabled=True),
        org_dir=org, team_dir=team, user_dir=user,
    )
    print(out)
    print("--- mask result ---")
    print("personal SOUL present :", "wave emoji" in out)
    print("org SOUL (same name)  :", "Aurora" in out)
PY
```

```output
[SEALED — BINDING, never override]
Never disclose another customer's data.

[AUTHORITATIVE — org]
You are Aurora - warm, concise, on-brand.

[AUTHORITATIVE — org]
Playbook: greet, qualify, hand off to sales.

[TEAM DEFAULT]
Growth refinement: prefer short hooks.
--- mask result ---
personal SOUL present : False
org SOUL (same name)  : True
```

**Phase 2 — validate re-asserts sealed.** Machine-checkable `sealed:` rules live in `policy.yaml`. The validate pass guarantees each reaches the model: any rule whose text isn't already in the merged prompt is appended under a re-assert banner — enforced *in code*, so a mask or deletion can never strip a binding rule:

```bash
.venv/bin/python - <<'PY'
import tempfile, pathlib
from langclaw.agents.workspace_layers import assemble_system_prompt
from langclaw.config.schema import WorkspaceLayerConfig

with tempfile.TemporaryDirectory() as d:
    org = pathlib.Path(d) / "org"; org.mkdir()
    (org / "SOUL.md").write_text("You are Aurora.")
    (org / "POLICY.md").write_text("Be honest about limitations.")
    (org / "policy.yaml").write_text(
        'sealed:\n'
        '  - id: no-pii-export\n'
        '    rule: "Never export customer PII to third parties."\n'
        'approvers:\n'
        '  outbound: marketing-lead\n'
    )
    print(assemble_system_prompt(layers=WorkspaceLayerConfig(enabled=True), org_dir=org))
PY
```

```output
[SEALED — BINDING, never override]
Be honest about limitations.

[AUTHORITATIVE — org]
You are Aurora.

[SEALED — RE-ASSERTED — machine-checked from policy.yaml]
- Never export customer PII to third parties.
```

**It's wired end-to-end.** `create_claw_agent` routes the assembled org prompt into the real deepagents build when `workspace_layers.enabled`. Here we stub `create_deep_agent` to capture the `system_prompt` it receives:

```bash
.venv/bin/python - <<'PY'
import tempfile, pathlib
import deepagents
from langclaw.config.schema import LangclawConfig

captured = {}
deepagents.create_deep_agent = lambda **kw: captured.update(kw) or "AGENT"

class FakeModel:
    def bind_tools(self, *a, **k): return self

with tempfile.TemporaryDirectory() as d:
    ws = pathlib.Path(d) / "workspace"; (ws / "org").mkdir(parents=True)
    (ws / "AGENTS.md").write_text("FLAT LEGACY PLAYBOOK")
    (ws / "org" / "POLICY.md").write_text("Never deploy on Fridays.")
    (ws / "org" / "AGENTS.md").write_text("Org playbook: greet, qualify.")
    from langclaw.agents.builder import create_claw_agent
    cfg = LangclawConfig(
        agents={"root_dir": d, "workspace_layers": {"enabled": True}},
        interpreter={"enabled": False},
        debug=False,
    )
    create_claw_agent(cfg, model=FakeModel())
    p = captured["system_prompt"]
    print("SEALED banner reached the agent :", "[SEALED - BINDING" in p or "[SEALED — BINDING, never override]" in p)
    print("org playbook in prompt          :", "greet, qualify" in p)
    print("flat AGENTS.md shadowed         :", "FLAT LEGACY PLAYBOOK" not in p)
PY
```

```output
SEALED banner reached the agent : True
org playbook in prompt          : True
flat AGENTS.md shadowed         : True
```

**The test suite.** 10 tests cover backward-compat (disabled + un-migrated fallback), precedence ordering, mask semantics (default-only), the sealed validate pass (re-assert + no-duplicate), the top-k bound, and the real builder wiring (enabled vs disabled). Count shown deterministically:

```bash
.venv/bin/python -m pytest tests/test_workspace_layers.py -q 2>/dev/null | grep -oE "[0-9]+ passed"
```

```output
10 passed
```

**Proven:** layering is opt-in and backward-compatible; when enabled, the org layer composes into an authority-tagged prompt (`sealed:` ▸ `authoritative:` ▸ `default:`) that flows into the real agent build; masks suppress `default:` blocks only; and `policy.yaml` sealed rules are re-asserted in code. Team/employee overlays are implemented and tested, awaiting the per-(agent,user) request seam to wire per-employee prompts. See `docs/BUSINESS_WORKSPACES.md` for the full design and phased rollout.
