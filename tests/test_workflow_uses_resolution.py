"""A workflow's ``@uses`` must resolve to a live, langclaw-registered tool.

The deepagents *backend* file tools (``read_file`` / ``write_file`` / ``ls`` /
``glob`` / ``grep`` / ``edit_file``) are injected inside ``create_deep_agent``
and require an injected LangGraph ``ToolRuntime`` the workflow PTC bridge cannot
supply — so they are NOT reachable from a saved/authored workflow. Declaring one
used to fail deep in the QuickJS sandbox with ``TypeError: not a function``; it
now fails fast at run-start with a clear, named error.
"""

from __future__ import annotations

import pytest
from langchain_core.tools import tool

from langclaw.workflows import resolve_workflow_tools, unresolved_workflow_tools


@tool
def web_fetch(url: str) -> str:
    """Fetch a URL."""
    return url


def test_unresolved_flags_backend_file_tool() -> None:
    assert unresolved_workflow_tools([web_fetch], ["web_fetch", "read_file"]) == ["read_file"]


def test_unresolved_matches_camelcase_sandbox_surface() -> None:
    # `webFetch` (the camelCase sandbox surface) resolves to the snake_case tool.
    assert unresolved_workflow_tools([web_fetch], ["webFetch"]) == []


def test_unresolved_empty_when_nothing_declared() -> None:
    assert unresolved_workflow_tools([web_fetch], None) == []


def test_resolve_returns_selected_subset_preserving_order() -> None:
    assert resolve_workflow_tools([web_fetch], ["web_fetch"], workflow_name="ok") == [web_fetch]


def test_resolve_raises_clear_error_for_backend_file_tool() -> None:
    with pytest.raises(ValueError) as exc_info:
        resolve_workflow_tools([web_fetch], ["read_file"], workflow_name="hn_digest")

    message = str(exc_info.value)
    assert "hn_digest" in message  # names the workflow
    assert "read_file" in message  # names the offending tool
    assert "backend file tools" in message.lower()  # the specific, actionable hint
    assert "not a function" not in message.lower()  # no longer the cryptic failure


def test_resolve_raises_for_unknown_tool() -> None:
    with pytest.raises(ValueError) as exc_info:
        resolve_workflow_tools([web_fetch], ["definitely_not_a_tool"], workflow_name="w")
    assert "definitely_not_a_tool" in str(exc_info.value)
