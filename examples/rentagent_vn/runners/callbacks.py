"""Callback implementations for scrape and research runners.

All callbacks follow the pattern: publish to broker (for SSE) + publish
to message bus (for agent/channel delivery).
"""

from __future__ import annotations

import time
from typing import Any

from loguru import logger

from examples.rentagent_vn.api.brokers import (
    ResearchEvent,
    ScanEvent,
    research_broker,
    scan_broker,
)
from examples.rentagent_vn.db import queries
from examples.rentagent_vn.models import ScrapeResult
from examples.rentagent_vn.trace import observe
from langclaw import Langclaw
from langclaw.bus.base import InboundMessage

# ---------------------------------------------------------------------------
# Scrape callbacks
# ---------------------------------------------------------------------------


def format_progress_message(purpose: str) -> str:
    """Formats a user-friendly progress update."""
    return f"\U0001f41f {purpose}"


async def progress_callback(
    app: Langclaw,
    job_id: str,
    run_id: str,
    url: str,
    event_type: str,
    purpose: str,
    channel_context: dict[str, Any],
) -> None:
    # Publish to scan broker for SSE streaming
    scan_id = (channel_context.get("metadata") or {}).get("scan_id")
    if scan_id:
        scan_broker.publish(
            scan_id,
            ScanEvent(
                type="progress",
                url=url,
                data={"purpose": purpose, "run_id": run_id},
                timestamp=time.monotonic(),
            ),
        )

    bus = app.get_bus()
    if bus is None:
        logger.error("Cannot publish progress for scrape {} \u2014 bus unavailable", job_id)
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
    # Publish to scan broker for SSE streaming
    scan_id = (channel_context.get("metadata") or {}).get("scan_id")
    if scan_id:
        scan_broker.publish(
            scan_id,
            ScanEvent(
                type="streaming_url",
                url=url,
                data={"streaming_url": streaming_url, "run_id": run_id},
                timestamp=time.monotonic(),
            ),
        )

    meta = channel_context.get("metadata", {}) or {}
    reply_to = meta.get("message_id") or meta.get("reply_to")
    bus = app.get_bus()
    if bus is None:
        return

    content = (
        "\U0001f310 **Live Preview Available**\n"
        f"I'm currently browsing **{url}**.\n"
        f"\U0001f517 [Click here to watch live]({streaming_url})"
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
    app: Langclaw,
    job_id: str,
    run_id: str,
    url: str,
    message: str,
    channel_context: dict[str, Any],
) -> None:
    pass


async def url_complete_callback(
    app: Langclaw,
    job_id: str,
    url: str,
    listings_count: int,
    channel_context: dict[str, Any],
) -> None:
    """Called when a single URL finishes scanning."""
    scan_id = (channel_context.get("metadata") or {}).get("scan_id")
    logger.info(
        "URL complete callback: url={}, listings={}, scan_id={}",
        url,
        listings_count,
        scan_id,
    )
    if scan_id:
        scan_broker.publish(
            scan_id,
            ScanEvent(
                type="url_complete",
                url=url,
                data={"listings_count": listings_count},
                timestamp=time.monotonic(),
            ),
        )


