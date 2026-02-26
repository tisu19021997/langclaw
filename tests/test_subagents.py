"""
Tests for the subagent registration API and builder integration.

Phase 1: standard delegation (subagent -> main agent -> channel)
Phase 2: channel-routed subagents (subagent -> channel)
"""

from __future__ import annotations

import pytest

# ---------------------------------------------------------------------------
# Phase 1 — app.subagent() registration
# ---------------------------------------------------------------------------


class TestSubagentRegistration:
    """Verify that app.subagent() stores specs correctly."""

    def test_subagent_stored(self):
        from langclaw import Langclaw

        app = Langclaw()
        app.subagent(
            "researcher",
            description="Researches topics",
            system_prompt="You are a researcher.",
        )
        assert len(app._subagents) == 1
        assert app._subagents[0]["name"] == "researcher"
        assert app._subagents[0]["description"] == "Researches topics"
        assert app._subagents[0]["system_prompt"] == "You are a researcher."

    def test_subagent_defaults(self):
        from langclaw import Langclaw

        app = Langclaw()
        app.subagent(
            "helper",
            description="General helper",
            system_prompt="You help.",
        )
        spec = app._subagents[0]
        assert spec["tools"] is None
        assert spec["model"] is None
        assert spec["roles"] is None
        assert spec["output"] == "main_agent"

    def test_subagent_with_all_options(self):
        from langclaw import Langclaw

        app = Langclaw()
        app.subagent(
            "analyst",
            description="Financial analyst",
            system_prompt="Analyze data.",
            tools=["web_search", "web_fetch"],
            model="openai:gpt-4.1",
            roles=["premium"],
            output="main_agent",
        )
        spec = app._subagents[0]
        assert spec["tools"] == ["web_search", "web_fetch"]
        assert spec["model"] == "openai:gpt-4.1"
        assert spec["roles"] == ["premium"]

    def test_multiple_subagents(self):
        from langclaw import Langclaw

        app = Langclaw()
        app.subagent("a", description="A", system_prompt="A.")
        app.subagent("b", description="B", system_prompt="B.")
        app.subagent("c", description="C", system_prompt="C.")
        assert len(app._subagents) == 3
        assert [s["name"] for s in app._subagents] == ["a", "b", "c"]

    def test_invalid_output_raises(self):
        from langclaw import Langclaw

        app = Langclaw()
        with pytest.raises(ValueError, match="Invalid output mode"):
            app.subagent(
                "bad",
                description="Bad",
                system_prompt="Bad.",
                output="invalid",
            )


# ---------------------------------------------------------------------------
# Phase 1 — tool name resolution
# ---------------------------------------------------------------------------


class TestToolNameResolution:
    """Verify _resolve_tools_by_name handles edge cases."""

    def _make_mock_tool(self, name: str):
        """Create a minimal object with a .name attribute."""

        class _MockTool:
            pass

        t = _MockTool()
        t.name = name
        return t

    def test_none_returns_none(self):
        from langclaw.agents.builder import _resolve_tools_by_name

        assert _resolve_tools_by_name(None, []) is None

    def test_resolve_valid_names(self):
        from langclaw.agents.builder import _resolve_tools_by_name

        tools = [self._make_mock_tool("web_search"), self._make_mock_tool("cron")]
        resolved = _resolve_tools_by_name(["web_search"], tools)
        assert resolved is not None
        assert len(resolved) == 1
        assert resolved[0].name == "web_search"

    def test_resolve_multiple_names(self):
        from langclaw.agents.builder import _resolve_tools_by_name

        tools = [
            self._make_mock_tool("a"),
            self._make_mock_tool("b"),
            self._make_mock_tool("c"),
        ]
        resolved = _resolve_tools_by_name(["a", "c"], tools)
        assert resolved is not None
        assert [t.name for t in resolved] == ["a", "c"]

    def test_unknown_name_raises(self):
        from langclaw.agents.builder import _resolve_tools_by_name

        tools = [self._make_mock_tool("web_search")]
        with pytest.raises(ValueError, match="unknown tool 'nonexistent'"):
            _resolve_tools_by_name(["nonexistent"], tools)

    def test_empty_list_returns_empty(self):
        from langclaw.agents.builder import _resolve_tools_by_name

        tools = [self._make_mock_tool("web_search")]
        resolved = _resolve_tools_by_name([], tools)
        assert resolved == []


