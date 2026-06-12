"""
Workflow-pattern cookbook — one non-trivial ``@app.workflow`` per orchestration
pattern from Claude Code's dynamic-workflows post, built on langclaw primitives.

https://claude.com/blog/a-harness-for-every-task-dynamic-workflows-in-claude-code

Each module exposes ``register(app)`` (add its reasoners + workflow) and runs
standalone via ``python -m examples.workflow_patterns.<module>``. ``register_all``
mounts all six on one app — handy for driving the whole set through one gateway:

    uv run python -m examples.workflow_patterns          # runs register_all
    uv run langclaw probe '/workflows'                   # lists all six
"""

from __future__ import annotations

from examples.workflow_patterns import (
    adversarial_verify,
    classify_and_act,
    fan_out_synthesize,
    generate_and_filter,
    loop_until_done,
    tournament,
)

#: (pattern label, workflow name, module) — the cookbook index.
PATTERNS = [
    ("classify-and-act", "triage", classify_and_act),
    ("fan-out-and-synthesize", "landscape", fan_out_synthesize),
    ("adversarial-verification", "fact_check", adversarial_verify),
    ("generate-and-filter", "tagline_studio", generate_and_filter),
    ("tournament", "prioritize", tournament),
    ("loop-until-done", "edge_hunt", loop_until_done),
]


def register_all(app):
    """Register every pattern workflow on *app*."""
    for _, _, module in PATTERNS:
        module.register(app)
    return app
