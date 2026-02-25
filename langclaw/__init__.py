"""
langclaw — production-ready multi-channel AI agent framework.

Quick start::

    from langclaw import Langclaw

    app = Langclaw()

    @app.tool()
    async def my_tool(query: str) -> str:
        \"\"\"My custom tool.\"\"\"
        ...

    app.run()
"""

from langclaw.agents.builder import create_claw_agent
from langclaw.app import Langclaw
from langclaw.config.schema import LangclawConfig, load_config

__version__ = "0.1.0"

__all__ = [
    "__version__",
    "Langclaw",
    "create_claw_agent",
    "LangclawConfig",
    "load_config",
]