# ---------------------------------------------------------------------------
# Phase 1 — _build_deepagent_subagents middleware
# ---------------------------------------------------------------------------


class TestBuildDeepagentSubagents:
    """Verify per-subagent middleware stack construction."""

    def _make_mock_tool(self, name: str):
        class _MockTool:
            pass

        t = _MockTool()
        t.name = name
        return t

    def _make_config(self, permissions_enabled: bool = False):
        from langclaw.config.schema import LangclawConfig

        cfg = LangclawConfig()
        cfg.permissions.enabled = permissions_enabled
        if permissions_enabled:
            from langclaw.config.schema import RoleConfig

            cfg.permissions.roles = {"viewer": RoleConfig(tools=["web_search"])}
        return cfg

    def test_basic_conversion(self):
        from langclaw.agents.builder import _build_deepagent_subagents

        specs = [
            {
                "name": "helper",
                "description": "Helps",
                "system_prompt": "Help.",
                "tools": None,
                "model": None,
                "output": "main_agent",
            }
        ]
        result = _build_deepagent_subagents(specs, [], self._make_config())
        assert len(result) == 1
        assert result[0]["name"] == "helper"
        assert result[0]["description"] == "Helps"
        assert result[0]["system_prompt"] == "Help."
        assert "tools" not in result[0]

    def test_tools_resolved(self):
        from langclaw.agents.builder import _build_deepagent_subagents

        tools = [self._make_mock_tool("web_search"), self._make_mock_tool("cron")]
        specs = [
            {
                "name": "researcher",
                "description": "Researches",
                "system_prompt": "Research.",
                "tools": ["web_search"],
                "model": None,
                "output": "main_agent",
            }
        ]
        result = _build_deepagent_subagents(specs, tools, self._make_config())
        assert len(result[0]["tools"]) == 1
        assert result[0]["tools"][0].name == "web_search"

    def test_middleware_includes_channel_context(self):
        from langclaw.agents.builder import _build_deepagent_subagents
        from langclaw.middleware.channel_context import ChannelContextMiddleware

        specs = [
            {
                "name": "helper",
                "description": "Helps",
                "system_prompt": "Help.",
                "tools": None,
                "model": None,
                "output": "main_agent",
            }
        ]
        result = _build_deepagent_subagents(specs, [], self._make_config())
        mw_types = [type(m) for m in result[0]["middleware"]]
        assert ChannelContextMiddleware in mw_types

    def test_middleware_includes_rbac_when_enabled(self):
        from langclaw.agents.builder import _build_deepagent_subagents

        specs = [
            {
                "name": "helper",
                "description": "Helps",
                "system_prompt": "Help.",
                "tools": None,
                "model": None,
                "output": "main_agent",
            }
        ]
        config = self._make_config(permissions_enabled=True)
        result = _build_deepagent_subagents(specs, [], config)
        assert len(result[0]["middleware"]) == 2

    def test_middleware_no_rbac_when_disabled(self):
        from langclaw.agents.builder import _build_deepagent_subagents

        specs = [
            {
                "name": "helper",
                "description": "Helps",
                "system_prompt": "Help.",
                "tools": None,
                "model": None,
                "output": "main_agent",
            }
        ]
        result = _build_deepagent_subagents(specs, [], self._make_config())
        assert len(result[0]["middleware"]) == 1

    def test_model_passed_through(self):
        from langclaw.agents.builder import _build_deepagent_subagents

        specs = [
            {
                "name": "helper",
                "description": "Helps",
                "system_prompt": "Help.",
                "tools": None,
                "model": "openai:gpt-4.1",
                "output": "main_agent",
            }
        ]
        result = _build_deepagent_subagents(specs, [], self._make_config())
        assert result[0]["model"] == "openai:gpt-4.1"

    def test_model_omitted_when_none(self):
        from langclaw.agents.builder import _build_deepagent_subagents

        specs = [
            {
                "name": "helper",
                "description": "Helps",
                "system_prompt": "Help.",
                "tools": None,
                "model": None,
                "output": "main_agent",
            }
        ]
        result = _build_deepagent_subagents(specs, [], self._make_config())
        assert "model" not in result[0]

    def test_channel_output_specs_skipped(self):
        """Channel-routed specs are handled separately, not by this function."""
        from langclaw.agents.builder import _build_deepagent_subagents

        specs = [
            {
                "name": "channel_agent",
                "description": "Reports to channel",
                "system_prompt": "Report.",
                "tools": None,
                "model": None,
                "output": "channel",
            }
        ]
        result = _build_deepagent_subagents(specs, [], self._make_config())
        assert len(result) == 0


