"""
Channel-routed subagent builder.

Wraps a standard LangGraph agent in a ``CompiledSubAgent``-compatible
runnable that publishes its final output directly to the message bus.
The ``GatewayManager`` routes ``InboundMessage`` objects with
``to="channel"`` straight to the channel without re-running the agent
pipeline.

The main agent receives a short confirmation instead of the full
subagent output, keeping its context window clean.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableLambda
from loguru import logger

from langclaw.bus.base import InboundMessage
from langclaw.context import LangclawContext
from langclaw.middleware.channel_context import ChannelContextMiddleware
from langclaw.middleware.permissions import build_capability_filter_middleware

if TYPE_CHECKING:
    from langchain_core.language_models import BaseChatModel

    from langclaw.bus.base import BaseMessageBus
    from langclaw.config.schema import LangclawConfig

DELIVERY_CONFIRMATION = "Results have been delivered directly to the user's channel."


def _make_run_and_publish(
    *,
    inner_agent: Any,
    bus: BaseMessageBus,
    spec_name: str,
) -> Any:
    """Build the async closure used as the ``CompiledSubAgent`` runnable.

    Extracted so unit tests can exercise the publish/confirm
    logic without constructing a real LangGraph agent.
    """

    async def _run_and_publish(
        state: dict[str, Any],
    ) -> dict[str, Any]:
        result = await inner_agent.ainvoke(state)

        messages = result.get("messages", [])
        if not messages:
            return {
                "messages": [
                    AIMessage(content=DELIVERY_CONFIRMATION),
                ],
            }

        last_msg = messages[-1]
        content = last_msg.text if hasattr(last_msg, "text") else str(last_msg.content)

        channel_ctx = state.get("channel_context", {})
        channel = channel_ctx.get("channel", "")
        user_id = channel_ctx.get("user_id", "")
        context_id = channel_ctx.get("context_id", "")
        chat_id = channel_ctx.get("chat_id", "")

        if channel and content:
            try:
                await bus.publish(
                    InboundMessage(
                        channel=channel,
                        user_id=user_id,
                        context_id=context_id,
                        content=content,
                        chat_id=chat_id,
                        origin="subagent",
                        to="channel",
                        metadata={
                            "subagent_name": spec_name,
                        },
                    )
                )
            except Exception:
                logger.exception(
                    "Failed to publish channel-routed subagent output for {}",
                    spec_name,
                )

        return {
            "messages": [
                AIMessage(content=DELIVERY_CONFIRMATION),
            ],
        }

    return _run_and_publish


def build_channel_routed_subagent(
    *,
    spec: dict[str, Any],
    bus: BaseMessageBus,
    tools: list[Any],
    model: str | BaseChatModel,
    config: LangclawConfig,
    context_schema: type[LangclawContext],
) -> dict[str, Any]:
    """Build a channel-routed subagent dict.

    Returns a ``CompiledSubAgent``-compatible dict.

    The returned runnable:
    1. Executes the inner agent to completion.
    2. Publishes the final text as an ``InboundMessage`` with
       ``origin="subagent"`` and ``to="channel"`` so the gateway
       delivers it straight to the originating channel.
    3. Returns a short confirmation message to the main agent.

    Args:
        spec:   Langclaw subagent spec dict (from ``app.subagent()``).
        bus:    Running ``BaseMessageBus`` instance.
        tools:  Resolved tool objects for this subagent.
        model:  LLM for this subagent (resolved model or string).
        config: Loaded ``LangclawConfig`` (for middleware construction).
        context_schema: Context schema to use for the subagent.

    Returns:
        A dict with ``name``, ``description``, and ``runnable`` keys
        matching the ``CompiledSubAgent`` shape expected by deepagents.
    """
    from langchain.agents import create_agent

    sa_middleware: list[Any] = [ChannelContextMiddleware()]
    if config.permissions.enabled:
        sa_middleware.append(
            build_capability_filter_middleware(config.permissions),
        )

    inner_agent = create_agent(
        model,
        system_prompt=spec["system_prompt"],
        tools=tools,
        middleware=sa_middleware,
        name=spec["name"],
        context_schema=context_schema,
    )

    run_fn = _make_run_and_publish(
        inner_agent=inner_agent,
        bus=bus,
        spec_name=spec["name"],
    )

    return {
        "name": spec["name"],
        "description": spec["description"],
        "runnable": RunnableLambda(run_fn),
    }