@observe
async def result_callback(
    app: Langclaw,
    job_id: str,
    result: ScrapeResult,
    channel_context: dict[str, Any],
) -> None:
    meta = channel_context.get("metadata", {}) or {}
    scan_id = meta.get("scan_id")
    campaign_id = channel_context.get("context_id")

    # 1. Save listings to database and track new vs duplicate counts
    new_count = 0
    total_saved = 0

    if campaign_id and scan_id:
        try:
            for listing in result.listings:
                inserted = await queries.upsert_listing(
                    campaign_id,
                    listing.model_dump(),
                    scan_id=scan_id,
                )
                if inserted and not inserted.get("_was_duplicate"):
                    new_count += 1
                total_saved += 1

            # 2. Update scan status to completed
            await queries.complete_scan(
                scan_id,
                listings_found=len(result.listings),
                new_listings=new_count,
                errors=result.errors if result.errors else None,
            )

            # 3. Add activity log entry for scan completion
            await queries.add_activity(
                campaign_id,
                "scan_complete",
                f"Scan completed with {len(result.listings)} listings, {new_count} new",
                scan_id=scan_id,
                metadata={
                    "listings_found": len(result.listings),
                    "new_listings": new_count,
                    "errors_count": len(result.errors),
                    "urls_scanned": result.urls_scanned,
                },
            )

            logger.info(
                "Scan {} persisted \u2014 {} listings ({} new), {} errors",
                scan_id,
                len(result.listings),
                new_count,
                len(result.errors),
            )

        except Exception as exc:
            logger.exception("Failed to save scan results for {}", scan_id)
            # Mark scan as failed if DB operations fail
            try:
                await queries.fail_scan(scan_id, [{"error": str(exc)}])
                await queries.add_activity(
                    campaign_id,
                    "scan_error",
                    f"Error saving scan results: {exc}",
                    scan_id=scan_id,
                )
            except Exception:
                logger.exception("Failed to mark scan {} as failed", scan_id)

    # Publish completion to scan broker for SSE streaming (always, even on DB error)
    if scan_id:
        scan_broker.publish(
            scan_id,
            ScanEvent(
                type="complete",
                url=None,
                data={
                    "listings_found": len(result.listings),
                    "new_listings": new_count,
                    "errors": len(result.errors),
                    "urls_scanned": result.urls_scanned,
                },
                timestamp=time.monotonic(),
            ),
        )

    reply_to = meta.get("message_id") or meta.get("reply_to")
    bus = app.get_bus()
    if bus is None:
        logger.error("Cannot publish results for scrape {} \u2014 bus unavailable", job_id)
        return

    logger.info(
        "Scrape {} complete \u2014 {} listings, {} errors",
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
                f"Found {len(result.listings)} listings ({new_count} new) across "
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


# ---------------------------------------------------------------------------
# Research callbacks
# ---------------------------------------------------------------------------


async def research_streaming_url_callback(
    app: Langclaw,
    research_id: str,
    listing_id: str,
    campaign_id: str,
    streaming_url: str,
    channel_context: dict[str, Any],
) -> None:
    """Called when TinyFish provides a streaming URL for live browser viewing."""
    logger.info(
        "Research {} streaming URL received: {}",
        research_id,
        streaming_url,
    )
    research_broker.publish(
        campaign_id,
        ResearchEvent(
            type="streaming_url",
            research_id=research_id,
            data={
                "listing_id": listing_id,
                "browser_url": streaming_url,
            },
            timestamp=time.monotonic(),
        ),
    )


async def research_progress_callback(
    app: Langclaw,
    research_id: str,
    listing_id: str,
    campaign_id: str,
    step: str,
    detail: str,
    channel_context: dict[str, Any],
) -> None:
    """Called when TinyFish reports progress on a research job."""
    research_broker.publish(
        campaign_id,
        ResearchEvent(
            type="progress",
            research_id=research_id,
            data={
                "listing_id": listing_id,
                "step": step,
                "detail": detail,
            },
            timestamp=time.monotonic(),
        ),
    )

    bus = app.get_bus()
    if bus is None:
        return

    await bus.publish(
        InboundMessage(
            channel=channel_context.get("channel", ""),
            user_id=channel_context.get("user_id", ""),
            context_id=channel_context.get("context_id", ""),
            chat_id=channel_context.get("chat_id", ""),
            content=f"Researching: {detail}",
            origin="area_research",
            to="channel",
            metadata={"research_id": research_id},
        )
    )


async def research_result_callback(
    app: Langclaw,
    research_id: str,
    listing_id: str,
    campaign_id: str,
    overall_score: float,
    verdict: str,
    channel_context: dict[str, Any],
) -> None:
    """Called when a research job completes successfully."""
    research_broker.publish(
        campaign_id,
        ResearchEvent(
            type="completed",
            research_id=research_id,
            data={
                "listing_id": listing_id,
                "overall_score": overall_score,
                "verdict": verdict,
            },
            timestamp=time.monotonic(),
        ),
    )
    research_broker.decrement_active(campaign_id)

    await queries.add_activity(
        campaign_id,
        "research_complete",
        f"Area research completed \u2014 score {overall_score:.1f}/10",
        metadata={
            "research_id": research_id,
            "listing_id": listing_id,
            "overall_score": overall_score,
        },
    )

    logger.info(
        "Research {} completed \u2014 score {:.1f}/10",
        research_id,
        overall_score,
    )


async def research_error_callback(
    app: Langclaw,
    research_id: str,
    listing_id: str,
    campaign_id: str,
    error_message: str,
    channel_context: dict[str, Any],
) -> None:
    """Called when a research job fails."""
    research_broker.publish(
        campaign_id,
        ResearchEvent(
            type="failed",
            research_id=research_id,
            data={
                "listing_id": listing_id,
                "error": error_message,
            },
            timestamp=time.monotonic(),
        ),
    )
    research_broker.decrement_active(campaign_id)

    await queries.add_activity(
        campaign_id,
        "research_error",
        f"Area research failed: {error_message}",
        metadata={
            "research_id": research_id,
            "listing_id": listing_id,
        },
    )

    logger.error("Research {} failed: {}", research_id, error_message)