# ---------------------------------------------------------------------------
# BYOA — app.subagent(graph=...) registration
# ---------------------------------------------------------------------------


class _FakeRunnable:
    """Minimal Runnable stand-in for tests."""

    def invoke(self, state):
        return {"messages": []}

    async def ainvoke(self, state):
        return {"messages": []}


class TestSubagentGraph:
    """Verify that app.subagent(graph=...) validates and stores specs."""

    def test_graph_runnable(self):
        from langchain_core.runnables import RunnableLambda

        from langclaw import Langclaw

        app = Langclaw()
        runnable = RunnableLambda(lambda x: {"messages": []})
        app.subagent(
            "my-graph",
            description="Custom pipeline",
            graph=runnable,
        )
        assert len(app._subagents) == 1
        spec = app._subagents[0]
        assert spec["name"] == "my-graph"
        assert spec["description"] == "Custom pipeline"
        assert spec["runnable"] is runnable

    def test_graph_subagent_dict(self):
        from langclaw import Langclaw

        app = Langclaw()
        app.subagent(
            "analyst",
            description="Financial analyst",
            graph={
                "system_prompt": "Analyze data.",
                "tools": [],
                "model": "openai:gpt-4.1",
            },
        )
        assert len(app._subagents) == 1
        spec = app._subagents[0]
        assert spec["name"] == "analyst"
        assert spec["description"] == "Financial analyst"
        assert spec["system_prompt"] == "Analyze data."
        assert spec["model"] == "openai:gpt-4.1"

    def test_graph_compiled_subagent_dict(self):
        from langclaw import Langclaw

        app = Langclaw()
        runnable = _FakeRunnable()
        app.subagent(
            "compiled",
            description="Pre-compiled",
            graph={"runnable": runnable},
        )
        spec = app._subagents[0]
        assert spec["name"] == "compiled"
        assert spec["description"] == "Pre-compiled"
        assert spec["runnable"] is runnable

    def test_graph_dict_name_overridden(self):
        """Method args take precedence over dict keys."""
        from langclaw import Langclaw

        app = Langclaw()
        app.subagent(
            "override-name",
            description="Override desc",
            graph={
                "name": "dict-name",
                "description": "dict desc",
                "system_prompt": "X.",
            },
        )
        spec = app._subagents[0]
        assert spec["name"] == "override-name"
        assert spec["description"] == "Override desc"

    def test_graph_raw_runnable_via_runnablelambda(self):
        from langchain_core.runnables import RunnableLambda

        from langclaw import Langclaw

        app = Langclaw()
        runnable = RunnableLambda(lambda x: {"messages": []})
        app.subagent(
            "my-lambda",
            description="A lambda agent",
            graph=runnable,
        )
        spec = app._subagents[0]
        assert spec["name"] == "my-lambda"
        assert spec["runnable"] is runnable

    def test_graph_invalid_type_raises(self):
        from langclaw import Langclaw

        app = Langclaw()
        with pytest.raises(TypeError, match="Runnable or dict"):
            app.subagent(
                "bad",
                description="Bad",
                graph="not_valid",  # type: ignore[arg-type]
            )

    def test_graph_and_system_prompt_mutually_exclusive(self):
        from langclaw import Langclaw

        app = Langclaw()
        with pytest.raises(ValueError, match="mutually exclusive"):
            app.subagent(
                "bad",
                description="Bad",
                graph=_FakeRunnable(),
                system_prompt="Conflict.",
            )

    def test_neither_graph_nor_system_prompt_raises(self):
        from langclaw import Langclaw

        app = Langclaw()
        with pytest.raises(ValueError, match="'graph' or 'system_prompt'"):
            app.subagent("bad", description="Bad")

    def test_all_types_in_single_list(self):
        from langchain_core.runnables import RunnableLambda

        from langclaw import Langclaw

        app = Langclaw()
        app.subagent("a", description="A", system_prompt="A.")
        app.subagent(
            "b",
            description="B",
            graph=RunnableLambda(lambda x: {"messages": []}),
        )
        app.subagent(
            "c",
            description="C",
            graph={"system_prompt": "C.", "tools": []},
        )
        assert len(app._subagents) == 3
        assert [s["name"] for s in app._subagents] == ["a", "b", "c"]
        assert "output" in app._subagents[0]
        assert "runnable" in app._subagents[1]
        assert "output" not in app._subagents[2]


