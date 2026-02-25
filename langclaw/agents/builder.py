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

from langclaw.agents.prompts.memory import MEMORY_SYSTEM_PROMPT
from langclaw.agents.tools import build_cron_tools, build_gmail_tools, build_web_tools
from langclaw.config.schema import LangclawConfig
from langclaw.middleware.channel_context import ChannelContextMiddleware
from langclaw.middleware.guardrails import ContentFilterMiddleware, PIIMiddleware
from langclaw.middleware.permissions import (
    LangclawContext,
    build_tool_permission_middleware,
)
from langclaw.middleware.rate_limit import RateLimitMiddleware
from langclaw.providers.registry import provider_registry
from langclaw.utils import to_virtual_path  # for extra_skills conversion

if TYPE_CHECKING:
    from langchain.agents.middleware import AgentMiddleware
    from langchain_core.tools import BaseTool
    from langgraph.types import Checkpointer

    from langclaw.cron.scheduler import CronManager

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
    cron_manager: CronManager | None = None,
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
        cron_manager:     Running ``CronManager`` instance. When provided and
                          ``config.cron.enabled`` is ``True``, the ``cron``
                          tool is added as a default tool so the agent can
                          schedule, list, and remove recurring jobs.
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

    skills = [config.agents.skills_source] + [
        to_virtual_path(s, config.agents.workspace_dir) for s in (extra_skills or [])
    ]

    tools: list[Any] = list(extra_tools or [])
    tools += build_web_tools(config)
    tools += build_gmail_tools(config)
    if cron_manager is not None:
        tools += build_cron_tools(config, cron_manager)

    agents_md = config.agents.agents_md_file.read_text("utf-8")
    system_prompt = f"""{agents_md}\n\n{MEMORY_SYSTEM_PROMPT}"""

    # Built-in middleware stack (order matters):
    #   1. ChannelContextMiddleware  — inject channel metadata first
    #   2. ToolPermission middleware — filter tools per-user role
    #   3. RateLimitMiddleware       — rate-check early
    #   4. ContentFilterMiddleware   — block banned content
    #   5. PIIMiddleware             — redact PII
    #   6. caller-provided extras
    middleware: list[Any] = [
        ChannelContextMiddleware(),
    ]

    if config.permissions.enabled:
        middleware.append(
            build_tool_permission_middleware(config.permissions),
        )

    middleware.extend(
        [
            RateLimitMiddleware(rpm=config.agents.rate_limit_rpm),
            ContentFilterMiddleware(
                banned_keywords=config.agents.banned_keywords,
            ),
            PIIMiddleware(
                "azure_openai_api_key",
                detector=r"^[a-zA-Z0-9]{84}$",
                strategy="redact",
                apply_to_output=True,
                apply_to_tool_results=True,
            ),
            *(extra_middleware or []),
        ]
    )

    context_schema = LangclawContext if config.permissions.enabled else None

    return create_deep_agent(
        model=resolved_model,
        tools=tools,
        skills=skills,
        system_prompt=system_prompt,
        checkpointer=checkpointer,
        backend=FilesystemBackend(
            root_dir=str(config.agents.workspace_dir),
            virtual_mode=True,
        ),
        middleware=middleware,
        context_schema=context_schema,
    )
