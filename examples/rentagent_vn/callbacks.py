from typing import Any

from loguru import logger

from examples.rentagent_vn.models import ScrapeResult
from examples.rentagent_vn.trace import observe
from langclaw import Langclaw
from langclaw.bus.base import InboundMessage


def format_progress_message(purpose: str) -> str:
    """Formats a user-friendly progress update."""
    return f"🐟 {purpose}"


async def progress_callback(
    app: Langclaw,
    job_id: str,
    run_id: str,
    url: str,
    event_type: str,
    purpose: str,
    channel_context: dict[str, Any],
) -> None:
    bus = app.get_bus()
    if bus is None:
        logger.error("Cannot publish progress for scrape {} — bus unavailable", job_id)
        return

    content = format_progress_message(purpose)

    await bus.publish(
        InboundMessage(
            channel=channel_context.get("channel", ""),
            user_id=channel_context.get("user_id", ""),
            context_id=channel_context.get("context_id", ""),
            chat_id=channel_context.get("chat_id", ""),
            content=content,
            origin="background_scrape",
            to="channel",
            metadata={
                "job_id": job_id,
            },
        )
    )


@observe
async def streaming_url_callback(
    app: Langclaw,
    job_id: str,
    run_id: str,
    url: str,
    streaming_url: str,
    channel_context: dict[str, Any],
) -> None:
    meta = channel_context.get("metadata", {}) or {}
    reply_to = meta.get("message_id") or meta.get("reply_to")
    bus = app.get_bus()
    if bus is None:
        return

    content = (
        f"🌐 **Live Preview Available**\n"
        f"I'm currently browsing **{url}**.\n"
        f"🔗 [Click here to watch live]({streaming_url})"
    )

    await bus.publish(
        InboundMessage(
            channel=channel_context.get("channel", ""),
            user_id=channel_context.get("user_id", ""),
            context_id=channel_context.get("context_id", ""),
            chat_id=channel_context.get("chat_id", ""),
            content=content,
            origin="background_scrape",
            to="channel",
            metadata={
                "job_id": job_id,
                "reply_to": reply_to,
            },
        )
    )


async def error_callback(
    job_id: str, run_id: str, url: str, message: str, channel_context: dict[str, Any]
) -> None:
    pass


@observe
async def result_callback(
    app: Langclaw,
    job_id: str,
    result: ScrapeResult,
    channel_context: dict[str, Any],
) -> None:
    meta = channel_context.get("metadata", {}) or {}
    reply_to = meta.get("message_id") or meta.get("reply_to")
    bus = app.get_bus()
    if bus is None:
        logger.error("Cannot publish results for scrape {} — bus unavailable", job_id)
        return

    logger.info(
        "Scrape {} complete — {} listings, {} errors",
        job_id,
        len(result.listings),
        len(result.errors),
    )
    await bus.publish(
        InboundMessage(
            channel=channel_context.get("channel", ""),
            user_id=channel_context.get("user_id", ""),
            context_id=channel_context.get("context_id", ""),
            chat_id=channel_context.get("chat_id", ""),
            content=(
                f"I have finished searching for listings. "
                f"Found {len(result.listings)} listings across "
                f"{result.urls_scanned} platform(s). "
                f"Results:\n```{result.model_dump_json()}```\n\n"
                "Now I need to present the results to the user."
            ),
            origin="background_scrape",
            to="agent",
            metadata={
                "job_id": job_id,
                "reply_to": reply_to,
            },
        )
    )
