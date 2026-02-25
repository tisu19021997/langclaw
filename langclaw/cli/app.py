"""
langclaw CLI — powered by Typer.

Commands:
    init        — scaffold ~/.langclaw/config.json
    agent       — interactive REPL or single-shot message
    gateway     — start multi-channel gateway
    cron        — manage scheduled jobs (add / list / remove)
    status      — show provider + channel configuration health
"""

from __future__ import annotations

import asyncio
from typing import Annotated

import typer

from langclaw.cli.utils import install_deps
from langclaw.config.schema import load_config

app = typer.Typer(
    name="langclaw",
    help="Multi-channel AI agent framework.",
    no_args_is_help=True,
    pretty_exceptions_show_locals=False,
)
cron_app = typer.Typer(help="Manage scheduled cron jobs.", no_args_is_help=True)
app.add_typer(cron_app, name="cron")


# ---------------------------------------------------------------------------
# langclaw init
# ---------------------------------------------------------------------------


@app.command()
def init(
    force: Annotated[
        bool, typer.Option("--force", "-f", help="Overwrite existing config.")
    ] = False,
) -> None:
    """Scaffold ~/.langclaw/ with config and default workspace files."""
    import shutil

    from langclaw.agents.builder import _DEFAULT_AGENTS_MD, _DEFAULT_SKILLS_DIR
    from langclaw.config.schema import _CONFIG_PATH, save_default_config

    if _CONFIG_PATH.exists() and not force:
        typer.echo(
            f"Config already exists at {_CONFIG_PATH}. Use --force to overwrite.",
            err=True,
        )
        raise typer.Exit(1)

    path = save_default_config()
    typer.echo(f"Config written to {path}")

    cfg = load_config()
    workspace = cfg.agents.workspace_dir
    workspace.mkdir(parents=True, exist_ok=True)

    # Copy AGENTS.md (skip if already present and not --force)
    dest_agents_md = cfg.agents.agents_md_file
    if not dest_agents_md.exists() or force:
        shutil.copy2(_DEFAULT_AGENTS_MD, dest_agents_md)
        typer.echo(f"AGENTS.md  → {dest_agents_md}")
    else:
        typer.echo(f"AGENTS.md  already exists at {dest_agents_md} (skipped)")

    # Copy default skills (merge; existing skill dirs not overwritten unless --force)
    dest_skills = cfg.agents.skills_dir
    for skill_dir in _DEFAULT_SKILLS_DIR.iterdir():
        if not skill_dir.is_dir():
            continue
        dest = dest_skills / skill_dir.name
        if dest.exists() and not force:
            typer.echo(f"skill/{skill_dir.name}  already exists (skipped)")
        else:
            shutil.copytree(skill_dir, dest, dirs_exist_ok=True)
            typer.echo(f"skill/{skill_dir.name}  → {dest}")

    typer.echo("\nEdit AGENTS.md and skills to customise your agent.")

    # Create memories directory
    memories_dir = cfg.agents.memories_dir
    if not memories_dir.exists() or force:
        memories_dir.mkdir(parents=True, exist_ok=True)
        typer.echo(f"memories directory created at {memories_dir}")
    else:
        typer.echo(f"memories directory already exists at {memories_dir} (skipped)")

    # Install all dependencies
    install_deps()


# ---------------------------------------------------------------------------
# langclaw agent
# ---------------------------------------------------------------------------


@app.command()
def agent(
    message: Annotated[
        str | None,
        typer.Option(
            "--message", "-m", help="Single message to send (non-interactive)."
        ),
    ] = None,
    model: Annotated[
        str | None,
        typer.Option("--model", help="Override the model from config."),
    ] = None,
) -> None:
    """Start an interactive REPL or send a single message to the agent."""
    asyncio.run(_agent_async(message=message, model_override=model))