# ---------------------------------------------------------------------------
# BYOA — _prepare_external_subagents middleware injection
# ---------------------------------------------------------------------------


class TestPrepareExternalSubagents:
    """Verify middleware injection for external subagent specs."""

    def _make_config(self, permissions_enabled: bool = False):
        from langclaw.config.schema import LangclawConfig

        cfg = LangclawConfig()
        cfg.permissions.enabled = permissions_enabled
        if permissions_enabled:
            from langclaw.config.schema import RoleConfig

            cfg.permissions.roles = {"viewer": RoleConfig(tools=["web_search"])}
        return cfg

    def test_compiled_passthrough(self):
        from langclaw.agents.builder import _prepare_external_subagents

        runnable = _FakeRunnable()
        specs = [{"name": "g", "description": "G", "runnable": runnable}]
        result = _prepare_external_subagents(specs, self._make_config())
        assert len(result) == 1
        assert result[0]["runnable"] is runnable
        assert "middleware" not in result[0]

    def test_subagent_gets_langclaw_middleware(self):
        from langclaw.agents.builder import _prepare_external_subagents
        from langclaw.middleware.channel_context import ChannelContextMiddleware

        specs = [
            {
                "name": "analyst",
                "description": "Analyst",
                "system_prompt": "Analyze.",
            }
        ]
        result = _prepare_external_subagents(specs, self._make_config())
        assert len(result) == 1
        mw_types = [type(m) for m in result[0]["middleware"]]
        assert ChannelContextMiddleware in mw_types

    def test_subagent_gets_rbac_when_enabled(self):
        from langclaw.agents.builder import _prepare_external_subagents

        specs = [
            {
                "name": "analyst",
                "description": "Analyst",
                "system_prompt": "Analyze.",
            }
        ]
        config = self._make_config(permissions_enabled=True)
        result = _prepare_external_subagents(specs, config)
        assert len(result[0]["middleware"]) == 2

    def test_subagent_no_rbac_when_disabled(self):
        from langclaw.agents.builder import _prepare_external_subagents

        specs = [
            {
                "name": "analyst",
                "description": "Analyst",
                "system_prompt": "Analyze.",
            }
        ]
        result = _prepare_external_subagents(specs, self._make_config())
        assert len(result[0]["middleware"]) == 1

    def test_preserves_user_middleware(self):
        """User-provided middleware should come after Langclaw's."""
        from langclaw.agents.builder import _prepare_external_subagents
        from langclaw.middleware.channel_context import ChannelContextMiddleware

        class _UserMiddleware:
            pass

        user_mw = _UserMiddleware()
        specs = [
            {
                "name": "analyst",
                "description": "Analyst",
                "system_prompt": "Analyze.",
                "middleware": [user_mw],
            }
        ]
        result = _prepare_external_subagents(specs, self._make_config())
        mw = result[0]["middleware"]
        assert isinstance(mw[0], ChannelContextMiddleware)
        assert mw[-1] is user_mw

    def test_mixed_specs(self):
        """Compiled and uncompiled specs in the same list."""
        from langclaw.agents.builder import _prepare_external_subagents

        runnable = _FakeRunnable()
        specs = [
            {"name": "compiled", "description": "C", "runnable": runnable},
            {"name": "declarative", "description": "D", "system_prompt": "D."},
        ]
        result = _prepare_external_subagents(specs, self._make_config())
        assert len(result) == 2
        assert result[0]["runnable"] is runnable
        assert "middleware" not in result[0]
        assert "middleware" in result[1]
        assert "runnable" not in result[1]


# ---------------------------------------------------------------------------
# Phase 2 — channel-routed subagent wrapper
# ---------------------------------------------------------------------------


class _FakeBus:
    """Minimal bus mock that records published messages."""

    def __init__(self):
        self.published: list = []

    async def publish(self, msg):
        self.published.append(msg)


