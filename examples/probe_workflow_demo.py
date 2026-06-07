"""
Probe + Saved-Workflow demo — drive the merged *scripted workflow* feature
end-to-end through the **probe harness** (PR #58), with no human tapping a chat.

What this proves
----------------
This is an isolated, WebSocket-only gateway that has the *scripted workflow*
primitive turned on and one saved workflow pre-seeded on disk:

    workflows/topic_digest.js   — a read-only "research a topic" digest

It exists so the probe (``langclaw probe ...``) can inject a user turn through
the real ``gateway → bus → agent → channel`` pipeline and assert on the result:

- **Keyless / deterministic** — ``/workflows list`` and ``/workflows run
  topic_digest …`` go through the command path and the zero-LLM
  ``origin="workflow"`` dispatch, so the saved JS body runs verbatim in the
  QuickJS sandbox **without ever calling the model**. No LLM key needed.
- **Live** — a natural-language turn ("digest the latest on …") needs an LLM
  key; the model then chooses the ``workflow_topic_digest`` tool itself.

Everything is rooted under ``<repo>/.probe_demo`` so it never touches your real
``~/.langclaw`` state, and it forces the ``filesystem`` backend (no host
``execute`` shell tool) and the keyless ``duckduckgo`` search backend.

Note
----
The ``langclaw probe`` commands below require the probe harness (PR #58). The
gateway and the two saved workflows themselves run on the current ``main``.

Run
---
    # 1. start the isolated probe gateway (this file)
    uv run python examples/probe_workflow_demo.py
    #    → listening on ws://127.0.0.1:18789

    # 2. in another shell, drive it with the probe — no LLM key required:
    uv run langclaw probe "/workflows list"
    uv run langclaw probe '/workflows run topic_digest {"topic": "langgraph"}'

    # 3. with an LLM key set (e.g. ANTHROPIC_API_KEY), drive the model itself:
    uv run langclaw probe "Give me a web digest on open-source AI agents"
"""

from __future__ import annotations

import os
from pathlib import Path

from langclaw import Langclaw


def _configure_model(cfg) -> str:
    """Point the agent at whatever provider key is in the environment.

    OpenRouter is OpenAI-compatible, so we use the ``openai:`` provider with its
    base_url + key. Falls back to OpenAI, then the configured Anthropic default.
    Returns a short human label of what got wired (for the startup banner).
    """
    if os.getenv("OPENROUTER_API_KEY"):
        model = os.getenv("OPENROUTER_MODEL", "openai/gpt-4o-mini")
        cfg.agents.model = f"openai:{model}"
        cfg.agents.model_kwargs = {
            "base_url": "https://openrouter.ai/api/v1",
            "api_key": os.environ["OPENROUTER_API_KEY"],
        }
        return f"OpenRouter → {model}"
    if os.getenv("OPENAI_API_KEY"):
        cfg.agents.model = os.getenv("OPENAI_MODEL", "openai:gpt-4.1")
        return f"OpenAI → {cfg.agents.model}"
    # Anthropic default is already in cfg.agents.model; live run needs the key.
    return f"default → {cfg.agents.model} (set a provider key for the live run)"


# --------------------------------------------------------------------------- #
# The saved workflow — a read-only "research a topic" digest.
#
# A saved workflow is just a JS file in the workspace ``workflows/`` folder. The
# body runs in the QuickJS sandbox: it gets the run input as the global ``inp``,
# calls allow-listed tools as ``tools.<camelName>(...)``, narrates progress via
# ``tools.phase`` / ``tools.log``, and returns its result via ``tools.output``.
#
# This one does a single web_search (keyless DuckDuckGo backend) so it stays
# well inside the sandbox time budget, and degrades gracefully: if search is
# rate-limited it still returns a well-formed brief noting the outage — so the
# end-to-end pipeline is exercised either way.
# --------------------------------------------------------------------------- #

TOPIC_DIGEST_JS = r"""
// Robust input: inp may be {topic}, a bare string, a stringified JSON object
// (the agent tool path doesn't json-parse like /workflows run does), or null.
let arg = inp;
if (typeof arg === "string" && arg.trim().charAt(0) === "{") {
  try { arg = JSON.parse(arg); } catch (e) { /* keep as string */ }
}
let topic = "open-source AI agents";
if (arg && typeof arg === "object" && typeof arg.topic === "string" && arg.topic.trim()) {
  topic = arg.topic.trim();
} else if (typeof arg === "string" && arg.trim()) {
  topic = arg.trim();
}

await tools.phase({ name: "gather" });
await tools.log({ message: "searching the web for: " + topic });
const res = await tools.webSearch({ query: topic, n: 5 });

await tools.phase({ name: "synthesize" });
const lines = ["# Web digest: " + topic, ""];
if (Array.isArray(res)) {
  if (res.length === 0) {
    lines.push("_No results returned._");
  } else {
    for (let i = 0; i < res.length; i++) {
      const r = res[i] || {};
      const title = r.title || r.content || "(untitled)";
      const url = r.url || "";
      lines.push((i + 1) + ". " + title + (url ? " — " + url : ""));
    }
  }
} else if (res && res.error) {
  lines.push("_Search unavailable: " + res.error + "_");
} else {
  lines.push("_Unexpected search result shape._");
}

await tools.output({
  result: {
    topic: topic,
    result_count: Array.isArray(res) ? res.length : 0,
    brief: lines.join("\n"),
  },
});
"""


