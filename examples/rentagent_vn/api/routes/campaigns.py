"""Campaign, listing, scan, and activity REST endpoints."""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse

from examples.rentagent_vn.api.brokers import scan_broker
from examples.rentagent_vn.api.models import (
    ActivityResponse,
    CampaignResponse,
    CreateCampaignRequest,
    ListingResponse,
    ScanResponse,
    StatsResponse,
    TriggerScanRequest,
    UpdateCampaignRequest,
    UpdateListingRequest,
)
from examples.rentagent_vn.db import queries

router = APIRouter(prefix="/api/v1", tags=["campaigns"])


# ---------------------------------------------------------------------------
# Campaigns
# ---------------------------------------------------------------------------


@router.post("/campaigns", response_model=CampaignResponse, status_code=201)
async def create_campaign(body: CreateCampaignRequest) -> Any:
    campaign = await queries.create_campaign(
        name=body.name,
        preferences=body.preferences,
        sources=body.sources,
        scan_frequency=body.scan_frequency,
    )
    return campaign


@router.get("/campaigns", response_model=list[CampaignResponse])
async def list_campaigns() -> Any:
    return await queries.list_campaigns()


@router.get("/campaigns/{campaign_id}", response_model=CampaignResponse)
async def get_campaign(campaign_id: str) -> Any:
    campaign = await queries.get_campaign(campaign_id)
    if not campaign:
        raise HTTPException(404, "Campaign not found")
    return campaign


@router.patch("/campaigns/{campaign_id}", response_model=CampaignResponse)
async def update_campaign(campaign_id: str, body: UpdateCampaignRequest) -> Any:
    updates = body.model_dump(exclude_none=True)
    if not updates:
        campaign = await queries.get_campaign(campaign_id)
        if not campaign:
            raise HTTPException(404, "Campaign not found")
        return campaign
    result = await queries.update_campaign(campaign_id, **updates)
    if not result:
        raise HTTPException(404, "Campaign not found")
    return result


# ---------------------------------------------------------------------------
# Listings
# ---------------------------------------------------------------------------


@router.get("/campaigns/{campaign_id}/listings", response_model=list[ListingResponse])
async def get_listings(
    campaign_id: str,
    stage: str | None = Query(None),
) -> Any:
    return await queries.get_listings(campaign_id, stage=stage)


@router.get("/campaigns/{campaign_id}/listings/{listing_id}", response_model=ListingResponse)
async def get_listing(campaign_id: str, listing_id: str) -> Any:
    listing = await queries.get_listing(listing_id)
    if not listing or listing.get("campaign_id") != campaign_id:
        raise HTTPException(404, "Listing not found")
    return listing


@router.patch("/campaigns/{campaign_id}/listings/{listing_id}", response_model=ListingResponse)
async def update_listing(campaign_id: str, listing_id: str, body: UpdateListingRequest) -> Any:
    listing = await queries.get_listing(listing_id)
    if not listing or listing.get("campaign_id") != campaign_id:
        raise HTTPException(404, "Listing not found")

    result = listing
    if body.stage is not None:
        result = await queries.update_listing_stage(listing_id, body.stage, body.skip_reason)
    if body.user_notes is not None:
        result = await queries.update_listing_notes(listing_id, body.user_notes)

    return result


# ---------------------------------------------------------------------------
# Scans
# ---------------------------------------------------------------------------


@router.post("/campaigns/{campaign_id}/scan", response_model=ScanResponse, status_code=201)
async def trigger_scan(campaign_id: str, body: TriggerScanRequest | None = None) -> Any:
    """Trigger a manual scan for the campaign.

    This creates a scan record and sends a message to the agent via the
    WebSocket message bus to start the scrape job. The actual scanning
    happens asynchronously via the background scrape runner.
    """
    campaign = await queries.get_campaign(campaign_id)
    if not campaign:
        raise HTTPException(404, "Campaign not found")

    # Import here to avoid circular imports at module level
    from examples.rentagent_vn.api.server import get_scan_trigger

    trigger = get_scan_trigger()
    if trigger is None:
        raise HTTPException(503, "Scan trigger not available — agent not running")

    # Build query from preferences if not overridden
    query = None
    if body and body.query:
        query = body.query

    scan = await trigger(campaign_id, query)
    return scan


@router.get("/campaigns/{campaign_id}/scans", response_model=list[ScanResponse])
async def list_scans(campaign_id: str, limit: int = Query(10, le=50)) -> Any:
    return await queries.get_scans(campaign_id, limit=limit)


@router.get("/campaigns/{campaign_id}/scans/{scan_id}/stream")
async def stream_scan_events(campaign_id: str, scan_id: str) -> StreamingResponse:
    """SSE endpoint streaming real-time scan progress events.

    The stream replays all buffered events for late-joining clients, then
    streams new events as they arrive. The stream ends with a 'done' event
    when the scan completes.
    """

    async def event_generator():
        async for event in scan_broker.subscribe(scan_id):
            data = {
                "type": event.type,
                "url": event.url,
                "timestamp": event.timestamp,
                **event.data,
            }
            yield f"data: {json.dumps(data)}\n\n"

        # Send final 'done' event to signal stream end
        yield f"data: {json.dumps({'type': 'done'})}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # Disable nginx buffering
        },
    )


# ---------------------------------------------------------------------------
# Activity
# ---------------------------------------------------------------------------


@router.get("/campaigns/{campaign_id}/activity", response_model=list[ActivityResponse])
async def list_activity(campaign_id: str, limit: int = Query(50, le=200)) -> Any:
    return await queries.get_activities(campaign_id, limit=limit)


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------


@router.get("/campaigns/{campaign_id}/stats", response_model=StatsResponse)
async def get_stats(campaign_id: str) -> Any:
    return await queries.get_campaign_stats(campaign_id)