async def _agent_async(
    message: str | None,
    model_override: str | None,
) -> None:
    from langclaw.app import Langclaw
    from langclaw.checkpointer import make_checkpointer_backend

    lc = Langclaw.from_env()
    if model_override:
        lc.config.agents.model = model_override

    cp_cfg = lc.config.checkpointer
    backend = make_checkpointer_backend(
        cp_cfg.backend,
        db_path=cp_cfg.sqlite.db_path,
        dsn=cp_cfg.postgres.dsn,
    )

    async with backend:
        claw_agent = lc.create_agent(checkpointer=backend.get())
        thread_id = "cli-session"
        runnable_config = {"configurable": {"thread_id": thread_id}}

        if message:
            await _run_once(claw_agent, message, runnable_config)
        else:
            await _run_repl(claw_agent, runnable_config)


async def _stream_agent(agent: object, message: str, config: dict) -> str:
    """
    Stream an agent response, printing only new characters as they arrive.

    ``stream_mode="values"`` emits the *full accumulated state* after every
    node execution, so chunk["messages"][-1].content always contains the
    entire response up to that point.  We track what we've already printed
    and only output the delta.

    Returns the complete final response string.
    """
    from langchain_core.messages import AIMessage, HumanMessage

    accumulated = ""
    async for chunk in agent.astream(
        {"messages": [HumanMessage(content=message)]},
        config=config,
        stream_mode="updates",
        print_mode="updates",
    ):
        if "messages" not in chunk:
            continue
        last = chunk["messages"][-1]
        # Only print AI / assistant messages, not tool results or human echoes
        if not isinstance(last, AIMessage):
            continue
        content = last.content or ""
        if not isinstance(content, str):
            # Some models return a list of content blocks; flatten to text
            content = " ".join(
                block.get("text", "") if isinstance(block, dict) else str(block)
                for block in content
            )
        if content and content != accumulated:
            delta = content[len(accumulated) :]
            if delta:
                typer.echo(delta, nl=False)
            accumulated = content
    return accumulated


async def _run_once(agent: object, message: str, config: dict) -> None:
    typer.echo("Agent: ", nl=False)
    await _stream_agent(agent, message, config)
    typer.echo()


async def _run_repl(agent: object, config: dict) -> None:
    typer.echo("langclaw agent — type 'exit' or Ctrl+C to quit.\n")
    while True:
        try:
            user_input = typer.prompt("You")
        except (EOFError, KeyboardInterrupt):
            typer.echo("\nBye.")
            break

        if user_input.strip().lower() in ("exit", "quit", "q"):
            typer.echo("Bye.")
            break

        typer.echo("Agent: ", nl=False)
        await _stream_agent(agent, user_input, config)
        typer.echo()


# ---------------------------------------------------------------------------
# langclaw gateway
# ---------------------------------------------------------------------------


@app.command()
def gateway() -> None:
    """Start the multi-channel gateway (all enabled channels)."""
    from langclaw.app import Langclaw

    Langclaw.from_env().run()


# ---------------------------------------------------------------------------
# langclaw cron
# ---------------------------------------------------------------------------


@cron_app.command("add")
def cron_add(
    name: Annotated[str, typer.Option("--name", "-n", help="Job name.")],
    message: Annotated[
        str, typer.Option("--message", "-m", help="Message to send on trigger.")
    ],
    channel: Annotated[str, typer.Option("--channel", "-c", help="Target channel.")],
    user_id: Annotated[str, typer.Option("--user-id", "-u", help="Target user ID.")],
    context_id: Annotated[
        str, typer.Option("--context-id", help="Context/chat ID.")
    ] = "default",
    cron: Annotated[
        str | None, typer.Option("--cron", help='Cron expression, e.g. "0 9 * * *".')
    ] = None,
    every: Annotated[
        int | None, typer.Option("--every", help="Interval in seconds.")
    ] = None,
) -> None:
    """Schedule a new cron job (persisted to the configured data store)."""
    if cron is None and every is None:
        typer.echo("Provide --cron or --every.", err=True)
        raise typer.Exit(1)

    asyncio.run(
        _cron_add_async(
            name=name,
            message=message,
            channel=channel,
            user_id=user_id,
            context_id=context_id,
            cron_expr=cron,
            every_seconds=every,
        )
    )


