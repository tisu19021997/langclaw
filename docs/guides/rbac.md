# RBAC

Langclaw's RBAC is three-axis and **default-deny on every axis**: enabling permissions restricts tools, subagents, *and* workflows for everyone. A *defined* role sees only what it lists (`["*"]` grants all, `[]` grants none); an *unknown* role — including an unlisted user whose `default_role` you never registered — gets nothing. Turning RBAC on locks things down; you open them back up by defining roles.

## Define roles

```python
app.role("admin",    tools=["*"],            subagents=["*"],            workflows=["*"])
app.role("analyst",  tools=["*"],            subagents=["researcher"],   workflows=["digest"])
app.role("free",     tools=["web_search"],   subagents=[],               workflows=[])
```

## Three axes

| Axis | `RoleConfig` field | Default |
|---|---|---|
| Tools | `tools` | **deny** — defined role sees only what it lists (`["*"]`=all, `[]`=none); unknown role → none |
| Subagents | `subagents` | **deny** — must be explicitly allowed |
| Workflows | `workflows` | **deny** — must be explicitly allowed |

All three axes are independent: a role with `tools=["*"]` still gets no subagents or workflows unless it lists them.

## Enable RBAC

RBAC is **off by default** — every user sees every tool (the capability filter isn't even installed). Turn it on:

```bash
LANGCLAW__PERMISSIONS__ENABLED=true
LANGCLAW__PERMISSIONS__DEFAULT_ROLE=viewer   # role for unlisted users (default: viewer)
```

Enabling RBAC is **default-deny**: until you define roles and grant capabilities, unlisted users get nothing. If you don't register the `default_role` (e.g. `viewer`), unlisted users are an unknown role and see **no tools, subagents, or workflows** — fail-closed, as you'd expect from a permissions switch.

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

!!! note "Unlisted users are denied until you grant the `default_role`"
    An unlisted user is assigned `default_role` (`"viewer"`). Enabling RBAC is
    default-deny on every axis, so if you never register that role with
    `app.role(...)` it resolves to **no tools, subagents, or workflows** — the
    safe direction. To give unlisted users a baseline, define the role
    explicitly: `app.role("viewer", tools=["web_search"])`, or
    `app.role("viewer", tools=["*"])` to restore open tool access for everyone
    not otherwise mapped.

There is currently no programmatic `role_resolver` hook — role assignment is
declarative via `user_roles` + `default_role`.

## How enforcement works

The unified capability filter runs as middleware **before the LLM sees the toolset**. Tools the role can't access are stripped from the model's tool list entirely — the agent can't call what it can't see.

Subagents and workflows have an additional gate: even if a subagent type appears in the toolset, the `task` tool checks `RoleConfig.subagents` at call time.

## Startup validation

`validate_capability_registry` runs at startup and raises `ValueError` if any axis is misconfigured — missing `RoleConfig` field, unreserved name prefix, or no enforcement shape. Misconfiguration fails loudly rather than silently passing through.

See [`examples/rbac_showboat.py`](https://github.com/tisu19021997/langclaw/blob/main/examples/rbac_showboat.py) for a runnable tour of all three axes.
