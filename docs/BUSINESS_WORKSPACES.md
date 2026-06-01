# Langclaw Business Workspaces: Org / Team / Employee Layering

**Status:** Design (final) · **Scope:** one org per deployment · **Spine:** POLICY-PROPOSAL, with grafts from OVERLAY-FS and TENANCY-FIRST

## 1. Problem & Goals

Today every langclaw workspace is single-tenant. The default agent reads one `workspace/AGENTS.md`; each named agent (`app.agent("researcher", …)`) gets an isolated `workspace/<agent_name>/` subtree. There is exactly one author and one reader of each file — fine for a solo bot, wrong for a business.

A business deploys **one agent to many employees**. Take the canonical case: a single `marketing` agent serves every marketer. There must be **one global marketing playbook** that all of them share and that defines how the bot operates — and **per-marketer personal tactics** that each marketer owns. Two layers, two authorities:

- **ORG layer** — shared, authoritative. Operating rules, brand persona, business logic, the global playbook, hard compliance constraints. *Not* directly editable by an individual employee.
- **EMPLOYEE layer** — personal memory, preferences, and ways of working, fully read/write by that one person and invisible to everyone else.

Goals:
1. **Immutable org from below.** An employee's agent can read the org layer but cannot mutate it through normal tool use.
2. **Promotion path.** A good personal tactic can be *proposed* up into the org playbook.
3. **Governance.** Only authorized roles accept/reject — or an LLM auto-audits low-risk diffs — with a durable audit trail. An accepted change reaches **everyone** with no fan-out.
4. **Privacy.** One employee's memory never enters another's prompt.
5. **Zero-disruption.** Existing single-workspace deployments keep working untouched.

We reject the naive "AGENTS.md = org, SOUL.md = personal" split: **both persona and playbook are org-level**, and authority is expressed as *data* (tags), not filename (§4).

## 2. Core Model & Terminology

The workspace is an **OverlayFS-style stack**: ORG is the sealed *lower* layer, TEAM an optional *middle* layer, EMPLOYEE the writable *upper* layer that shadows the ones below. A request's effective workspace is the merged view, resolved per `(channel, user_id)`. We keep the existing `workspace/<agent>/` root and nest the layers **under** it — named-agent isolation is reused verbatim, not re-rooted.

- **Layer** — org / team / employee. Each holds prompt fragments, skills, memories.
- **Authority tag** — every prompt/memory block carries `sealed:` (binding, never overridable, never maskable), `authoritative:` (org rule, wins on conflict), or `default:` (suggestion; a more-specific layer may override or mask it).
- **Tenant identity** — `(channel, user_id)`, plus an optional `team_id` from config. `org_id` is reserved (defaulted) since we scope to one org per deployment.
- **Proposal** — a structured diff from a lower-authority layer requesting a change to the org/team layer.

```
        EFFECTIVE WORKSPACE  =  merge(employee ▸ team ▸ org)  then validate(sealed)

  employee/   ── upper ── writable by owner ─┐
  team/       ── middle ─ read-only to agent  ├─ overlay walk (specific wins for default:)
  org/        ── lower ── sealed, read-only ──┘   org wins for authoritative:/sealed:
                                                  validate pass re-asserts sealed:
```

## 3. Workspace Layout

```
workspace/marketing/                      # agent root = root_dir/workspace/<agent>
├── org/                                   # ORG — authoritative, agent-read-only
│   ├── SOUL.md                            # PERSONA: brand voice, who the agent is
│   ├── AGENTS.md                          # PLAYBOOK: operating rules, business logic
│   ├── POLICY.md                          # OPERATING RULES (prose, sealed constraints)
│   ├── policy.yaml                        # machine-checkable: sealed-rule IDs + section→approver map
│   ├── skills/                            # org-blessed SKILL.md dirs
│   ├── memories/                          # org KNOWLEDGE (FAQs, catalog) — read-all
│   └── _proposals/                        # the governance store
│       ├── pending/  PROP-0007.json
│       ├── accepted/ PROP-0003.json
│       ├── rejected/ PROP-0005.json
│       └── audit.log                      # append-only {actor,time,diff,outcome,rationale}
├── teams/<team_id>/                       # TEAM — optional middle tier (refinements)
│   ├── PLAYBOOK.md  memories/  _proposals/pending/…
└── users/<user_id>/                       # EMPLOYEE — fully read/write by that person
    ├── SOUL.md                            # personal tactics / preferences (MEMORY+persona)
    ├── memories/                          # personal /memories (today's writable surface)
    └── masks.yaml                         # whiteout: non-sealed org defaults this user suppresses
```

Reconciling the roles against the naive split:

