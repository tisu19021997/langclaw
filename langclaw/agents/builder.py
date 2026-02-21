"""
Agent builder — always produces a deepagents deep agent.

Default skills and memory are bundled inside the package and copied to the
user's workspace on first ``langclaw init``. The app always uses the workspace
copies so users can modify them directly.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from langchain_core.language_models import BaseChatModel
from langgraph.graph.state import CompiledStateGraph

from langclaw.config.schema import LangclawConfig
from langclaw.middleware.channel_context import ChannelContextMiddleware
from langclaw.middleware.guardrails import ContentFilterMiddleware, PIIMiddleware
from langclaw.middleware.rate_limit import RateLimitMiddleware
from langclaw.providers.registry import provider_registry
from langclaw.tools.memory import MEMORY_SYSTEM_PROMPT, MemoryTool

if TYPE_CHECKING:
    from langchain.agents.middleware import AgentMiddleware
    from langchain_core.tools import BaseTool
    from langgraph.types import Checkpointer

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_DEFAULTS_DIR = Path(__file__).parent / "defaults"
_DEFAULT_AGENTS_MD = _DEFAULTS_DIR / "AGENTS.md"
_DEFAULT_SKILLS_DIR = _DEFAULTS_DIR / "skills"

# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def create_claw_agent(
    config: LangclawConfig,
    *,
    checkpointer: Checkpointer | None = None,
    extra_tools: list[BaseTool | Any] | None = None,
    extra_skills: list[str] | None = None,
    extra_middleware: list[AgentMiddleware] | None = None,
    model: BaseChatModel | None = None,
) -> CompiledStateGraph:
    """
    Create a langclaw deep agent backed by ``deepagents.create_deep_agent``.

    The agent starts with the built-in default skills (summarize) and a
    persistent memory tool scoped to ``config.memories_dir``. Extra
    capabilities stack on top via ``extra_tools`` and ``extra_skills``.

    Args:
        config:           Loaded LangclawConfig.
        checkpointer:     LangGraph ``BaseCheckpointSaver`` for persisting
                          conversation state across turns. Without this the
                          agent starts fresh on every message.
        extra_tools:      Additional LangChain tools beyond the defaults.
        extra_skills:     Paths to directories containing ``SKILL.md`` files.
        extra_middleware: Additional ``AgentMiddleware`` instances inserted
                          after the built-in middleware stack.
        model:            Pre-built chat model. If omitted, resolved from config.

    Returns:
        A compiled LangGraph runnable (CompiledGraph) ready for ``.invoke``
        / ``.astream``.
    """
    try:
        from deepagents import create_deep_agent
        from deepagents.backends import FilesystemBackend
    except ImportError as exc:
        raise ImportError(
            "deepagents is required. Install with: uv add deepagents"
        ) from exc

    resolved_model = model or provider_registry.resolve_model(
        config.agents.model, config.providers
    )

    skills = [str(config.skills_dir)] + list(extra_skills or [])

    # Loaded by deepagents at startup and injected into the system prompt.
    # Only included if the user has already run `langclaw init`.
    memory = [str(config.agents_md_file)] if config.agents_md_file.exists() else []

    # Ensure the memories directory exists before the tool tries to use it
    config.memories_dir.mkdir(parents=True, exist_ok=True)

    memory_tool = MemoryTool(memories_dir=config.memories_dir)
    tools: list[Any] = [memory_tool, *list(extra_tools or [])]

    # Built-in middleware stack (order matters):
    #   1. ChannelContextMiddleware  — inject channel metadata first
    #   2. RateLimitMiddleware       — rate-check early, before expensive ops
    #   3. ContentFilterMiddleware   — block banned content before any LLM call
    #   4. PIIMiddleware             — redact PII from inbound messages
    #   5. caller-provided extras
    middleware: list[Any] = [
        ChannelContextMiddleware(),
        RateLimitMiddleware(rpm=config.agents.rate_limit_rpm),
        ContentFilterMiddleware(banned_keywords=config.agents.banned_keywords),
        PIIMiddleware(
            "azure_openai_api_key",
            detector=r"^[a-zA-Z0-9]{84}$",
            strategy="redact",
            apply_to_output=True,
            apply_to_tool_results=True,
        ),
        *(extra_middleware or []),
    ]

    return create_deep_agent(
        model=resolved_model,
        tools=tools,
        skills=skills,
        memory=memory,
        system_prompt=MEMORY_SYSTEM_PROMPT,
        checkpointer=checkpointer,
        backend=FilesystemBackend(
            root_dir=str(config.workspace_dir), virtual_mode=True
        ),
        middleware=middleware,
    )
