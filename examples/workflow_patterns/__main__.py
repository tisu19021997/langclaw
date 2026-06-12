"""Run all six pattern workflows on one WebSocket-only app.

uv run python -m examples.workflow_patterns
uv run langclaw probe '/workflows'
"""

from __future__ import annotations

from examples.workflow_patterns import register_all
from examples.workflow_patterns._app import make_app

if __name__ == "__main__":
    app = make_app(system_prompt="You can run any of the registered pattern workflows.")
    register_all(app)
    app.run()