| Role | File | Layer | Authority |
|---|---|---|---|
| Brand persona | `SOUL.md` | org (+ employee overlay) | org `authoritative:`, employee `default:` |
| Operating rules / playbook | `AGENTS.md` | org (+ team) | `authoritative:` |
| Sealed constraints | `POLICY.md` + `policy.yaml` | org only | `sealed:` |
| Org knowledge | `memories/` | org / team | read-all |
| Personal memory & style | `SOUL.md`, `memories/` | employee | `default:`, owner-private |
| Suppression | `masks.yaml` | employee | drops `default:` only |

So `SOUL.md` exists at **both** org and employee scope — the org one is brand identity, the personal one is individual style that *refines* it. That is the richer picture the naive framing missed.

## 4. Prompt Assembly & Precedence

All layering attaches at the single concatenation point in `builder.py` (~337), where today `base = workspace/AGENTS.md`. Replace the lone read with `assemble_system_prompt(layers, user_id)`, a **two-phase merge-then-validate** (Kubernetes mutating→validating model):

**Phase 1 — merge (mutating).** Read in precedence order, each block emitted with its authority-tag header:

```
[SEALED — BINDING, never override]   org/POLICY.md, org/policy.yaml sealed blocks
[AUTHORITATIVE — org]                org/SOUL.md, org/AGENTS.md
[TEAM DEFAULT]                       teams/<team>/PLAYBOOK.md          (if team)
[PERSONAL — refines, never relaxes]  users/<user_id>/SOUL.md + top-k memories
```

Personal lines win **only** over `default:` blocks. Lines named in the user's `masks.yaml` are dropped — *unless* `sealed:`. Employee **memories are retrieved top-k, not concatenated whole** (graft from TENANCY-FIRST), so personal memory cannot grow the prompt unboundedly.

**Phase 2 — validate (validating).** A guardrail pass re-asserts every `sealed:` rule from `policy.yaml`: any personal/team line that contradicts or strips a sealed rule is removed before the agent sees the prompt. Authority is enforced **in code, not by trusting the LLM**.

After assembly, the existing tail is unchanged: optional per-agent `system_prompt`, interpreter prompt, then `Your name is <display_name>`.

