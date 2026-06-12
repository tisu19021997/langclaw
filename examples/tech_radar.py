"""
Tech Radar — a real, schedulable intelligence brief, defined three ways.

This is the example behind langclaw's workflow story. The *job* is the same in
every case: for each topic you track, search the web, dedupe the hits by domain,
and synthesise one dated markdown digest. What changes is **who writes the
control flow** — and that is the whole point.

The same orchestration, three ways
----------------------------------
1. ``@app.workflow("tech_radar")`` — **you** author it (this file). Python,
   reviewed, typed at the input boundary, unit-testable, deterministic. The
   recommended path for anything that ships.

2. ``workflows/tech_radar_live.js`` — **the agent** authors it. The user says
   *"run a workflow to scan these topics"*, the model writes a sandboxed JS body
   via the ``eval`` interpreter, and when it's good the model saves it with its
   ordinary ``write_file`` to ``workflows/<name>.js``. The file is the source of
   truth; it loads as a ``workflow_tech_radar_live`` tool. (This example seeds
   that file programmatically so it runs out of the box — ``SavedWorkflowStore``
   writes the exact canonical format ``write_file`` would.)

3. ``mode="llm_authored"`` (not shown here; see ``examples/workflow_research.py``)
   — you declare only the contract and the LLM authors the body fresh per run.

All three are invoked the same way: the agent calls ``workflow_<name>``, a user
runs ``/workflows run <name> {json}``, **or a cron job fires it** — and a saved
workflow on cron re-runs the frozen body with *zero LLM cost*, delivering the
digest to whichever channel scheduled it (e.g. a 7am brief to your Telegram).

Kinship with Claude Code's dynamic workflows
--------------------------------------------
https://claude.com/blog/a-harness-for-every-task-dynamic-workflows-in-claude-code
Same idea — the model builds a harness instead of grinding through a task in one
context window. langclaw's honest twist: that harness doesn't die at the end of
the task. Save it, and it becomes a durable, scheduled, channel-delivered
routine. (The honest limit, too: langclaw's sandbox is tuned for scripted
control flow and a few subagents, not Claude's tens-to-hundreds-agent fan-out.)

Run it
------
    LANGCLAW__WORKFLOWS__ENABLED=true \
    LANGCLAW__INTERPRETER__ENABLED=true \
    uv run python examples/tech_radar.py
    # then, in another terminal, drive it through the real pipeline with the probe:
    uv run langclaw probe '/workflows'
    uv run langclaw probe '/workflows run tech_radar {"topics": ["LangGraph", "MCP"]}'
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

from pydantic import BaseModel, Field

from langclaw import Langclaw
from langclaw.config.schema import LangclawConfig
from langclaw.workflows import SavedWorkflowStore

# ---------------------------------------------------------------------------
# Config — everything the three paths need, and nothing else.
# ---------------------------------------------------------------------------

config = LangclawConfig()

# A local WebSocket channel so you can drive it with the probe / a WS client and
# never touch a real Telegram/Discord chat. Defaults to 127.0.0.1:18789 — the
# probe's default URL — so `langclaw probe ...` connects with no flags. Force the
# outward-facing channels OFF so this example can never hijack a real bot the
# ambient environment happens to have configured (LANGCLAW__CHANNELS__…).
config.channels.websocket.enabled = True
config.channels.telegram.enabled = False
config.channels.discord.enabled = False
config.channels.matrix.enabled = False

# Workflows are off by default; the saved-JS path needs the interpreter on too.
config.workflows.enabled = True
config.interpreter.enabled = True

# Keyless-of-LLM web search: Brave if a key is configured (LANGCLAW__TOOLS__…),
# else DuckDuckGo, which needs no key at all. Either way the *orchestration* runs
# with no model call, so `/workflows run` and cron fire it for free.
config.tools.search_backend = "brave" if config.tools.brave_api_key else "duckduckgo"

# Root the agent filesystem (and therefore the saved-`workflows/` folder) at a
# known directory next to this example, so the seeded saved workflow is visible.
_WS = (Path(__file__).resolve().parent.parent / ".tech_radar_workspace").resolve()
config.agents.backend.root_dir = str(_WS)

app = Langclaw(
    config=config,
    system_prompt=(
        "## Tech Radar\n"
        "When the user asks what's new across a set of topics, call the "
        "`tech_radar` workflow — it scans every topic in parallel and returns one "
        "dated brief. For a single lookup, a plain web_search is fine."
    ),
)


# ---------------------------------------------------------------------------
# 1) Operator-authored Python workflow — the recommended path.
# ---------------------------------------------------------------------------


class RadarBrief(BaseModel):
    """Typed input — validated at the run boundary before the body runs."""

    topics: list[str] = Field(
        default=["LangGraph", "AI agents", "Model Context Protocol"],
        description="The topics to scan, one parallel search each.",
    )
    per_topic: int = Field(default=4, ge=1, le=8, description="Links to keep per topic.")


def _rank(hits: list[dict], keep: int) -> list[dict]:
    """Dedupe search hits by domain (one per source) and keep the top *keep*."""
    seen: set[str] = set()
    out: list[dict] = []
    for hit in hits:
        host = urlparse(hit.get("url", "")).netloc.removeprefix("www.")
        if not host or host in seen:
            continue
        seen.add(host)
        out.append(hit)
        if len(out) >= keep:
            break
    return out


@app.workflow(
    "tech_radar",
    input=RadarBrief,
    max_concurrency=4,
    description=(
        "Scan a list of topics and return one dated markdown brief. Runs one "
        "parallel web_search per topic, dedupes hits by domain, then synthesises. "
        "Use this whenever the user wants 'what's new across X, Y, Z'."
    ),
)
async def tech_radar(ctx, inp: RadarBrief) -> str:
    # Fan out one search per topic. The factory closes each thunk over its OWN
    # topic (not the loop variable); ctx.parallel runs them bounded by
    # max_concurrency and returns results in input order.
    def _scan(topic: str):
        return lambda c: c.tool("web_search", query=f"{topic} latest news", n=inp.per_topic + 3)

    ctx.phase("scan")
    findings = await ctx.parallel([_scan(t) for t in inp.topics])

    ctx.phase("synthesize")
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    out = [f"# Tech radar — {today}", ""]
    for topic, hits in zip(inp.topics, findings, strict=False):
        ctx.log(f"ranking {topic}")
        out.append(f"## {topic}")
        if not isinstance(hits, list):  # web_search returned {"error": ...}
            out.append(f"_search failed: {hits}_\n")
            continue
        ranked = _rank(hits, inp.per_topic)
        if not ranked:
            out.append("_no results_\n")
            continue
        for hit in ranked:
            title = (hit.get("title") or "untitled").strip()
            url = hit.get("url", "")
            out.append(f"- [{title}]({url})")
        out.append("")
    return "\n".join(out)


# ---------------------------------------------------------------------------
# 2) Agent-authored saved workflow — seeded here so the example is self-contained.
#
# In real use the agent WRITES this file with `write_file` after prototyping the
# job in an `eval` script ("save that workflow as tech_radar_live"). The `.js` is
# the source of truth — editable, version-controllable. `SavedWorkflowStore.save`
# emits the identical canonical format. The body gets `inp`, calls the role-
# filtered tool surface as camelCase `tools.webSearch(...)`, narrates progress
# with `tools.phase`/`tools.log`, and returns via `tools.output({result})`.
# ---------------------------------------------------------------------------

_TECH_RADAR_LIVE_JS = """\
const topics = (inp && inp.topics) || ["LangGraph", "AI agents", "Model Context Protocol"];
const perTopic = (inp && inp.per_topic) || 4;

