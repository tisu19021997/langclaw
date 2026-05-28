# Langclaw Development Guide

Multi-channel AI agent framework built on LangChain, LangGraph, and deepagents.

See @AGENTS.md for package map and code conventions.
See @docs/ARCHITECTURE.md for design rationale.

## Quick Reference

```bash
uv sync --group dev              # Install all deps
uv run pytest tests/ -v          # Run tests
uv run ruff check . --fix        # Lint + auto-fix
uv run ruff format .             # Format code
uv run pre-commit run --all-files  # Full pre-commit suite
```

## Key File Locations

| Task | Primary File(s) |
|------|-----------------|
| Add built-in tool | `langclaw/agents/tools/` + export in `__init__.py` |
| Add channel | `langclaw/gateway/<name>.py` subclassing `BaseChannel` |
| Add middleware | `langclaw/middleware/` + wire in `agents/builder.py` |
| Add message bus | `langclaw/bus/<name>.py` + factory in `bus/__init__.py` |
| Add checkpointer | `langclaw/checkpointer/<name>.py` + factory in `checkpointer/__init__.py` |
| Modify config schema | `langclaw/config/schema.py` (Pydantic Settings) |
| CLI commands | `langclaw/cli/app.py` (Typer) |
| Agent construction | `langclaw/agents/builder.py` |
| Gateway orchestration | `langclaw/gateway/manager.py` |
| Register named agents | `langclaw/app.py` (`app.agent()`) |
| Agent routing logic | `langclaw/gateway/manager.py` (`_resolve_agent_name`) |
| Active agent persistence | `langclaw/session/manager.py` (`get_active_agent` / `set_active_agent`) |
| Register a workflow | `langclaw/app.py` (`app.workflow()`) + fn in `langclaw/workflows/` |
| Workflow execution / progress | `langclaw/workflows/runner.py`, `context.py` |
| Agent-spawned workflow tools | `langclaw/agents/tools/workflow.py` (`run_workflow`, `orchestrate`) |
| Declarative plan (compose) | `langclaw/workflows/plan.py`, `interpret.py` |

## Extension Patterns

### Adding a Channel

Subclass `BaseChannel` in `langclaw/gateway/base.py`:

```python
class MyChannel(BaseChannel):
    name = "my_channel"

    async def start(self, bus: BaseMessageBus) -> None:
        # Connect and publish InboundMessage to bus
        ...

    async def send_ai_message(self, msg: OutboundMessage) -> None:
        # Deliver AI response to user (required)
        ...

    async def stop(self) -> None:
        # Cleanup resources
        ...

    # Optional overrides:
    # async def send_tool_progress(self, msg) -> None: ...
    # async def send_tool_result(self, msg) -> None: ...
```

Add config in `config/schema.py`, enable in `app.py:_build_all_channels()`.

### Adding a Message Bus

Subclass `BaseMessageBus` in `langclaw/bus/base.py`:

```python
class MyBus(BaseMessageBus):
    async def start(self) -> None: ...
    async def stop(self) -> None: ...
    async def publish(self, msg: InboundMessage) -> None: ...
    def subscribe(self) -> AsyncIterator[InboundMessage]: ...
```

Register in `bus/__init__.py:make_message_bus()` factory.

### Adding Middleware

Create in `langclaw/middleware/`, then add to stack in `agents/builder.py`:

```python
middleware: list[Any] = [
    ChannelContextMiddleware(),      # 1. Inject channel metadata (first)
    # ToolPermissionMiddleware,      # 2. RBAC filtering (if enabled)
    RateLimitMiddleware(...),        # 3. Rate limiting
    ContentFilterMiddleware(...),    # 4. Content filtering
    PIIMiddleware(...),              # 5. PII redaction
    *(extra_middleware or []),       # 6. User-provided (last)
]
```

Order matters: earlier middleware runs first on input, last on output.

### Adding a Checkpointer

