"""Ad-hoc multi-step jobs via the `eval` interpreter (scripted RLM).

This is the langclaw recipe for the kind of freeform request that reads like
"Run a workflow to: <multi-step task>" — there is *no* pre-registered
`@app.workflow` for it; instead the agent writes a sandboxed JS program that
loops, branches, and fans out subagents over the live toolset.

Example prompt this app is built to handle (send it over your channel):

    Run a workflow to: read the top-50 HN news for today, find the top-5
    interesting posts about AI and another 3 random posts that are too cool to
    miss. For the top-5 AI posts: spawn subagents to explore what each post is
    about and what people are saying, save each as obsidian-friendly markdown
    (frontmatter tags, [[links]] to existing notes when possible). For the 3
    random posts: a one-paragraph read. Every post links to the origin. Save
    everything in /hn-vault.

What makes it run (the honest checklist):

1. `interpreter.enabled` — the agent gets the `eval` tool. The system-prompt
   nudge already tells it to use `eval` for real control flow / fan-out, so a
   "run a workflow to ..." prompt routes here.
2. The PTC allowlist. `web_search`, `web_fetch`, `read_file`, `ls`, `glob`,
   `grep`, and `task` are in langclaw's read-only default — so fetching HN,
   reading /hn-vault to link existing notes, and subagent fan-out work out of
   the box. The ONLY mutating tool this job needs is `write_file` (to save the
   markdown), so it is the one entry added to `allow_tools`.
3. `interpreter.timeout` — the default 5s budget *includes awaited subagent
   runs*. A 5-post fan-out of web research blows past that, so bump it.
4. A `post_explorer` subagent for the per-post deep dive (reachable via
   `tools.task({subagent_type: "post_explorer"})`).

Not a langclaw thing: the Claude Code "obsidian-markdown" *skill* is not loaded
here — langclaw has tools/subagents/middleware, not Claude Code plugin skills.
The obsidian formatting (frontmatter, tags, [[links]]) is instructed in the
subagent prompt instead.

Prereqs: `uv add 'langclaw[interpreter,search]'` and a model API key
(e.g. OPENAI_API_KEY). Paths are jailed to the agent workspace, so "/hn-vault"
lands under the configured workspace dir.
"""

from __future__ import annotations

from langclaw import Langclaw
from langclaw.config.schema import LangclawConfig

config = LangclawConfig()

# 1 + 2 + 3 — turn on eval, allow the one mutating tool (write_file), give the
# fan-out room to finish. Everything else this job needs is already in the
# read-only PTC default (web_search/web_fetch/read_file/ls/glob/grep/task).
config.interpreter.enabled = True
config.interpreter.allow_tools = ["write_file"]
config.interpreter.timeout = 600.0  # seconds, covers the whole subagent fan-out

# Turn on the workflow primitive too, so once the agent runs this job via eval you
# can say "save that workflow as hn_digest" — it persists the JS to
# <workspace>/workflows/hn_digest.js and it becomes a `workflow_hn_digest` tool you
# can re-run later (and after a restart). Needs both flags on (see save_workflow).
config.workflows.enabled = True

# Keyless web search so the example runs without a search-provider key
# (web_fetch needs no key at all). Swap to brave/tavily for better results.
config.tools.search_backend = "duckduckgo"

# A local WebSocket channel so you can drive it without a bot token.
config.channels.websocket.enabled = True

app = Langclaw(config=config)


# 4 — the per-post deep-dive subagent the script fans out to.
app.subagent(
    "post_explorer",
    description="Deep-dive one Hacker News post: what it's about + the discussion.",
    system_prompt=(
        "You research a single Hacker News post. Given its title and URL:\n"
        "1. Fetch the linked article and the HN comments.\n"
        "2. Summarize what the post is about and the gist of the discussion.\n"
        "3. Return obsidian-friendly markdown: YAML frontmatter with `tags:` "
        "(topic tags), a short body, `[[wiki-links]]` to related concepts, and a "
        "link to the origin HN post. Keep it tight.\n"
        "Do not invent facts or URLs."
    ),
    tools=["web_fetch", "web_search"],
)


if __name__ == "__main__":
    # Send the prompt in the module docstring over the WebSocket channel; the
    # agent will reach for `eval` and orchestrate the fan-out itself.
    app.run()