# --------------------------------------------------------------------------- #
# A second read use case — fetch a specific web page and digest it (a different
# read modality from search). Uses web_fetch, a langclaw web tool (so it lives
# in the agent's tool list the workflow sandbox is built from).
#
# NOTE: saved workflows can only `@uses` langclaw-registered tools (web_search,
# web_fetch, gmail, cron, …). The deepagents *backend* file tools (read_file,
# ls, glob, grep) are injected inside create_deep_agent and are NOT in that list,
# so a workflow cannot `@uses read_file` — even though the `eval` interpreter can.
# --------------------------------------------------------------------------- #

PAGE_DIGEST_JS = r"""
// Robust input: inp may be {url}, a bare string, a stringified JSON object, or null.
let arg = inp;
if (typeof arg === "string" && arg.trim().charAt(0) === "{") {
  try { arg = JSON.parse(arg); } catch (e) { /* keep as string */ }
}
let url = "https://raw.githubusercontent.com/langchain-ai/langgraph/main/README.md";
if (arg && typeof arg === "object" && typeof arg.url === "string" && arg.url.trim()) {
  url = arg.url.trim();
} else if (typeof arg === "string" && arg.trim()) {
  url = arg.trim();
}

await tools.phase({ name: "fetch" });
await tools.log({ message: "fetching: " + url });
const res = await tools.webFetch({ urls: [url] });

await tools.phase({ name: "digest" });
const doc = Array.isArray(res) && res.length > 0 ? res[0] : {};
const content = (doc && doc.content) || "";
const title = (doc && doc.title) || "";
await tools.output({
  result: {
    url: url,
    title: title,
    content_length: content.length,
    preview: content.slice(0, 280),
  },
});
"""


def build_app(port: int = 18789) -> Langclaw:
    """Build the isolated probe gateway with the saved workflow pre-seeded."""
    # Isolate all on-disk state under <repo>/.probe_demo so we never touch the
    # developer's real ~/.langclaw (state.db, cron.db, workspace).
    repo_root = Path(__file__).resolve().parent.parent
    demo_root = repo_root / ".probe_demo"
    workspace = demo_root / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)

    app = Langclaw(
        system_prompt=(
            "## Research Assistant\n"
            "When the user asks you to research, digest, or get the latest on a "
            "topic, call the `workflow_topic_digest` tool with the topic. It "
            "searches the web and returns a structured brief. Pass the user's "
            'topic as `workflow_input` like {"topic": "..."}.'
        ),
    )

    cfg = app.config
    # Turn on the scripted-workflow stack: workflows + the QuickJS interpreter
    # (saved workflows are JS bodies, so they need the interpreter sandbox).
    #
    # NOTE: we set these on the *config* (not the Langclaw(enable_interpreter=True)
    # constructor flag) on purpose. _reload_saved_workflows() gates on the raw
    # config.interpreter.enabled, and the constructor flag does NOT flip that —
    # so the flag alone silently disables saved-workflow reconcile. Setting the
    # config flag is the documented (LANGCLAW__INTERPRETER__ENABLED=true) path.
    cfg.interpreter.enabled = True
    cfg.workflows.enabled = True
    # Fully isolate the agent workspace (AGENTS.md, skills, memories) under our
    # demo dir so we never read the developer's real ~/.langclaw persona.
    cfg.agents.root_dir = str(demo_root)
    # Filesystem backend (file tools, NO host `execute`), rooted in our demo dir.
    cfg.agents.backend.backend = "filesystem"
    cfg.agents.backend.root_dir = str(workspace)
    # Keyless DuckDuckGo search so the web-digest body needs no API key.
    cfg.tools.search_backend = "duckduckgo"
    # Keep the test surface small and self-contained.
    cfg.checkpointer.sqlite.db_path = str(demo_root / "state.db")
    cfg.cron.enabled = False
    cfg.heartbeat.enabled = False
    cfg.channels.websocket.port = port
    model_label = _configure_model(cfg)
    print(f"[probe-demo] model: {model_label}")

    # Pre-seed the saved workflow file the agent would normally write itself.
    # SavedWorkflowStore.save writes the canonical `// @description` / `// @uses`
    # header + body — exactly what the folder-watch loader parses on startup.
    from langclaw.workflows.saved_store import SavedWorkflowStore

    store = SavedWorkflowStore(cfg.agents.workflows_dir)
    store.save(
        "topic_digest",
        script=TOPIC_DIGEST_JS,
        description="Research a topic from the live web and return a structured digest.",
        uses_tools=["web_search"],
    )
    store.save(
        "page_digest",
        script=PAGE_DIGEST_JS,
        description="Fetch a specific web page and return a structured digest (title + preview).",
        uses_tools=["web_fetch"],
    )
    print(f"[probe-demo] seeded 2 saved workflows → {cfg.agents.workflows_dir}")
    print(f"[probe-demo] starting WebSocket-only probe gateway on ws://127.0.0.1:{port}")
    return app


if __name__ == "__main__":
    build_app().run(probe=True)
