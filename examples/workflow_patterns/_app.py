"""
Shared harness for the workflow-pattern cookbook.

Each pattern file (``classify_and_act.py``, ``adversarial_verify.py``, …) is a real
``@app.workflow`` you can run on its own or drive through the probe. They all share
three things, kept here so the pattern files stay about the *pattern*:

1. ``make_app()`` — a WebSocket-only Langclaw app with workflows on and the
   outward-facing channels forced off (so an example never hijacks a real bot the
   ambient environment has configured).

2. ``reasoner()`` — register a focused, single-shot, model-backed tool. A workflow
   body reaches LLM judgment with ``ctx.tool("<name>", prompt=...)``; each call is a
   *fresh* model context (no history bleed), which is exactly the isolation the
   dynamic-workflow patterns rely on to fight goal-drift and self-preferential bias.

3. Tolerant parsers (``pick_label`` / ``parse_score`` / ``parse_winner`` /
   ``split_items``) — real models don't always honour "reply with only X", so the
   control flow degrades gracefully instead of crashing.

Honest boundary
---------------
A registered ``@app.workflow`` orchestrates **tools** (``ctx.tool`` / ``ctx.parallel``)
and arbitrary Python — not deepagents subagents. ``ctx.subagent`` exists on the API
but is currently inert for registered workflows (the ``task`` delegation tool isn't
in the workflow step executor's toolset). The model-backed tool here is the working
way to put isolated LLM judgment inside a durable, schedulable workflow today. Full
subagent fan-out *does* work in the ad-hoc ``eval`` interpreter path
(``tools.task({subagent_type})``) — see ``examples/hn_digest_eval.py``.

Run one pattern:
    uv run python -m examples.workflow_patterns.tournament
    uv run langclaw probe '/workflows run prioritize {"items": ["A","B"], "criterion": "impact"}'
"""

from __future__ import annotations

import re
from typing import Any

from langchain.chat_models import init_chat_model

from langclaw import Langclaw
from langclaw.config.schema import LangclawConfig


def make_app(system_prompt: str = "") -> Langclaw:
    """A safe, WebSocket-only app with the workflow primitive enabled."""
    config = LangclawConfig()
    config.channels.websocket.enabled = True
    config.channels.telegram.enabled = False
    config.channels.discord.enabled = False
    config.channels.matrix.enabled = False
    config.workflows.enabled = True
    # Keyless-of-LLM web search: Brave if a key is configured, else DuckDuckGo.
    config.tools.search_backend = "brave" if config.tools.brave_api_key else "duckduckgo"
    return Langclaw(config=config, system_prompt=system_prompt)


def reasoner(app: Langclaw, name: str, *, system: str, description: str):
    """Register a model-backed reasoning tool named *name* on *app*.

    The returned tool takes one ``prompt`` argument, runs a single isolated model
    call with the given *system* instruction, and returns ``{"text": ...}`` (or
    ``{"error": ...}`` — never raises into the workflow). Call it from a workflow
    body with ``await ctx.tool(name, prompt=...)``.
    """
    model = init_chat_model(app.config.agents.model, **app.config.agents.model_kwargs)

    async def _impl(prompt: str) -> dict:
        try:
            msg = await model.ainvoke([("system", system), ("user", prompt)])
            return {"text": (getattr(msg, "content", "") or "").strip()}
        except Exception as exc:  # noqa: BLE001 — surface as a tool error, never raise
            return {"error": str(exc)}

    # ``@app.tool`` derives the tool name from the function name, so stamp it.
    _impl.__name__ = name
    _impl.__doc__ = description
    return app.tool()(_impl)


def say(result: Any) -> str:
    """Pull the text out of a ``reasoner`` tool result (``{"text"|"error": ...}``)."""
    if isinstance(result, dict):
        return str(result.get("text") or result.get("error") or "").strip()
    return str(result).strip()


# --- tolerant parsers -------------------------------------------------------


def pick_label(text: str, choices: list[str], default: str = "") -> str:
    """Return the first *choice* that appears in *text* (case-insensitive)."""
    low = text.lower()
    for c in choices:
        if c.lower() in low:
            return c
    return default or (choices[0] if choices else "")


def parse_score(text: str, default: int = 5) -> int:
    """Extract a 0–10 score. Prefers ``SCORE: <n>``; falls back to the first int."""
    m = re.search(r"score\D{0,4}(\d{1,2})", text, re.IGNORECASE)
    if not m:
        m = re.search(r"\b(10|[0-9])\b", text)
    if not m:
        return default
    return max(0, min(10, int(m.group(1))))


def parse_winner(text: str) -> str | None:
    """Return ``'A'`` or ``'B'`` from a referee verdict, else ``None``."""
    m = re.search(r"winner\W{0,3}([ab])\b", text, re.IGNORECASE)
    if m:
        return m.group(1).upper()
    m = re.search(r"\boption\s+([ab])\b", text, re.IGNORECASE)
    return m.group(1).upper() if m else None


def split_items(text: str, *, limit: int = 50) -> list[str]:
    """Split a model list into clean items: strip bullets/numbering, drop blanks."""
    out: list[str] = []
    for raw in text.splitlines():
        line = re.sub(r"^\s*(?:[-*•]|\d+[.)])\s*", "", raw).strip()
        line = line.strip("\"'`").strip()
        if line and not line.lower().startswith(("here are", "sure", "okay", "none")):
            out.append(line)
        if len(out) >= limit:
            break
    return out


def norm(s: str) -> str:
    """Normalise an item for dedup (lowercase, collapse whitespace/punctuation)."""
    return re.sub(r"[^a-z0-9 ]", "", s.lower()).strip()