Subclass `BaseCheckpointerBackend` in `langclaw/checkpointer/base.py`:

```python
class MyCheckpointer(BaseCheckpointerBackend):
    async def __aenter__(self) -> Self: ...
    async def __aexit__(self, *_) -> None: ...
    def get(self) -> Checkpointer: ...  # Return LangGraph checkpointer
```

Register in `checkpointer/__init__.py:make_checkpointer_backend()`.

### Named Agents (multi-agent switching)

Register independent named agents on the app. Each gets its own LangGraph thread
(`context_id = "agent:<name>"`) so conversation history never bleeds across agents.

```python
app.agent(
    "researcher",
    description="Deep research with web tools",
    system_prompt="You are a meticulous researcher. Always cite sources.",
    tools=[web_search, web_fetch],          # None → inherits config-driven tools
    model="openai:gpt-4.1",                 # None → inherits default model
)
```

Users interact via the built-in `/agent` command (registered automatically):

```
/agent                          → list all agents with active marker
/agent researcher               → switch to researcher agent (persistent)
/agent default                  → return to main agent
/agent researcher What is X?    → one-off message to researcher (no session change)
```

WebSocket clients can also specify the target agent via metadata:

```json
{
  "type": "message",
  "content": "Summarize the quarterly report",
  "metadata": { "agent_name": "researcher" }
}
```

**`_resolve_agent_name` priority order** (in `gateway/manager.py`):
1. `msg.metadata["agent_name"]` — stamped by cron at schedule time (deterministic, restart-safe)
2. Phase 2 `agent_resolver` hook — auto-routing (not yet implemented; stub in `_resolve_agent_name`)
3. `SessionManager.get_active_agent()` — set by `/agent` (per user, in-memory)
4. `"default"` — fallback

**Cron + named agents:** The cron tool derives `agent_name` from `ctx.context_id`
(set to `"agent:<name>"` when a named agent is active) at schedule time and stamps it
into the job's `fire_kwargs`. On fire, it appears in `InboundMessage.metadata["agent_name"]`
and takes priority over the user's current interactive session. Old persisted jobs without
the field default to `""` and fall through to the next priority level — fully backward compatible.

**Adding Phase 2 auto-routing:** Uncomment the `agent_resolver` stub in
`GatewayManager._resolve_agent_name` and wire a `Callable[[InboundMessage], Awaitable[str | None]]`
through `GatewayManager.__init__` and `Langclaw._run_async`.

### Dynamic Workflows

Register an async workflow that orchestrates named agents. All triggers set
`metadata["workflow_name"]` and flow through the bus → `GatewayManager._handle`
→ `WorkflowRunner.dispatch`:

```python
@app.workflow("digest", description="Summarise overnight commits")
async def digest(ctx: WorkflowContext) -> str:
    await ctx.phase("Fetch")
    raw = await ctx.run(ctx.input, agent="researcher")   # delegate to a named agent
    return await ctx.run(f"Summarise: {raw}")            # ctx.run / parallel / pipeline
```

Three trigger paths:
- `/workflow <name> [input]` — human; result → channel.
- `run_workflow(name, input)` / `orchestrate(goal, steps)` agent tools — spawned
  from chat; result calls back via `InboundMessage(origin="workflow", to="agent")`.
- `cron(action="add", workflow="<name>", ...)` — scheduled; result → channel.

**Compose (no predefined fn):** `orchestrate` takes a declarative `WorkflowPlan`
DAG (`workflows/plan.py`) as tool args — the agent is the planner, no code exec —
executed by the built-in `_interpret` workflow over named agents.

The subsystem (runner, `/workflow`, agent tools) activates when a workflow is
registered via `@app.workflow()`, **or** when opted in with
`Langclaw(enable_workflows=True)` / `LANGCLAW__WORKFLOWS__ENABLED=true` — the
opt-in gives the agent `orchestrate` (compose a dynamic workflow) with no
predefined workflow. Gate logic: `Langclaw._workflows_active()`. Full guide:
`docs/workflows.md`; runnable example: `examples/research_workflow.py`.

