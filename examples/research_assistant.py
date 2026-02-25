"""
Research Assistant — a multi-channel bot that looks up stock prices,
searches the web, and can schedule daily digest reports via cron.

Demonstrates
------------
- ``@app.tool()``     — custom async tool (stock price via httpx)
- ``@app.command()``  — fast command that bypasses the LLM
- ``app.role()``      — RBAC: analysts get every tool, free users get web_search only
- lifecycle hooks     — ``@app.on_startup`` / ``@app.on_shutdown``
- cron-ready          — users can ask the agent to schedule recurring reports

Run
---
1. Copy ``.env.example`` to ``.env`` and fill in at least one LLM provider key
   and one channel token (Telegram or Discord).
2. ``pip install langclaw[telegram]``   (or ``langclaw[discord]``, etc.)
3. ``python examples/research_assistant.py``
"""

from __future__ import annotations

import httpx
from loguru import logger

from langclaw import Langclaw
from langclaw.gateway.commands import CommandContext

app = Langclaw()

# ---------------------------------------------------------------------------
# Shared HTTP client (created once, closed on shutdown)
# ---------------------------------------------------------------------------

_http_client: httpx.AsyncClient | None = None


@app.on_startup
async def _open_http():
    global _http_client
    _http_client = httpx.AsyncClient(
        timeout=10,
        headers={"User-Agent": "Mozilla/5.0 (compatible; langclaw/0.1)"},
    )
    logger.info("HTTP client ready")


@app.on_shutdown
async def _close_http():
    global _http_client
    if _http_client:
        await _http_client.aclose()
        _http_client = None
    logger.info("HTTP client closed")


# ---------------------------------------------------------------------------
# Custom tool — stock price lookup
# ---------------------------------------------------------------------------


@app.tool()
async def get_stock_price(ticker: str) -> dict:
    """Fetch the latest quote for a US stock ticker.

    Args:
        ticker: Stock symbol, e.g. ``"AAPL"``, ``"MSFT"``, ``"TSLA"``.

    Returns:
        A dict with ``ticker``, ``price``, ``change``, and ``change_pct``.
    """
    assert _http_client is not None, "HTTP client not initialised"
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
    params = {"interval": "1d", "range": "1d"}
    resp = await _http_client.get(url, params=params)
    if resp.status_code == 429:
        return {
            "ticker": ticker.upper(),
            "error": "Rate-limited by Yahoo Finance. Try again shortly.",
        }
    resp.raise_for_status()

    data = resp.json()
    result = data.get("chart", {}).get("result")
    if not result:
        return {"ticker": ticker.upper(), "error": "No data returned for this ticker."}

    meta = result[0].get("meta", {})
    price = meta.get("regularMarketPrice") or meta.get("previousClose")
    prev_close = meta.get("chartPreviousClose") or meta.get("previousClose")
    if price is None:
        return {
            "ticker": ticker.upper(),
            "error": f"Price unavailable (type: {meta.get('instrumentType', 'unknown')}).",
        }

    change = round(price - prev_close, 2) if prev_close else 0.0
    pct = round((change / prev_close) * 100, 2) if prev_close else 0.0

    return {
        "ticker": ticker.upper(),
        "price": price,
        "change": change,
        "change_pct": f"{pct:+.2f}%",
    }


# ---------------------------------------------------------------------------
# Custom command — quick portfolio snapshot (no LLM needed)
# ---------------------------------------------------------------------------

WATCHLIST = ["AAPL", "MSFT", "GOOGL", "NVDA"]


@app.command("watchlist", description="show watchlist prices (no AI)")
async def watchlist_cmd(ctx: CommandContext) -> str:
    assert _http_client is not None
    lines = ["Watchlist:"]
    for ticker in WATCHLIST:
        try:
            url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
            resp = await _http_client.get(url, params={"interval": "1d", "range": "1d"})
            meta = resp.json()["chart"]["result"][0]["meta"]
            price = meta["regularMarketPrice"]
            lines.append(f"  {ticker}: ${price:.2f}")
        except Exception:
            lines.append(f"  {ticker}: unavailable")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# RBAC — analysts can use everything, free-tier users get web search only
# ---------------------------------------------------------------------------

app.role("analyst", tools=["*"])
app.role("free", tools=["web_search"])


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    app.run()
