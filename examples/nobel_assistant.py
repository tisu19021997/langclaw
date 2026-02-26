"""
Nobel Prize Bot — an agent that answers Nobel Prize questions and
schedules recurring trivia via cron.

Demonstrates
------------
- ``@app.tool()``    — custom tool calling the Nobel Prize REST API.
- **Cron scheduling** — the built-in ``cron`` tool lets users say
  "send me a Nobel Prize fact every morning at 9" and the LLM
  translates that into a cron expression automatically.
- ``@app.command()`` — ``/nobel`` instant command (no LLM).
- ``@app.command()`` — ``/jobs`` lists active cron jobs (no LLM).

Nobel Prize API
---------------
Free, public, no API key required.

  Base URL: ``https://api.nobelprize.org/v1``

  Endpoints::

    GET /prize.json                           — all prizes
    GET /prize.json?year=2024                 — prizes for a year
    GET /prize.json?category=physics          — prizes in a category
    GET /prize.json?category=medicine&year=1990&yearto=1994
    GET /laureate.json?firstname=Albert       — search laureates
    GET /laureate.json?bornCountryCode=US     — by birth country (ISO)
    GET /country.json                         — list of country codes

  Categories: ``physics``, ``chemistry``, ``medicine``,
  ``literature``, ``peace``, ``economics``.

  Full docs: https://www.nobelprize.org/about/developer-zone-2/

How cron works
--------------
When the user asks the agent to schedule something, the agent calls the
built-in ``cron`` tool with ``action='add'``.  On fire, the scheduled
``message`` is injected into the agent pipeline as a new prompt — the
agent wakes up, calls tools (including ``nobel_prizes``), and sends the
result to the user.

Run
---
1. Copy ``.env.example`` to ``.env`` and fill in at least one LLM provider
   key and one channel token.
2. ``pip install langclaw[telegram]``  (or whichever channel you prefer)
3. ``python examples/cron_reporter.py``
4. Try:
   - "Who won the Nobel Prize in Physics in 2024?"
   - "List all medicine laureates from 1990 to 1994"
   - "Send me a random Nobel Prize fact every morning at 9"
   - "Schedule a daily Nobel trivia at 8 PM"
   - ``/nobel 2023 chemistry``
   - ``/jobs``
"""

from __future__ import annotations

import httpx
from loguru import logger

from langclaw import Langclaw
from langclaw.config.schema import load_config
from langclaw.gateway.commands import CommandContext

NOBEL_BASE = "https://api.nobelprize.org/v1"

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

base_config = load_config()
base_config.cron.enabled = True
base_config.cron.timezone = "Europe/Amsterdam"

app = Langclaw(
    config=base_config,
    system_prompt=(
        "## Nobel Prize Assistant\n"
        "You are a knowledgeable assistant specializing in the Nobel Prize.\n\n"
        "You have access to the `nobel_prizes` tool which queries the official "
        "Nobel Prize API. Use it to answer questions about laureates, prizes, "
        "categories, and years.\n\n"
        "When users ask you to schedule Nobel trivia or daily facts, use the "
        "`cron` tool. Write the cron message as an instruction to yourself:\n"
        '  - Good: "Look up a random Nobel Prize from the last 10 years '
        'using the nobel_prizes tool and share one interesting fact."\n'
        '  - Bad: "Nobel trivia" (too vague for you to act on at fire time)\n\n'
        "Keep answers concise. Include the year, category, and laureate names."
    ),
)


# ---------------------------------------------------------------------------
# Custom tool — Nobel Prize API
# ---------------------------------------------------------------------------


@app.tool()
async def nobel_prizes(
    year: int | None = None,
    year_to: int | None = None,
    category: str | None = None,
) -> dict | list[dict]:
    """Query the Nobel Prize API for prizes and laureates.

    Args:
        year: Filter by year (e.g. 2024). Use with year_to for a range.
        year_to: End year for a range query (requires year).
        category: Filter by category. One of: physics, chemistry,
            medicine, literature, peace, economics.
    """
    params: dict[str, str] = {}
    if year is not None:
        params["year"] = str(year)
    if year_to is not None:
        params["yearto"] = str(year_to)
    if category is not None:
        params["category"] = category.lower()

    url = f"{NOBEL_BASE}/prize.json"
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(url, params=params)

    if resp.status_code != 200:
        return {"error": f"Nobel API returned {resp.status_code}"}

    data = resp.json()
    prizes = data.get("prizes", [])
    if not prizes:
        return {"error": "No prizes found for the given criteria."}

    results = []
    for prize in prizes[:20]:
        entry: dict = {
            "year": prize.get("year"),
            "category": prize.get("category"),
        }
        laureates = prize.get("laureates", [])
        entry["laureates"] = [
            {
                "name": f"{lr.get('firstname', '')} {lr.get('surname', '')}".strip(),
                "motivation": lr.get("motivation", ""),
            }
            for lr in laureates
        ]
        results.append(entry)

    return results


# ---------------------------------------------------------------------------
# Command: quick Nobel lookup (no LLM)
# ---------------------------------------------------------------------------


@app.command("nobel", description="quick Nobel lookup: /nobel [year] [category]")
async def nobel_cmd(ctx: CommandContext) -> str:
    year = None
    category = None
    categories = ("physics", "chemistry", "medicine", "literature", "peace", "economics")
    for arg in ctx.args:
        if arg.isdigit() and len(arg) == 4:
            year = arg
        elif arg.lower() in categories:
            category = arg.lower()

    if not year and not category:
        return (
            "Usage: /nobel [year] [category]\n"
            "  /nobel 2024\n"
            "  /nobel 2024 physics\n"
            "  /nobel physics\n\n"
            "Categories: physics, chemistry, medicine, literature, peace, economics"
        )

    params: dict[str, str] = {}
    if year:
        params["year"] = year
    if category:
        params["category"] = category

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"{NOBEL_BASE}/prize.json", params=params)
        data = resp.json()
    except Exception as exc:
        logger.debug("Nobel API error: {}", exc)
        return f"API error: {exc}"

    prizes = data.get("prizes", [])
    if not prizes:
        return "No prizes found."

    lines = []
    for prize in prizes[:10]:
        header = f"{prize['category'].title()} {prize['year']}"
        names = [
            f"{lr.get('firstname', '')} {lr.get('surname', '')}".strip()
            for lr in prize.get("laureates", [])
        ]
        lines.append(f"{header}: {', '.join(names)}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Command: list active cron jobs without the LLM
# ---------------------------------------------------------------------------


@app.command("jobs", description="list all active scheduled jobs")
async def jobs_cmd(ctx: CommandContext) -> str:
    return 'Use the agent to manage cron jobs:\n  "List my scheduled jobs"\n  "Remove job <id>"'


# ---------------------------------------------------------------------------
# RBAC
# ---------------------------------------------------------------------------

app.role("admin", tools=["*"])
app.role("viewer", tools=["nobel_prizes", "web_search"])

# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    app.run()
