# Tools

Tools are async functions the agent can call. Register them with `@app.tool()`.

## Basic tool

```python
from langclaw import Langclaw

app = Langclaw()

@app.tool()
async def get_weather(city: str) -> dict:
    """Get current weather for a city."""
    # real implementation: call a weather API
    return {"city": city, "temp": 22, "condition": "sunny"}
```

The function name becomes the tool name. The docstring becomes the tool description shown to the LLM.

## Error handling

Tools must return error dicts — never raise into the agent:

```python
@app.tool()
async def fetch_data(url: str) -> dict:
    """Fetch JSON from a URL."""
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(url)
            r.raise_for_status()
            return r.json()
    except Exception as e:
        return {"error": str(e)}  # correct
        # raise  # wrong — breaks the agent loop
```

## Custom tool name

By default the tool name is the function name. To use a different name, pre-wrap
the function with LangChain's `@tool("name")` — `@app.tool()` registers an
existing `BaseTool` as-is:

```python
from langchain_core.tools import tool

@app.tool()
@tool("search_web")
async def _search(query: str) -> list[dict]:
    """Search the web for a query."""
    ...
```

## Bring an existing LangChain tool

Any LangChain `BaseTool` instance drops in via `register_tool()` (or
`register_tools([...])` for several at once) — this is how you reuse the
LangChain tool ecosystem:

```python
from langchain_community.tools import DuckDuckGoSearchRun

app.register_tool(DuckDuckGoSearchRun())
app.register_tools([tool_a, tool_b], roles=["analyst"])  # optional RBAC grant
```

`@app.tool()` is sugar for "wrap this function, then register it";
`register_tool()` skips the wrapping for tools you already built.

## Built-in tools

Langclaw ships built-in tools under `langclaw.agents.tools`. They're wired automatically when the relevant config is set:

| Tool | Extra required | Config key |
|---|---|---|
| `web_search` | `search` | `LANGCLAW__TOOLS__BRAVE_API_KEY` (default backend) — see note |
| `web_fetch` | `search` | — |
| `read_email` / `search_emails` / `send_email` / `draft_email` / `reply_email` / `manage_labels` | `gmail` | `LANGCLAW__TOOLS__GMAIL__ENABLED=true` (+ OAuth client) |
| `cron` | — | `LANGCLAW__CRON__ENABLED=true` |
| `task` | — | subagents registered |
| `eval` | `interpreter` | `LANGCLAW__INTERPRETER__ENABLED=true` |

!!! note "`web_search` needs a backend key"
    The default search backend is `brave`, which requires `LANGCLAW__TOOLS__BRAVE_API_KEY`.
    Set `LANGCLAW__TOOLS__SEARCH_BACKEND=tavily` (with `…TAVILY_API_KEY`) or
    `=duckduckgo` (no key required) to switch. Without a usable backend, `web_search`
    is silently omitted from the toolset.

## RBAC

Tools participate in the three-axis RBAC system. See the [RBAC guide](rbac.md) for role definitions that gate tool access.
