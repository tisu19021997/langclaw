"""
web_search backends must return an error dict on a runtime failure (e.g. an
HTTP 429 from the search API) rather than raising — otherwise the exception
propagates into the agent/workflow and kills the step. This is the langclaw
tool convention: tools return ``{"error": "..."}``, never raise into the agent.
"""

from __future__ import annotations

import re
import sys
import tomllib
from pathlib import Path

import pytest


def _search_extra_requirements() -> list[str]:
    root = Path(__file__).resolve().parents[1]
    data = tomllib.loads((root / "pyproject.toml").read_text())
    return data["project"]["optional-dependencies"]["search"]


def _req_name(requirement: str) -> str:
    return re.split(r"[<>=!~ \[]", requirement.strip(), maxsplit=1)[0].lower()


def test_search_extra_pins_ddgs_not_legacy_duckduckgo() -> None:
    # langchain_community's DuckDuckGoSearchResults now imports the renamed `ddgs`
    # package; the legacy `duckduckgo-search` breaks the keyless backend at call
    # time ("Could not import ddgs python package").
    names = {_req_name(r) for r in _search_extra_requirements()}
    assert "ddgs" in names
    assert "duckduckgo-search" not in names


async def test_duckduckgo_import_error_hint_points_at_ddgs(monkeypatch) -> None:
    # When the backend package is missing, the actionable hint must name `ddgs`,
    # not the legacy `duckduckgo-search`.
    monkeypatch.setitem(sys.modules, "langchain_community.tools", None)

    from langclaw.agents.tools.web_search import _make_duckduckgo_tool

    tool = _make_duckduckgo_tool()
    with pytest.raises(ImportError) as exc_info:
        await tool.ainvoke({"query": "test"})

    message = str(exc_info.value)
    assert "ddgs" in message
    assert "duckduckgo-search" not in message


async def test_brave_search_returns_error_dict_on_failure(monkeypatch):
    import langchain_community.document_loaders as dl

    class _Boom:
        def __init__(self, **kwargs):
            pass

        def load(self):
            raise Exception("HTTP error 429")

    monkeypatch.setattr(dl, "BraveSearchLoader", _Boom)

    from langclaw.agents.tools.web_search import _make_brave_tool

    tool = _make_brave_tool("fake-key")
    out = await tool.ainvoke({"query": "test"})

    assert isinstance(out, dict)
    assert "error" in out
    assert "429" in out["error"]


async def test_duckduckgo_search_returns_error_dict_on_failure(monkeypatch):
    import langchain_community.tools as community_tools

    class _Boom:
        def __init__(self, **kwargs):
            pass

        def invoke(self, query):
            raise Exception("rate limited")

    monkeypatch.setattr(community_tools, "DuckDuckGoSearchResults", _Boom)

    from langclaw.agents.tools.web_search import _make_duckduckgo_tool

    tool = _make_duckduckgo_tool()
    out = await tool.ainvoke({"query": "test"})

    assert isinstance(out, dict)
    assert "error" in out
