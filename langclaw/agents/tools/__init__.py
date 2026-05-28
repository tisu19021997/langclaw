"""Built-in agent tools for langclaw."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from langclaw.agents.tools.web_fetch import web_fetch
from langclaw.agents.tools.web_search import make_web_search_tool

if TYPE_CHECKING:
    from langclaw.config.schema import LangclawConfig
    from langclaw.cron.scheduler import CronManager

# Map each backend to the config field that holds its API key.
# duckduckgo needs no key, so it maps to an empty string constant.
_BACKEND_KEY_FIELD: dict[str, str] = {
    "brave": "brave_api_key",
    "tavily": "tavily_api_key",
    "duckduckgo": "",
}


def build_web_tools(config: LangclawConfig) -> list[Any]:
    """Return web-related tools enabled by the current config.

    The search tool is added when either:
    - the configured backend requires no API key (``"duckduckgo"``), or
    - the corresponding API key field in ``config.tools`` is non-empty.

    ``web_fetch`` is always included as it needs no credentials.
    """
    tools: list[Any] = []

    tools_cfg = config.tools
    backend = tools_cfg.search_backend
    key_field = _BACKEND_KEY_FIELD.get(backend, "")

    if key_field == "":
        # No key required (e.g. duckduckgo)
        tools.append(make_web_search_tool(backend))
    else:
        api_key: str = getattr(tools_cfg, key_field, "")
        if api_key:
            tools.append(make_web_search_tool(backend, api_key=api_key))

    tools.append(web_fetch)

    return tools


def build_gmail_tools(config: LangclawConfig) -> list[Any]:
    """Return Gmail tools enabled by the current config.

    When ``config.tools.gmail.readonly`` is ``True`` only ``read_email`` and
    ``search_emails`` are returned.  When ``False`` the full suite (send,
    draft, reply, manage_labels) is included as well.

    Returns an empty list when Gmail is disabled or credentials are missing.
    """
    gmail = config.tools.gmail
    if not gmail.enabled or not gmail.client_id:
        return []

    from langclaw.agents.tools.gmail import (
        make_draft_email_tool,
        make_manage_labels_tool,
        make_read_email_tool,
        make_reply_email_tool,
        make_search_emails_tool,
        make_send_email_tool,
    )

    if gmail.readonly:
        return [make_read_email_tool(gmail), make_search_emails_tool(gmail)]

    return [
        make_read_email_tool(gmail),
        make_search_emails_tool(gmail),
        make_send_email_tool(gmail),
        make_draft_email_tool(gmail),
        make_reply_email_tool(gmail),
        make_manage_labels_tool(gmail),
    ]


def build_fs_tools(config: LangclawConfig, workspace_dir: Path) -> list[Any]:
    """Return filesystem tools scoped to *workspace_dir*.

    The tools are always included — they require no external credentials.
    All file operations are sandboxed to *workspace_dir* and paths that
    escape it are rejected.

    Args:
        config:        Loaded ``LangclawConfig`` (reserved for future flags).
        workspace_dir: Absolute path to the agent's workspace root.
    """
    from langclaw.agents.tools.fs import make_fs_tools

    return make_fs_tools(workspace_dir)


def build_cron_tools(
    config: LangclawConfig,
    cron_manager: CronManager,
    workflow_names: tuple[str, ...] = (),
) -> list[Any]:
    """Return the cron tool when ``config.cron.enabled`` is ``True``.

    Args:
        config:         Loaded ``LangclawConfig``.
        cron_manager:   A running ``CronManager`` instance owned by the gateway.
        workflow_names: Registered workflow names, so the cron tool can validate
                        a ``workflow=`` schedule request and the agent can
                        schedule workflows as well as prompts.

    Returns:
        A list containing the ``cron`` tool, or an empty list if cron is
        disabled in config.
    """
    if not config.cron.enabled:
        return []

    from langclaw.agents.tools.cron import make_cron_tool

    return [
        make_cron_tool(cron_manager, timezone=config.cron.timezone, workflow_names=workflow_names)
    ]


__all__ = [
    "build_cron_tools",
    "build_fs_tools",
    "build_gmail_tools",
    "build_web_tools",
    "make_web_search_tool",
    "web_fetch",
]
