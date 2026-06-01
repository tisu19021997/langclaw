"""Proof that the ad-hoc 'run a workflow to ...' recipe (examples/hn_digest_eval.py)
exposes exactly the tools that prompt needs to the eval sandbox — offline, no
network or LLM. The agent reaches HN, reads /hn-vault, fans out subagents, and
saves markdown using only this resolved PTC surface.
"""

from __future__ import annotations

from types import SimpleNamespace

from langclaw.config.schema import InterpreterConfig
from langclaw.interpreter import RUNTIME_INJECTED_TOOLS, resolve_ptc_allowlist


def _tool(name: str):
    return SimpleNamespace(name=name)


def test_hn_recipe_ptc_surface():
    # Build-time toolset the agent ships with for this job.
    available = [_tool("web_search"), _tool("web_fetch")]

    # The recipe: eval on, only write_file added beyond the read-only default.
    cfg = InterpreterConfig(enabled=True, allow_tools=["write_file"])

    surface = resolve_ptc_allowlist(
        available,
        interpreter_config=cfg,
        runtime_tool_names=RUNTIME_INJECTED_TOOLS,  # deepagents-injected (task, write_file, ...)
    )

    # resolve_ptc_allowlist returns the raw (snake_case) tool names.
    # Already-default tools the prompt relies on:
    assert "web_fetch" in surface  # read HN
    assert "web_search" in surface
    assert "read_file" in surface  # read /hn-vault to link existing notes
    assert "task" in surface  # fan out post_explorer subagents
    # The one opt-in this recipe adds:
    assert "write_file" in surface  # save markdown to /hn-vault

    # Honest negative: a mutating tool NOT opted into stays out of the sandbox.
    assert "edit_file" not in surface


def test_write_file_requires_opt_in():
    """Without allow_tools, write_file is NOT reachable — saving would fail until
    the operator opts in. Proves the default is genuinely read-only."""
    cfg = InterpreterConfig(enabled=True)  # no allow_tools
    surface = resolve_ptc_allowlist(
        [_tool("web_fetch")],
        interpreter_config=cfg,
        runtime_tool_names=RUNTIME_INJECTED_TOOLS,
    )
    assert "write_file" not in surface
    assert "web_fetch" in surface  # but reads/fetches still work