tools.phase({ name: "scan" });
const sections = [];
for (const topic of topics) {
  tools.log({ message: "searching " + topic });
  const res = await tools.webSearch({ query: topic + " latest news", n: perTopic + 3 });
  const hits = Array.isArray(res) ? res : [];
  const seen = {};
  const lines = [];
  for (const hit of hits) {
    let host = "";
    try { host = (hit.url || "").split("/")[2] || ""; } catch (e) { host = ""; }
    host = host.replace(/^www\\./, "");
    if (!host || seen[host]) continue;
    seen[host] = true;
    lines.push("- [" + (hit.title || "untitled") + "](" + (hit.url || "") + ")");
    if (lines.length >= perTopic) break;
  }
  sections.push("## " + topic + "\\n" + (lines.join("\\n") || "_no results_"));
}

tools.phase({ name: "synthesize" });
await tools.output({ result: "# Tech radar (live)\\n\\n" + sections.join("\\n\\n") });
"""


def _seed_saved_workflow() -> None:
    """Write workflows/tech_radar_live.js if it isn't there yet (idempotent)."""
    store = SavedWorkflowStore(config.agents.workflows_dir)
    if not (store.directory / "tech_radar_live.js").exists():
        store.save(
            "tech_radar_live",
            script=_TECH_RADAR_LIVE_JS,
            description=(
                "Live tech radar: one web_search per topic, deduped by domain, into "
                "a markdown brief. Schedulable on cron with zero LLM cost."
            ),
            uses_tools=["web_search"],
        )


if __name__ == "__main__":
    _seed_saved_workflow()
    app.run()