Authority stays with the org two independent ways: at **assembly** (sealed survives, masks can't touch it) and at **write time** (employees physically cannot write into `org/` — §5). The merged prompt itself instructs the model that sealed/authoritative blocks are binding and personal notes may only refine them.

## 5. Memory Model

- **Scoping key:** extend today's `(channel, user_id, context_id)` with `scope ∈ {org|team|user}` and `owner_id`. Org memory → `org/memories/` (read-all), team → `teams/<team_id>/`, personal → `users/<user_id>/memories/` (owner read/write only).
- **Conflict precedence:** most-specific-wins for `default:` preferences; **org-wins, non-overridable for `sealed:`/`authoritative:`** (AWS-SCP "explicit deny wins"). A personal tactic beats a suggested playbook default; it never beats a compliance rule.
- **Privacy boundary (hardened invariant, graft from TENANCY-FIRST):** a user's `users/<id>/` is reachable only within its own `(channel, user_id)` namespace, derived server-side from `InboundMessage` identity — never from the message body. SessionManager's namespace tuple gains `scope`/`owner_id` so personal threads **provably cannot bleed** across employees. Reads flow *down* the ladder (everyone sees org/team); nothing flows *up* except a consented promotion event.

**`fs.py` changes.** Today `_safe_resolve()` sandboxes to one root and rejects `../`, which makes org-read impossible from an employee root. Parameterize the sandbox per layer and bind **three roots** instead of one:
- employee tools (`write_file`/`edit_file`/`delete_file`) root at `users/<user_id>/` — read/write;
- a **new read-only `org_read`/`org_ls` tool** roots at `org/` (and team) — no write verbs bound;
- writes targeting `org/` are rejected at the tool layer. The only privileged org write lives inside the promotion command (§6), never in the agent toolset.

**SessionManager changes.** Add `get_team(channel, user_id)` (from config), and the `scope`/`owner_id` namespace dimension above. `context_id="agent:<name>"` is unchanged.

**The load-bearing seam.** `workspace_dir` and the deepagents `FilesystemBackend` bind at agent *build* time and the agent is cached in `_agent_map` keyed by **name only**, but `user_id` arrives only per *request* via `ChannelContextMiddleware`. We resolve this by keying `_agent_map` on `(agent_name, user_id)` and **lazily building/caching a per-employee agent with an explicit LRU cap**. No deepagents fork, no undocumented hook. We name its cost: live-agent count scales with active employees, plus cold-start on eviction.

**Documented escape hatch (graft from OVERLAY-FS).** If the LRU instance cache proves too heavy under high concurrency, fall back to a **single agent + read-only personal context injected at assembly time** — losing agent-driven personal *writes* but preserving the layering and privacy. A **day-one spike** confirms whether deepagents exposes any request-time backend selection; if it does, that unlocks the cheaper single-instance path and we take it.

> **Update — backend injection landed.** The builder no longer hardcodes `FilesystemBackend`: `create_claw_agent(backend=...)` / `Langclaw(backend=...)` now accept an explicit backend, and deepagents' `create_deep_agent(backend=...)` accepts a `Callable[[ToolRuntime], BackendProtocol]` **runtime factory** — i.e. request-time backend selection *is* supported. That partially settles the Phase-0 spike below and opens the cheaper single-instance path: one cached agent whose backend factory re-roots per request from the `ToolRuntime` context (`user_id`), instead of the per-`(agent,user)` LRU. The layered-prompt assembly (§5) and per-layer write-gating still need building; this only removes the backend-binding risk.

## 6. Promotion & Governance Flow

Proposal-as-diff (GitOps PR model), composed from existing primitives — `@app.command`, `user_roles`/`_resolve_user_role`, the `RoleConfig.subagents` allowlist, and the file-backed memory store.

1. **Propose** — `/propose <text>` (an `@app.command`, cloned from `_setup_agent_command`) emits the employee's tactic as a **unified diff** against `org/AGENTS.md` (or `POLICY.md`) into `_proposals/pending/PROP-N.json`: `{author, source_thread, target_section, diff, provenance, state:"proposed"}`. Never an in-place write. Published to the bus as a `proposal` message — proposals are messages.
2. **Route** — `policy.yaml` maps doc section → approver role (CODEOWNERS-style). A new `RoleConfig.can_approve_memory: list[str]` capability says which scopes a role may approve; team scope needs a team lead, org scope an org admin.
3. **Auto-audit (optional)** — an `app.agent("policy_auditor")` subagent (reachable only via the default-deny `RoleConfig.subagents` allowlist) reads the diff + `policy.yaml` and returns allow/reject **with a logged rationale**. Clean low-risk diffs auto-accept; anything touching a `sealed:` rule escalates to a human (OPA validating-webhook pattern).
4. **Decide** — `/review` lists pending; `/approve PROP-N` (gated on `can_approve_memory`) atomically merges the diff into `org/` via the one privileged write path, moves the artifact to `accepted/`, and appends `{actor,time,diff,outcome,rationale}` to `audit.log`. `/reject` moves to `rejected/`; personal memory untouched.
5. **Propagate** — because every employee's prompt is re-assembled from `org/` at session build (and via the existing AGENTS.md hot-reload at `manager.py`), an accepted change reaches **everyone** on their next session — no fan-out.

State machine: `Proposed → Under-Review → Accepted | Rejected`, every transition append-logged (restart-safe, filesystem-native).

```text
marketer (role=marketer)        policy_auditor          marketing-lead (can_approve_memory=[team,org])
   │  /propose "Use UGC hooks    (subagent)                     │
   │   in cold DMs"                  │                          │
   ├── writes _proposals/pending/PROP-0007.json (diff vs org/AGENTS.md §outbound)
   ├── publishes proposal msg ─────► reads diff + policy.yaml
   │                                 │  no sealed rule touched → verdict: ESCALATE(low-risk)
   │                                 └── appends rationale to audit.log
   │                                                            │  /review → /approve PROP-0007
   │                                          atomic merge diff ─┤  into org/AGENTS.md
   │                                          mv → accepted/, append audit.log
   ▼
 ALL marketers' next session assembles updated org/AGENTS.md  (propagation, no fan-out)
```

## 7. Config / Schema Changes

```python
# langclaw/config/schema.py
class WorkspaceLayerConfig(BaseModel):
    enabled: bool = False                       # opt-in; off == today's behavior
    sealed_files: list[str] = ["POLICY.md", "policy.yaml"]
    approver_map_file: str = "policy.yaml"
    employee_memory_top_k: int = 8              # bounds personal-memory prompt growth
    agent_instance_cache_size: int = 256        # LRU cap for per-(agent,user) instances

class RoleConfig(BaseModel):                    # extend existing
    # ... existing fields (subagents, etc.) ...
    can_approve_memory: list[str] = []          # scopes this role may approve: ["team","org"]

class ChannelConfigBase(BaseModel):             # extend existing per-channel base
    user_roles: dict[str, str] = {}             # existing
    user_teams: dict[str, str] = {}             # NEW: user_id -> team_id

class AgentConfig(BaseModel):                    # add layer-aware path props
    workspace_layers: WorkspaceLayerConfig = WorkspaceLayerConfig()
    @property
    def org_dir(self) -> Path: return self.workspace_dir / "org"
    def team_dir(self, team_id: str) -> Path: return self.workspace_dir / "teams" / team_id
    def user_dir(self, user_id: str) -> Path: return self.workspace_dir / "users" / user_id
    @property
    def proposals_dir(self) -> Path: return self.org_dir / "_proposals"
```

Env examples:

```bash
LANGCLAW__AGENTS__WORKSPACE_LAYERS__ENABLED=true
LANGCLAW__AGENTS__WORKSPACE_LAYERS__EMPLOYEE_MEMORY_TOP_K=8
LANGCLAW__AGENTS__WORKSPACE_LAYERS__AGENT_INSTANCE_CACHE_SIZE=256
LANGCLAW__CHANNELS__TELEGRAM__USER_TEAMS__123456=growth
LANGCLAW__CHANNELS__TELEGRAM__USER_ROLES__789012=marketing-lead
```

`builder.py`: (a) replace the single `AGENTS.md` read with `assemble_system_prompt`; (b) swap the injected backend (default `LocalShellBackend` via `make_backend`) for a `CompositeBackend` of three layer-scoped roots + the read-only `org_read` tool — passed through the existing `backend=` param, or as a per-request backend factory; (c) key `_agent_map` on `(agent_name, user_id)` with the LRU (or drop the LRU entirely if the factory path is taken). `gateway/manager.py`: register `/propose`, `/review`, `/approve`, `/reject` via the existing command closure; reuse `_resolve_user_role` for gating. Reused wholesale: named-agent subtree pattern, `@app.command`, RBAC, `_safe_resolve`, subagents, the bus.

## 8. Backward Compatibility / Migration

Today's single workspace is the **degenerate single-org, single-user** case. When `workspace_layers.enabled = false` (the default), `assemble_system_prompt` reads the legacy single `workspace/<agent>/AGENTS.md`, the config-default backend (now `LocalShellBackend`, selectable via `config.agents.backend`) is unchanged, and `_agent_map` keys on name only — **zero behavior change** for every existing deployment. Layering, per-user roots, and the proposal commands activate only when explicitly enabled.

When enabled with no `org/` present, first build treats the existing flat tree as the org layer: move (or symlink) `AGENTS.md`, `skills/`, `memories/` under `org/` and create empty `users/`. A one-time `langclaw migrate-workspace` does this idempotently. Enabling is per-agent, so a deployment can layer the `marketing` agent while leaving others flat.

## 9. Phased Rollout + Risks

**Phase 0 — de-risk (day one).** ~~Spike the deepagents backend-root question: does it expose any request-time backend selection?~~ **Answered:** yes — `create_deep_agent(backend=...)` takes a `Callable[[ToolRuntime], BackendProtocol]` factory, and langclaw now threads a `backend=` param through `create_claw_agent` / `Langclaw` (see §5 update note). This unlocks the cheaper single-instance path; the remaining spike is confirming the factory can read `user_id` from the `ToolRuntime` context to re-root per request.

**Phase 1 — layered read-only.** `WorkspaceLayerConfig`, `org/`+`users/` tree, two-phase `assemble_system_prompt`, `org_read` tool, employee-writable `users/` fs root, hardened SessionManager namespace, migration command. Ships the org/employee split and privacy without governance.

**Phase 2 — governance.** `_proposals/` store, `/propose` `/review` `/approve` `/reject`, `policy.yaml` approver map + `can_approve_memory`, audit log, hot-reload propagation.

**Phase 3 — auto-audit + teams.** `policy_auditor` subagent, `masks.yaml` whiteouts, team middle tier.

**Top 3 risks / open questions:**
1. **Build-time vs request-time binding (~~highest~~ → downgraded).** Per-employee writable memory requires either an LRU per-`(agent,user)` instance cache (memory + cold-start cost) or a request-time backend swap. **The swap is now confirmed supported** (deepagents' `backend=` runtime factory + langclaw's `backend=` param — see §5/Phase-0), so the single-instance path is viable; the residual risk is only wiring `user_id` from `ToolRuntime` into the factory. Mitigation: the confirmed factory path + the documented read-only-context escape hatch.
2. **Agent-instance explosion.** Many concurrent employees × the LRU cap means cold-starts under churn and a hard concurrency ceiling. Open question: is the read-only-injection fallback acceptable as the default at scale, reserving live writes for smaller teams?
3. **Auto-audit trust boundary.** An LLM auditor auto-accepting org-playbook changes is a real attack/error surface. Mitigation: auto-accept is opt-in and structurally forbidden from touching `sealed:` rules; conservatively, Phase 2 ships human-only approval and the auditor lands in Phase 3 behind a flag.
