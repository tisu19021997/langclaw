"""
web_search backends must return an error dict on a runtime failure (e.g. an
HTTP 429 from the search API) rather than raising — otherwise the exception
propagates into the agent/workflow and kills the step. This is the langclaw
tool convention: tools return ``{"error": "..."}``, never raise into the agent.
"""

from __future__ import annotations


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
