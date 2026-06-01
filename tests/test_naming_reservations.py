"""Namespace reservation guards (langclaw/naming.py + app enforcement).

A workflow generates a `workflow_<name>` tool and the framework owns several
command names; these tests prove a developer cannot silently collide with either.
"""

from __future__ import annotations

import pytest

from langclaw.app import Langclaw
from langclaw.naming import (
    check_command_name_allowed,
    check_tool_name_allowed,
    reserved_prefix_owner,
    workflow_tool_name,
)

# --- pure helpers -----------------------------------------------------------


def test_workflow_tool_name_composition():
    assert workflow_tool_name("research") == "workflow_research"


def test_reserved_prefix_owner():
    assert reserved_prefix_owner("workflow_x") == "workflow"
    assert reserved_prefix_owner("search") is None


def test_check_tool_name_allowed_rejects_reserved_prefix():
    with pytest.raises(ValueError, match="reserved"):
        check_tool_name_allowed("workflow_anything")
    check_tool_name_allowed("search")  # free name → no raise


def test_check_command_name_allowed():
    with pytest.raises(ValueError, match="reserved"):
        check_command_name_allowed("workflow")
    with pytest.raises(ValueError, match="reserved"):
        check_command_name_allowed("start")
    check_command_name_allowed("ping")  # free name → no raise


# --- app enforcement --------------------------------------------------------


def test_app_tool_decorator_rejects_reserved_prefix():
    app = Langclaw()

    with pytest.raises(ValueError, match="reserved"):

        @app.tool()
        def workflow_export(x: str) -> str:
            """A tool that would shadow a workflow's generated tool."""
            return x


def test_app_register_tool_rejects_reserved_prefix():
    from langchain_core.tools import tool as lc_tool

    app = Langclaw()

    @lc_tool
    def workflow_thing(x: str) -> str:
        """reserved-prefix tool"""
        return x

    with pytest.raises(ValueError, match="reserved"):
        app.register_tool(workflow_thing)


def test_app_command_rejects_reserved_name():
    app = Langclaw()

    with pytest.raises(ValueError, match="reserved"):

        @app.command("workflow")
        async def my_workflow_cmd(ctx) -> str:
            return "nope"


def test_app_allows_normal_tool_and_command():
    app = Langclaw()

    @app.tool()
    def search(q: str) -> str:
        """fine"""
        return q

    @app.command("ping")
    async def ping(ctx) -> str:
        return "pong"

    assert any(getattr(t, "name", "") == "search" for t in app._extra_tools)
    assert any(name == "ping" for name, _fn, _desc in app._extra_commands)


def test_both_guards_keep_workflow_tool_namespace_disjoint():
    """The two guards together make a workflow's generated tool uncollidable:

    - a workflow's *bare* name can't equal an existing tool name (reserved_names);
    - a tool name can't take the ``workflow_`` prefix (prefix guard).

    So workflow 'export' → tool 'workflow_export' can never clash with a tool.
    """
    # (a) workflow bare name vs existing tool name → blocked
    app = Langclaw()

    @app.tool()
    def export(x: str) -> str:
        """normal tool named export"""
        return x

    with pytest.raises(ValueError, match="collides"):

        @app.workflow("export", description="clashes with the export tool")
        async def export_wf(ctx, inp):
            return inp

    # (b) a tool can't claim the generated workflow tool name
    app2 = Langclaw()

    @app2.workflow("report", description="generates tool workflow_report")
    async def report_wf(ctx, inp):
        return inp

    with pytest.raises(ValueError, match="reserved"):

        @app2.tool()
        def workflow_report(x: str) -> str:
            """would collide with the workflow's generated tool"""
            return x

    assert workflow_tool_name("report") == "workflow_report"
