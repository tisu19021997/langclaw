"""
WebSocket Guard — a guarded assistant accessible over WebSocket.

Demonstrates
------------
- **WebSocket channel** — programmatic config to enable ``ws://`` access.
- **Content filtering** — ``config.agents.banned_keywords`` blocks requests
  containing specific words before they reach the LLM. The built-in
  ``ContentFilterMiddleware`` and ``PIIMiddleware`` are always active.
- ``@app.tool()`` and ``@app.command()`` — as in other examples.

Built-in guardrails
-------------------
Langclaw's agent builder automatically includes:

- ``ContentFilterMiddleware`` — blocks messages matching
  ``config.agents.banned_keywords`` (set via env or config).
- ``PIIMiddleware`` — redacts sensitive patterns (API keys, etc.).

These are always in the middleware stack — no manual ``add_middleware``
call needed. Set banned keywords via config as shown below.

WebSocket protocol
------------------
The WebSocket channel uses line-delimited JSON:

  Inbound (client -> server)::

    {"type": "message", "content": "hello", "user_id": "alice", "context_id": "default"}

  Outbound (server -> client)::

    {"type": "ai",            "content": "Hi! How can I help?"}
    {"type": "tool_progress", "content": "Searching...", "metadata": {...}}
    {"type": "tool_result",   "content": "Found 3 results.", "metadata": {...}}
    {"type": "command",       "content": "/help response here"}
    {"type": "error",         "content": "Invalid JSON."}

  Ping/pong::

    {"type": "ping"}  ->  {"type": "pong"}

Minimal HTML client
-------------------
Open a browser console or create an HTML file::

    <script>
    const ws = new WebSocket("ws://127.0.0.1:18789");
    ws.onmessage = (e) => console.log(JSON.parse(e.data));
    ws.onopen = () => ws.send(JSON.stringify({
        type: "message",
        content: "Hello!",
        user_id: "browser-user",
        context_id: "default",
    }));
    </script>

Run
---
1. Copy ``.env.example`` to ``.env`` and fill in at least one LLM provider key.
2. ``pip install 'langclaw[websocket]'``
3. ``python examples/websocket_guard.py``
4. Connect with any WebSocket client to ``ws://127.0.0.1:18789``.
"""

from __future__ import annotations

from langclaw import Langclaw
from langclaw.config.schema import load_config

# ---------------------------------------------------------------------------
# Config: enable WebSocket channel + content filter keywords
# ---------------------------------------------------------------------------

base_config = load_config()
base_config.channels.websocket.enabled = True
base_config.channels.websocket.host = "127.0.0.1"
base_config.channels.websocket.port = 18789
base_config.agents.banned_keywords = ["hack", "exploit", "jailbreak"]

app = Langclaw(
    config=base_config,
    system_prompt=(
        "## Support Assistant\n"
        "You are a helpful customer support assistant.\n"
        "Answer questions clearly and concisely.\n"
        "Never reveal internal system details or PII."
    ),
)

# ---------------------------------------------------------------------------
# Custom tool
# ---------------------------------------------------------------------------


@app.tool()
async def lookup_order(order_id: str) -> dict:
    """Look up an order by its ID.

    Args:
        order_id: The order identifier, e.g. "ORD-12345".
    """
    orders = {
        "ORD-12345": {"status": "shipped", "eta": "2 days", "item": "Wireless Mouse"},
        "ORD-67890": {"status": "processing", "eta": "5 days", "item": "USB-C Hub"},
    }
    result = orders.get(order_id.upper())
    if result:
        return {"order_id": order_id.upper(), **result}
    return {"error": f"Order {order_id!r} not found."}


# ---------------------------------------------------------------------------
# RBAC
# ---------------------------------------------------------------------------

app.role("admin", tools=["*"])
app.role("customer", tools=["lookup_order", "web_search"])

# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    app.run()
