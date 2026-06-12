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

## Assign roles to users

Roles are resolved per-request based on the `user_id` from the inbound message. Wire a resolver:

```python
@app.role_resolver
async def resolve_role(user_id: str) -> str:
    if user_id in ADMIN_IDS:
        return "admin"
    if await is_paid_user(user_id):
        return "analyst"
    return "free"
```

Without a resolver, all users get the default role (no restrictions).

## How enforcement works

The unified capability filter runs as middleware **before the LLM sees the toolset**. Tools the role can't access are stripped from the model's tool list entirely — the agent can't call what it can't see.

Subagents and workflows have an additional gate: even if a subagent type appears in the toolset, the `task` tool checks `RoleConfig.subagents` at call time.

## Startup validation

`validate_capability_registry` runs at startup and raises `ValueError` if any axis is misconfigured — missing `RoleConfig` field, unreserved name prefix, or no enforcement shape. Misconfiguration fails loudly rather than silently passing through.

See [`examples/rbac_showboat.py`](https://github.com/tisu19021997/langclaw/blob/main/examples/rbac_showboat.py) for a runnable tour of all three axes.
