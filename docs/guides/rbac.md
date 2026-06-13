# RBAC

Langclaw's RBAC is three-axis and default-deny on two of them: tools are default-pass-through (a role with `tools=["*"]` sees everything), while subagents and workflows are default-deny (a role must explicitly list the ones it can reach).

## Define roles

```python
app.role("admin",    tools=["*"],            subagents=["*"],            workflows=["*"])
app.role("analyst",  tools=["*"],            subagents=["researcher"],   workflows=["digest"])
app.role("free",     tools=["web_search"],   subagents=[],               workflows=[])
```

## Three axes

| Axis | `RoleConfig` field | Default |
|---|---|---|
| Tools | `tools` | pass-through (`["*"]` = all tools) |
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

!!! note "What `default_role` actually grants"
    The default role is **not** "no restrictions". An unlisted user gets whatever
    that role's `RoleConfig` allows: tools are pass-through *only if* the role's
    `tools` includes them (define `app.role("viewer", tools=["*"])` to allow all),
    while **subagents and workflows remain default-deny** unless the role lists
    them explicitly.

There is currently no programmatic `role_resolver` hook — role assignment is
declarative via `user_roles` + `default_role`.

## How enforcement works

The unified capability filter runs as middleware **before the LLM sees the toolset**. Tools the role can't access are stripped from the model's tool list entirely — the agent can't call what it can't see.

Subagents and workflows have an additional gate: even if a subagent type appears in the toolset, the `task` tool checks `RoleConfig.subagents` at call time.

## Startup validation

`validate_capability_registry` runs at startup and raises `ValueError` if any axis is misconfigured — missing `RoleConfig` field, unreserved name prefix, or no enforcement shape. Misconfiguration fails loudly rather than silently passing through.

See [`examples/rbac_showboat.py`](https://github.com/tisu19021997/langclaw/blob/main/examples/rbac_showboat.py) for a runnable tour of all three axes.
