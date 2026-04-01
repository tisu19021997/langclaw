"""
The agent I used for OpenClaw VN Meetup.
OpenClaw Meetup Q&A Bot — answers audience questions about your OpenClaw talk.

Demonstrates
------------
- ``Langclaw()`` with a long ``system_prompt`` (transcript embedded)
- ``@app.tool()`` — custom tool that calls the Telegram Bot API directly
- Built-in ``web_search`` via DuckDuckGo (no API key needed)
- Lifecycle hooks for shared HTTP client

Run
---
1. Set environment variables::

       export ANTHROPIC_API_KEY="sk-ant-..."
       export LANGCLAW__CHANNELS__TELEGRAM__ENABLED=true
       export LANGCLAW__CHANNELS__TELEGRAM__TOKEN="<bot-token-from-botfather>"
       export LANGCLAW__TOOLS__SEARCH_BACKEND=duckduckgo
       export OPENCLAW_OWNER_CHAT_ID="<your-numeric-telegram-id>"

   To find your numeric Telegram ID, message @userinfobot on Telegram.

2. ``pip install langclaw[telegram]``
3. ``python examples/openclaw_qa.py``
"""

from __future__ import annotations

import os

import httpx
from langchain.tools import ToolRuntime
from loguru import logger

from langclaw import Langclaw
from langclaw.context import LangclawContext

# ---------------------------------------------------------------------------
# Transcript of your 10-minute OpenClaw talk (replace with your actual text)
# ---------------------------------------------------------------------------

TRANSCRIPT = """\
## Introduction

Speaker: Quang (friendly name: tisu), AI engineer. Builds agents professionally \
and as hobby projects. Creator of open-source projects langclaw and openhay. \
First time speaking to a large audience. This session is "the map before the hike" \
— other speakers will cover hands-on usage afterward.

## What is OpenClaw?

**OpenClaw is an operating system for AI agents.** It provides infrastructure to \
build, use, control, and refine personal agents that run 24/7 on your machine. \
Agents communicate through a central gateway across any chat channel — Discord, \
Slack, even Zalo.

## Core Concepts: LLMs and Agents

- **LLM** (GPT, Claude, Gemini): Generates text. You give it prompts, it gives \
words back.
- **Agent = LLM + Tools**: The LLM is the brain, tools are the hands. Give an LLM \
tools (Google Search API, calendar, weather) and it becomes an agent that can \
complete tasks.

## OpenClaw Agents

OpenClaw agents follow the same Agent = LLM + Tools formula, with key differences:

- **No LLM lock-in**: Use any LLM — just provide API keys. Not locked to ChatGPT \
or Claude.
- **Prompt-driven identity**: Prompts control personality, knowledge, and behavior. \
Loaded at session start, editable by the agent during the session.
- **Skills & Plugins**: Teach the agent specific tasks at different levels. Skills \
are surface-level, plugins are fundamental. Powerful built-ins include file \
interaction, parallel subagent spawning, and direct command execution.
- **ClawHub.ai**: Marketplace for community skills and plugins.

**What makes OpenClaw special?** Two things: **always-on** and **self-improvement**.

## Always-On: 3 Types of Schedulers

Unlike ChatGPT (agent dies when you close the browser), OpenClaw agents wake up \
on their own:

1. **Cron jobs**: Classic scheduling. Example: "Every morning at 7am, read my \
emails and create a todo list." The list is ready when you wake up.
2. **Heartbeat**: Agent wakes every 30–60 minutes to check its todo list and \
current conversations for pending tasks.
3. **Webhooks**: External events trigger the agent. Example: Gmail webhook wakes \
the agent when your boss emails you while you're WFH.

OpenClaw can also **create its own schedules and heartbeat tasks** autonomously.

## Self-Improvement: Memory System

Memory is stored as **markdown files** (text files with formatting):

- **MEMORY.md**: Durable facts (e.g., "you have 3 dogs", "you prefer com tam \
over banh mi").
- **Daily log files**: High-level summaries of discussions, topics, and questions. \
Generated at a configurable time.
- **Memory flush**: When a session ends or the context window limit is reached, \
the agent saves important facts to memory and less important ones to daily logs.

Result: A colleague that works 24/7 **and** remembers your things.

## Self-Evolution

Because everything is files and the agent has filesystem access:

- **SOUL.md**: Defines personality, tone, vibes. The agent can rewrite it.
- **Skill creation**: Tell it "Create a skill for managing Trello" — it writes a \
SKILL.md, teaches itself the Trello API. Three minutes later, it knows Trello.
- **Adaptive behavior**: Tell it to be more concise, or it decides on its own to \
be more proactive based on how you work. No locked configuration, no re-onboarding.

"This isn't science fiction. It's a language model editing text files. Files, not \
magic — but it works really well."

## Before You Go Live: Risks & Tips

1. **Limited context window**: Be mindful when adding skills/plugins. Start a new \
session for every task.
2. **LLM cost**: The software is free, but LLM API calls cost money. Set monthly \
spend limits. Consider local models like Qwen if possible.
3. **Onboarding**: Treat it like a new employee:
   - Give OpenClaw its own computer (run in a sandbox or VPS, not your personal \
machine).
   - Give it its own email, workspace, and API keys. Forward emails/calendar to it.
   - Be patient — work with it, let it learn about you over time. It will grow.
"""

# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = Langclaw(
    system_prompt=(
        "## OpenClaw Meetup Q&A Assistant\n\n"
        "You are a Q&A bot for an OpenClaw meetup talk. Your primary source "
        "of truth is the speaker's transcript below. Answer questions about "
        "OpenClaw based on this transcript first.\n\n"
        "### Rules\n"
        "1. **Transcript first** — always check the transcript before "
        "searching the web.\n"
        "2. **Web search** — use `web_search` when the transcript lacks "
        "sufficient detail or the question is about something not covered "
        "in the talk.\n"
        "3. **Notify the speaker** — call `notify_owner` when you are "
        "approximately 70% unsure about your answer. It is better to "
        "over-notify than to give a wrong answer. After calling "
        "`notify_owner`, tell the user that you don't have the answer from the transcription and "
        "have notified the speaker, tisu will get back to you soon."
        "Do NOT attempt to answer the "
        "question yourself in that case.\n"
        "4. **Be concise** — this is a live meetup. Keep answers short and "
        "to the point (2-4 sentences unless detail is requested).\n"
        "5. **Stay on topic** — politely redirect off-topic questions back "
        "to OpenClaw or the talk.\n\n"
        f"### Speaker's Transcript\n\n{TRANSCRIPT}\n\n"
        "Slide: https://docs.google.com/presentation/d/1AO08XUSHCeQOu_q5tu5UQ7TYyMXagjiJFVATTWdHPt8/edit?usp=sharing\n"
        "Made with langclaw."
    ),
)

# ---------------------------------------------------------------------------
# Shared HTTP client
# ---------------------------------------------------------------------------

_http_client: httpx.AsyncClient | None = None


@app.on_startup
async def _open_http():
    global _http_client
    _http_client = httpx.AsyncClient(timeout=10)
    logger.info("HTTP client ready")


@app.on_shutdown
async def _close_http():
    global _http_client
    if _http_client:
        await _http_client.aclose()
        _http_client = None
    logger.info("HTTP client closed")


# ---------------------------------------------------------------------------
# Custom tool — notify the speaker via Telegram DM
# ---------------------------------------------------------------------------

BOT_TOKEN = os.environ.get("LANGCLAW__CHANNELS__TELEGRAM__TOKEN", "")
OWNER_CHAT_ID = os.environ.get("OPENCLAW_OWNER_CHAT_ID", "")


@app.tool()
async def notify_owner(
    question_summary: str,
    *,
    runtime: ToolRuntime[LangclawContext],
) -> dict:
    """Notify the speaker when you are unsure about an answer (~70% uncertain).

    Call this BEFORE answering so the speaker can follow up with the user
    directly. The speaker will receive a Telegram DM with the question
    and who asked it. The user's identity is extracted automatically.

    Args:
        question_summary: A concise summary of the question you are unsure about.
    """
    if not BOT_TOKEN or not OWNER_CHAT_ID:
        return {"error": "LANGCLAW__CHANNELS__TELEGRAM__TOKEN or OPENCLAW_OWNER_CHAT_ID not set."}

    assert _http_client is not None, "HTTP client not initialised"

    ctx = runtime.context
    user_id = ctx.user_id if ctx else "unknown"
    username = ctx.metadata.get("username", "") if ctx else ""
    user_label = f"@{username}" if username else user_id

    text = (
        f"📩 Uncertain Q&A at the meetup\n\n"
        f"Question: {question_summary}\n"
        f"Asked by: {user_label} (ID: {user_id})\n\n"
        f"You may want to DM them directly."
    )

    try:
        resp = await _http_client.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={"chat_id": OWNER_CHAT_ID, "text": text},
        )
        resp.raise_for_status()
        logger.info(f"Owner notified about question from {username}")
        return {"status": "Speaker has been notified and may follow up with you."}
    except httpx.HTTPError as exc:
        logger.error(f"Failed to notify owner: {exc}")
        return {"error": f"Could not notify speaker: {exc}"}


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    app.run()