async def _cron_add_async(
    *,
    name: str,
    message: str,
    channel: str,
    user_id: str,
    context_id: str,
    cron_expr: str | None,
    every_seconds: int | None,
) -> None:
    from langclaw.bus import AsyncioMessageBus
    from langclaw.cron import make_cron_manager

    cfg = load_config()
    bus = AsyncioMessageBus()
    await bus.start()
    mgr = make_cron_manager(bus=bus, config=cfg.cron)
    await mgr.start()
    try:
        job_id = await mgr.add_job(
            name=name,
            message=message,
            channel=channel,
            user_id=user_id,
            context_id=context_id,
            cron_expr=cron_expr,
            every_seconds=every_seconds,
        )
        typer.echo(f"Job created: {job_id}")
    finally:
        await mgr.stop()
        await bus.stop()


@cron_app.command("list")
def cron_list() -> None:
    """List all scheduled cron jobs from the configured data store."""
    asyncio.run(_cron_list_async())


async def _cron_list_async() -> None:
    from langclaw.cron import list_jobs_from_store

    cfg = load_config()
    if cfg.cron.data_store.backend == "memory":
        typer.echo(
            "Cannot list jobs: the 'memory' data store does not persist jobs. "
            "Set cron.data_store.backend to 'sqlite' (default) or 'postgres'.",
            err=True,
        )
        raise typer.Exit(1)

    try:
        jobs = await list_jobs_from_store(cfg.cron)
    except Exception as exc:
        typer.echo(f"Error reading data store: {exc}", err=True)
        raise typer.Exit(1) from exc

    if not jobs:
        typer.echo("No cron jobs found.")
        return

    typer.echo(f"{'ID':<36}  {'Name':<24}  {'Schedule':<20}  Channel / User")
    typer.echo("-" * 100)
    for job in jobs:
        typer.echo(
            f"{job.id:<36}  {job.name[:24]:<24}  {job.schedule:<20}  "
            f"{job.channel}/{job.user_id}"
        )


@cron_app.command("remove")
def cron_remove(
    job_id: Annotated[str, typer.Argument(help="Job ID to remove.")],
) -> None:
    """Remove a scheduled cron job by ID."""
    typer.echo(f"Remove job {job_id} via the running gateway process.")


# ---------------------------------------------------------------------------
# langclaw status
# ---------------------------------------------------------------------------


@app.command()
def status() -> None:
    """Show configuration and provider health."""
    from langclaw.providers import provider_registry

    cfg = load_config()

    typer.echo("\n=== Providers ===")
    rows = provider_registry.list_configured(cfg.providers)
    for row in rows:
        mark = "✓" if row["configured"] == "yes" else "✗"
        gw = " (gateway)" if row["gateway"] == "yes" else ""
        typer.echo(f"  {mark} {row['display']}{gw}")

    typer.echo("\n=== Channels ===")
    ch = cfg.channels
    channel_states = [
        ("telegram", ch.telegram.enabled),
        ("discord", ch.discord.enabled),
        ("slack", ch.slack.enabled),
        ("websocket", ch.websocket.enabled),
    ]
    for name, enabled in channel_states:
        mark = "✓" if enabled else "✗"
        typer.echo(f"  {mark} {name}")

    typer.echo("\n=== Bus / Checkpointer ===")
    typer.echo(f"  Bus:          {cfg.bus.backend}")
    typer.echo(f"  Checkpointer: {cfg.checkpointer.backend}")
    typer.echo(f"  Agent model:  {cfg.agents.model}")
    typer.echo()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    app()


if __name__ == "__main__":
    main()