## Message Flow

```
User message flow:
Channel → InboundMessage → Bus → GatewayManager._handle()
  → _resolve_agent_name()          # pick agent (metadata > session > default)
  → SessionManager.get_config()    # get/create LangGraph thread
  → active_agent.astream()         # run chosen agent
  → OutboundMessage → Channel

Command flow (bypasses LLM):
Channel → /command → CommandRouter → instant response

Cron flow:
APScheduler → _fire_job() → InboundMessage(origin="cron", metadata={agent_name | workflow_name}) → Bus → same as user flow

Workflow flow:
/workflow · run_workflow/orchestrate tool · cron(workflow=)
  → InboundMessage(metadata={workflow_name, [notify_agent, _depth, plan]}) → Bus
  → _handle() → WorkflowRunner.dispatch() → workflow fn (ctx.run delegates to named agents)
  → result → channel (human/cron) OR InboundMessage(origin="workflow", to="agent") (agent-spawned)
```

Key routing fields on `InboundMessage`:
- `origin`: `"user"` | `"cron"` | `"heartbeat"` | `"subagent"` | `"workflow"`
- `to`: `"agent"` (default) | `"channel"` (bypass agent)
- `metadata["agent_name"]`: explicit agent target (stamped by cron at schedule time)
- `metadata["workflow_name"]`: dispatch to a workflow instead of the agent

## Common Pitfalls

### Tool Error Handling

Tools must return error dicts, never raise into the agent:

```python
@app.tool()
async def my_tool(query: str) -> dict:
    try:
        return {"result": do_work(query)}
    except SomeError as e:
        return {"error": str(e)}  # Correct
        # raise  # Wrong — breaks agent loop
```

### Type Annotations

Use modern syntax (Python 3.11+):

```python
# Correct
def foo(items: list[str], value: int | None = None) -> dict[str, Any]: ...

# Wrong — never use typing module equivalents
def foo(items: List[str], value: Optional[int] = None) -> Dict[str, Any]: ...
```

### Logging

Use loguru with f-strings, not stdlib logging:

```python
from loguru import logger

logger.info(f"Processing message from {user_id}")
logger.error(f"Failed to connect: {exc}")
```

### Commands vs Tools

- **Commands** (`/start`, `/reset`, `/help`, `/agent`): Fast system ops, bypass bus and LLM entirely
- **Tools**: LLM-invoked functions, go through full middleware pipeline

Don't implement user-facing quick actions as tools — use `@app.command()`.

`/agent` is registered automatically by `GatewayManager._setup_agent_command()` as a closure
when at least one named agent exists. It calls `SessionManager.set_active_agent()` for persistent
switches and publishes directly to the bus for one-off messages.

### Workflow Completion Messages

Agent-spawned workflows (`notify_agent=True`) call back by publishing
`InboundMessage(origin="workflow", to="agent")`. It **must not** carry
`workflow_name` — `_handle` would re-dispatch it and loop forever. The `_depth`
key (capped, default 3) guards agent→workflow→agent recursion: propagate it, don't drop it.

## Testing

```bash
uv run pytest tests/ -v                    # All tests
uv run pytest tests/test_gateway.py -v     # Specific module
uv run pytest -k "test_telegram" -v        # Pattern match
```

Tests use `pytest-asyncio` with `asyncio_mode = "auto"`.

## Environment Variables

Config uses `LANGCLAW__` prefix with nested `__` delimiters:

```bash
LANGCLAW__AGENTS__MODEL=openai:gpt-4.1
LANGCLAW__CHANNELS__TELEGRAM__TOKEN=bot123:abc
LANGCLAW__CHANNELS__TELEGRAM__ENABLED=true
LANGCLAW__BUS__BACKEND=rabbitmq
LANGCLAW__CHECKPOINTER__BACKEND=postgres
```
