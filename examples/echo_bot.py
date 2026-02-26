"""
Echo Bot — the simplest possible langclaw app.

Demonstrates
------------
- ``Langclaw()`` constructor with a ``system_prompt``
- ``@app.tool()`` decorator
- ``app.run()``

Run
---
1. Copy ``.env.example`` to ``.env`` and fill in at least one LLM provider key
   and one channel token (Telegram, Discord, or WebSocket).
2. ``pip install langclaw[telegram]``  (or whichever channel you prefer)
3. ``python examples/echo_bot.py``
"""

from __future__ import annotations

from langclaw import Langclaw

app = Langclaw(
    system_prompt="You are a friendly assistant. Keep answers short and helpful.",
)


@app.tool()
async def reverse_text(text: str) -> str:
    """Reverse the given text. Useful for word games and puzzles.

    Args:
        text: The string to reverse.
    """
    return text[::-1]


if __name__ == "__main__":
    app.run()
