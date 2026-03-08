"""Area research REST and SSE endpoints."""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse

from examples.rentagent_vn.api.brokers import research_broker
from examples.rentagent_vn.api.models import (
    AreaResearchResponse,
    TriggerResearchRequest,
    TriggerResearchResponse,
)
from examples.rentagent_vn.db import queries

router = APIRouter(prefix="/api/v1", tags=["research"])


# ---------------------------------------------------------------------------
# Trigger batch research
# ---------------------------------------------------------------------------


@router.post(
    "/campaigns/{campaign_id}/research",
    response_model=TriggerResearchResponse,
    status_code=201,
)
async def trigger_research(campaign_id: str, body: TriggerResearchRequest) -> Any:
    """Start area research for one or more listings.

    Creates area_research records, moves listings to "researching" stage,
    and queues background research jobs.
    """
    campaign = await queries.get_campaign(campaign_id)
    if not campaign:
        raise HTTPException(404, "Campaign not found")

    if not body.listing_ids:
        raise HTTPException(400, "No listing_ids provided")

    # Validate all listings belong to this campaign
    research_ids: list[str] = []
    for listing_id in body.listing_ids:
        listing = await queries.get_listing(listing_id)
        if not listing or listing.get("campaign_id") != campaign_id:
            raise HTTPException(404, f"Listing {listing_id} not found in campaign")

        if not listing.get("address"):
            # Skip listings without addresses — can't research
            continue

        # Create research record
        auto_config = body.auto_outreach.model_dump() if body.auto_outreach else {}
        research = await queries.create_area_research(
            listing_id=listing_id,
            campaign_id=campaign_id,
            criteria=body.criteria,
            auto_outreach_config=auto_config,
        )

        # Move listing to "researching" stage
        await queries.update_listing_stage(listing_id, "researching")
        await queries.link_research_to_listing(listing_id, research["id"])

        research_ids.append(research["id"])

    if not research_ids:
        raise HTTPException(400, "No valid listings to research (all missing addresses)")

    # Queue background jobs
    from examples.rentagent_vn.api.server import get_research_trigger

    trigger = get_research_trigger()
    if trigger:
        for rid in research_ids:
            await trigger(rid, campaign_id)

    await queries.add_activity(
        campaign_id,
        "research_started",
        f"Area research started for {len(research_ids)} listing(s)",
        metadata={"research_ids": research_ids},
    )

    return TriggerResearchResponse(
        research_ids=research_ids,
        status="queued",
        message=f"Research started for {len(research_ids)} listing(s)",
    )


# ---------------------------------------------------------------------------
# Get research results
# ---------------------------------------------------------------------------


@router.get(
    "/campaigns/{campaign_id}/research",
    response_model=list[AreaResearchResponse],
)
async def list_research(
    campaign_id: str,
    status: str | None = Query(None),
) -> Any:
    """List area research records for a campaign."""
    return await queries.list_research(campaign_id, status=status)


# ---------------------------------------------------------------------------
# SSE stream — MUST be defined before /{research_id} to avoid path capture
# ---------------------------------------------------------------------------


@router.get("/campaigns/{campaign_id}/research/stream")
async def stream_research_events(campaign_id: str) -> StreamingResponse:
    """SSE endpoint streaming real-time research progress events.

    Replays buffered events for late-joining clients, then streams
    new events as they arrive.
    """

    async def event_generator():
        async for event in research_broker.subscribe(campaign_id):
            data = {
                "type": event.type,
                "research_id": event.research_id,
                "timestamp": event.timestamp,
                **event.data,
            }
            yield f"data: {json.dumps(data)}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ---------------------------------------------------------------------------
# Get single research (after /stream to avoid path param capture)
# ---------------------------------------------------------------------------


@router.get(
    "/campaigns/{campaign_id}/research/{research_id}",
    response_model=AreaResearchResponse,
)
async def get_research(campaign_id: str, research_id: str) -> Any:
    """Get a single area research record."""
    research = await queries.get_area_research(research_id)
    if not research or research.get("campaign_id") != campaign_id:
        raise HTTPException(404, "Research not found")
    return research


# ---------------------------------------------------------------------------
# Retry
# ---------------------------------------------------------------------------


@router.post(
    "/campaigns/{campaign_id}/research/{research_id}/retry",
    response_model=AreaResearchResponse,
)
async def retry_research(campaign_id: str, research_id: str) -> Any:
    """Reset a failed research job and re-queue it."""
    research = await queries.get_area_research(research_id)
    if not research or research.get("campaign_id") != campaign_id:
        raise HTTPException(404, "Research not found")

    if research["status"] not in ("failed", "done"):
        raise HTTPException(400, "Can only retry failed or completed research")

    updated = await queries.update_research_status(research_id, "queued")

    from examples.rentagent_vn.api.server import get_research_trigger

    trigger = get_research_trigger()
    if trigger:
        await trigger(research_id, campaign_id)

    return updated
