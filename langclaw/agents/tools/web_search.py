"""Web search tool with pluggable backends.

Supported backends
------------------
``"brave"``
    Brave Search via ``langchain_community.document_loaders.BraveSearchLoader``.
    Requires ``api_key``.

``"tavily"``
    Tavily Search via ``langchain_community.retrievers.TavilySearchAPIRetriever``.
    Requires ``api_key`` and ``tavily-python`` package.

``"duckduckgo"``
    DuckDuckGo via ``langchain_community.tools.DuckDuckGoSearchResults``.
    No API key needed; requires ``duckduckgo-search`` package.
"""

from __future__ import annotations

import asyncio

from langchain_core.tools import BaseTool, tool
from loguru import logger

# ---------------------------------------------------------------------------
# Backend implementations
# ---------------------------------------------------------------------------


def _make_brave_tool(api_key: str) -> BaseTool:
    """Return a ``web_search`` tool backed by Brave Search."""

    @tool
    async def web_search(query: str, n: int = 5) -> list[dict] | dict:
        """Search the web for recent or breaking information.

        Args:
            query: A search-ready query that emphasises recency. Include
                terms like "latest", "news", dates, or time ranges.
            n: Number of results to return. Increase for complex topics.
        """
        try:
            from langchain_community.document_loaders import BraveSearchLoader
        except ImportError as exc:
            raise ImportError(
                "langchain-community is required. Install with: uv add langchain-community"
            ) from exc

        logger.debug('Brave search: "{}" (n={})', query, n)
        loader = BraveSearchLoader(
            query=query,
            api_key=api_key,
            search_kwargs={"count": n},
        )
        loop = asyncio.get_running_loop()
        try:
            docs = await loop.run_in_executor(None, loader.load)
        except Exception as exc:  # noqa: BLE001 - surface as a tool error, never raise into the agent
            logger.warning(f"Brave search failed: {exc}")
            return {"error": f"web_search failed: {exc}"}

        results = [
            {
                "title": doc.metadata.get("title", ""),
                "url": doc.metadata.get("link", ""),
                "content": doc.page_content,
            }
            for doc in docs
        ]
        logger.debug("Brave search returned {} results", len(results))
        return results

    return web_search


def _make_tavily_tool(api_key: str) -> BaseTool:
    """Return a ``web_search`` tool backed by ``TavilySearchAPIRetriever``.

    The retriever returns ``Document`` objects whose metadata contains
    ``title``, ``source``, and ``score`` fields; these are normalised into
    the same ``{title, url, content}`` shape used by the other backends.
    """

    @tool
    async def web_search(query: str, n: int = 5) -> list[dict] | dict:
        """Search the web for recent or breaking information.

        Args:
            query: A search-ready query that emphasises recency. Include
                terms like "latest", "news", dates, or time ranges.
            n: Number of results to return. Increase for complex topics.
        """
        try:
            from langchain_community.retrievers import TavilySearchAPIRetriever
        except ImportError as exc:
            raise ImportError(
                "langchain-community and tavily-python are required. "
                "Install with: uv add langchain-community tavily-python"
            ) from exc

        logger.debug('Tavily search: "{}" (n={})', query, n)
        retriever = TavilySearchAPIRetriever(
            k=n,
            tavily_api_key=api_key,
        )
        loop = asyncio.get_running_loop()
        try:
            docs = await loop.run_in_executor(None, retriever.invoke, query)
        except Exception as exc:  # noqa: BLE001 - surface as a tool error, never raise into the agent
            logger.warning(f"Tavily search failed: {exc}")
            return {"error": f"web_search failed: {exc}"}

        results = [
            {
                "title": doc.metadata.get("title", ""),
                "url": doc.metadata.get("source", ""),
                "content": doc.page_content,
            }
            for doc in docs
        ]
        logger.debug("Tavily search returned {} results", len(results))
        return results

    return web_search


def _make_duckduckgo_tool() -> BaseTool:
    """Return a ``web_search`` tool backed by ``DuckDuckGoSearchResults``.

    ``DuckDuckGoSearchResults`` returns a structured list of snippets (title,
    link, snippet) unlike ``DuckDuckGoSearchRun`` which returns a single
    concatenated string.
    """

    @tool
    async def web_search(query: str, n: int = 5) -> list[dict] | dict:
        """Search the web for recent or breaking information.

        Args:
            query: A search-ready query that emphasises recency. Include
                terms like "latest", "news", dates, or time ranges.
            n: Number of results to return. Increase for complex topics.
        """
        try:
            from langchain_community.tools import DuckDuckGoSearchResults
        except ImportError as exc:
            raise ImportError(
                "langchain-community and duckduckgo-search are required. "
                "Install with: uv add langchain-community duckduckgo-search"
            ) from exc

        logger.debug('DuckDuckGo search: "{}" (n={})', query, n)
        ddg = DuckDuckGoSearchResults(num_results=n, output_format="list")
        loop = asyncio.get_running_loop()
        try:
            raw = await loop.run_in_executor(None, ddg.invoke, query)
        except Exception as exc:  # noqa: BLE001 - surface as a tool error, never raise into the agent
            logger.warning(f"DuckDuckGo search failed: {exc}")
            return {"error": f"web_search failed: {exc}"}

        # raw is list[dict] with keys: snippet, title, link
        results = [
            {
                "title": item.get("title", ""),
                "url": item.get("link", ""),
                "content": item.get("snippet", ""),
            }
            for item in (raw if isinstance(raw, list) else [])
        ]
        logger.debug("DuckDuckGo search returned {} results", len(results))
        return results

    return web_search


# ---------------------------------------------------------------------------
# Public factory
# ---------------------------------------------------------------------------

_BACKENDS = ("brave", "tavily", "duckduckgo")


def make_web_search_tool(
    backend: str,
    *,
    api_key: str = "",
) -> BaseTool:
    """Return a ``web_search`` LangChain tool for the given backend.

    Args:
        backend: One of ``"brave"``, ``"tavily"``, or ``"duckduckgo"``.
        api_key: API key required by ``"brave"`` and ``"tavily"`` backends.
                 Ignored by ``"duckduckgo"``.

    Raises:
        ValueError: If *backend* is not recognised.
        ValueError: If *api_key* is empty for a backend that requires one.
    """
    if backend == "brave":
        if not api_key:
            raise ValueError("brave search backend requires tools.brave_api_key to be set.")
        return _make_brave_tool(api_key)

    if backend == "tavily":
        if not api_key:
            raise ValueError("tavily search backend requires tools.tavily_api_key to be set.")
        return _make_tavily_tool(api_key)

    if backend == "duckduckgo":
        return _make_duckduckgo_tool()

    raise ValueError(f"Unknown search backend: {backend!r}. Choose one of: {', '.join(_BACKENDS)}.")