class _FakeInnerAgent:
    """Minimal agent mock that returns a canned response."""

    def __init__(self, response_text: str = "Research results"):
        self._text = response_text

    async def ainvoke(self, state):
        from langchain_core.messages import AIMessage

        return {"messages": [AIMessage(content=self._text)]}


class TestChannelRoutedRunnable:
    """Test the _run_and_publish closure directly."""

    @pytest.mark.asyncio
    async def test_publishes_to_bus(self):
        from langclaw.agents.subagents import DELIVERY_CONFIRMATION

        bus = _FakeBus()
        agent = _FakeInnerAgent("Here are the results")

        # Build the closure manually to avoid needing a real LLM
        from langclaw.agents.subagents import _make_run_and_publish

        run_fn = _make_run_and_publish(
            inner_agent=agent,
            bus=bus,
            spec_name="researcher",
        )

        state = {
            "messages": [],
            "channel_context": {
                "channel": "telegram",
                "user_id": "123",
                "context_id": "ctx",
                "chat_id": "456",
            },
        }
        result = await run_fn(state)

        assert len(bus.published) == 1
        msg = bus.published[0]
        assert msg.channel == "telegram"
        assert msg.user_id == "123"
        assert msg.chat_id == "456"
        assert msg.content == "Here are the results"
        assert msg.metadata["_direct_delivery"] is True
        assert msg.metadata["subagent_name"] == "researcher"

        assert len(result["messages"]) == 1
        assert result["messages"][0].content == DELIVERY_CONFIRMATION

    @pytest.mark.asyncio
    async def test_no_publish_without_channel_context(self):
        bus = _FakeBus()
        agent = _FakeInnerAgent("Results")

        from langclaw.agents.subagents import _make_run_and_publish

        run_fn = _make_run_and_publish(
            inner_agent=agent,
            bus=bus,
            spec_name="helper",
        )

        state = {"messages": []}
        result = await run_fn(state)

        assert len(bus.published) == 0
        assert len(result["messages"]) == 1

    @pytest.mark.asyncio
    async def test_empty_agent_response(self):
        bus = _FakeBus()

        class _EmptyAgent:
            async def ainvoke(self, state):
                return {"messages": []}

        from langclaw.agents.subagents import _make_run_and_publish

        run_fn = _make_run_and_publish(
            inner_agent=_EmptyAgent(),
            bus=bus,
            spec_name="empty",
        )

        state = {
            "messages": [],
            "channel_context": {"channel": "discord"},
        }
        result = await run_fn(state)

        assert len(bus.published) == 0
        assert len(result["messages"]) == 1


# ---------------------------------------------------------------------------
# Phase 2 — GatewayManager direct delivery
# ---------------------------------------------------------------------------


class TestDirectDelivery:
    """Verify _handle short-circuits for _direct_delivery messages."""

    @pytest.mark.asyncio
    async def test_direct_delivery_sends_to_channel(self):
        from unittest.mock import AsyncMock

        from langclaw.bus.base import InboundMessage, OutboundMessage
        from langclaw.gateway.manager import GatewayManager

        mock_channel = AsyncMock()
        mock_channel.name = "telegram"
        mock_channel.is_enabled.return_value = True

        gm = GatewayManager.__new__(GatewayManager)
        gm._channel_map = {"telegram": mock_channel}

        msg = InboundMessage(
            channel="telegram",
            user_id="u1",
            context_id="ctx",
            content="Subagent result text",
            chat_id="c1",
            metadata={
                "_direct_delivery": True,
                "subagent_name": "researcher",
            },
        )

        await gm._handle(msg)

        mock_channel.send.assert_called_once()
        out: OutboundMessage = mock_channel.send.call_args[0][0]
        assert out.content == "Subagent result text"
        assert out.channel == "telegram"
        assert out.user_id == "u1"
        assert out.chat_id == "c1"
        assert out.type == "ai"
        assert out.metadata["subagent_name"] == "researcher"

    @pytest.mark.asyncio
    async def test_non_direct_delivery_not_shortcircuited(self):
        """Regular messages should NOT hit the direct delivery path."""
        from langclaw.bus.base import InboundMessage

        msg = InboundMessage(
            channel="telegram",
            user_id="u1",
            context_id="ctx",
            content="Hello",
            metadata={},
        )
        assert not (msg.metadata or {}).get("_direct_delivery")
