# RBAC

Langclaw's RBAC is three-axis, and the axes differ in their default. **Tools** are pass-through for *unknown* roles — an unlisted role name sees the full toolset, while a *defined* role sees only the tools it lists (`["*"]` grants all, `[]` grants none). **Subagents** and **workflows** are default-deny on every role: each must explicitly list the ones it can reach.

## Define roles

```python
app.role("admin",    tools=["*"],            subagents=["*"],            workflows=["*"])
app.role("analyst",  tools=["*"],            subagents=["researcher"],   workflows=["digest"])
app.role("free",     tools=["web_search"],   subagents=[],               workflows=[])
```

## Three axes

| Axis | `RoleConfig` field | Default |
|---|---|---|
| Tools | `tools` | unknown role → all tools; defined role → only what it lists (`["*"]`=all, `[]`=none) |
| Subagents | `subagents` | **deny** — must be explicitly allowed |
| Workflows | `workflows` | **deny** — must be explicitly allowed |

## Enable RBAC

RBAC is **off by default** — every user sees every tool. Turn it on:

```bash
LANGCLAW__PERMISSIONS__ENABLED=true
LANGCLAW__PERMISSIONS__DEFAULT_ROLE=viewer   # role for unlisted users (default: viewer)
```

## Assign roles to users

Roles are resolved per-request from the inbound message's `user_id`. You map
users to roles **per channel** via that channel's `user_roles` setting. The env
format is a comma list of `id:role` (IDs *or* `@usernames`):

```bash
LANGCLAW__CHANNELS__TELEGRAM__USER_ROLES=123456:admin,@alice:analyst,789:free
```

Equivalently in code/config: `channels.telegram.user_roles = {"123456": "admin", "@alice": "analyst"}`.

**Resolution order** (`gateway/manager.py:_resolve_user_role`):

1. A pre-resolved `metadata["user_role"]` on the message (e.g. stamped by a cron job at schedule time).
2. The channel's `user_roles` mapping — by `user_id`, then by `username`.
3. `permissions.default_role` (default `"viewer"`) for anyone unlisted.

!!! warning "Footgun: define your `default_role`, or tools stay open"
    An unlisted user is assigned `default_role` (`"viewer"`). If you never register
    that role with `app.role(...)`, it is an **unknown** role — and on the **tools**
    axis unknown roles are **pass-through (all tools)**. So enabling RBAC *without*
    defining the default role does **not** restrict tools (subagents and workflows
    *are* still denied). To actually limit tools, define it explicitly, e.g.
    `app.role("viewer", tools=["web_search"])` — or `tools=[]` for none.

There is currently no programmatic `role_resolver` hook — role assignment is
declarative via `user_roles` + `default_role`.

## How enforcement works

The unified capability filter runs as middleware **before the LLM sees the toolset**. Tools the role can't access are stripped from the model's tool list entirely — the agent can't call what it can't see.

Subagents and workflows have an additional gate: even if a subagent type appears in the toolset, the `task` tool checks `RoleConfig.subagents` at call time.

## Startup validation

`validate_capability_registry` runs at startup and raises `ValueError` if any axis is misconfigured — missing `RoleConfig` field, unreserved name prefix, or no enforcement shape. Misconfiguration fails loudly rather than silently passing through.

See [`examples/rbac_showboat.py`](https://github.com/tisu19021997/langclaw/blob/main/examples/rbac_showboat.py) for a runnable tour of all three axes.
