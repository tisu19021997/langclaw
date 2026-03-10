"""Zalo integration API routes.

Proxy endpoints forward requests to the Zalo Node.js service.
Outreach endpoints handle message drafting and sending.
"""

from __future__ import annotations

import os
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException
from loguru import logger

from examples.rentagent_vn.api.models import (
    DraftOutreachRequest,
    OutreachMessageResponse,
    SendOutreachRequest,
    ZaloAuthCookieRequest,
    ZaloStatusResponse,
)
from examples.rentagent_vn.db import queries
from examples.rentagent_vn.outreach import draft_outreach_message

router = APIRouter(prefix="/api/v1", tags=["zalo"])

ZALO_SERVICE_URL = os.environ.get("ZALO_SERVICE_URL", "http://localhost:8001")


# ---------------------------------------------------------------------------
# Zalo Service Proxy Endpoints
# ---------------------------------------------------------------------------


async def _proxy_to_zalo(
    method: str,
    path: str,
    json_body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Proxy a request to the Zalo Node.js service."""
    url = f"{ZALO_SERVICE_URL}{path}"
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            if method == "GET":
                resp = await client.get(url)
            elif method == "POST":
                resp = await client.post(url, json=json_body)
            else:
                raise ValueError(f"Unsupported method: {method}")

            if resp.status_code >= 400:
                error_data = resp.json() if resp.text else {"error": "Unknown error"}
                raise HTTPException(status_code=resp.status_code, detail=error_data)

            return resp.json()
    except httpx.ConnectError:
        raise HTTPException(
            status_code=503,
            detail={"error": "Zalo service unavailable. Is it running on :8001?"},
        )
    except httpx.TimeoutException:
        raise HTTPException(
            status_code=504,
            detail={"error": "Zalo service timeout"},
        )


@router.post("/zalo/auth/cookie", response_model=ZaloStatusResponse)
async def connect_zalo_cookie(body: ZaloAuthCookieRequest) -> Any:
    """Connect to Zalo using cookie credentials."""
    result = await _proxy_to_zalo(
        "POST",
        "/auth/cookie",
        {
            "cookie": body.cookie,
            "imei": body.imei,
            "userAgent": body.user_agent,
        },
    )
    return result


@router.post("/zalo/auth/qr")
async def connect_zalo_qr() -> dict[str, Any]:
    """Generate QR code for Zalo login."""
    result = await _proxy_to_zalo("POST", "/auth/qr", {"qrPath": "./qr.png"})
    return result


@router.get("/zalo/status", response_model=ZaloStatusResponse)
async def get_zalo_status() -> Any:
    """Get current Zalo connection status."""
    try:
        result = await _proxy_to_zalo("GET", "/auth/status")
        return result
    except HTTPException as e:
        if e.status_code == 503:
            return {
                "connected": False,
                "phone_number": None,
                "error": "Service unavailable",
            }
        raise


@router.post("/zalo/logout", response_model=ZaloStatusResponse)
async def disconnect_zalo() -> Any:
    """Disconnect from Zalo."""
    result = await _proxy_to_zalo("POST", "/auth/logout")
    return result


# ---------------------------------------------------------------------------
# Outreach Endpoints
# ---------------------------------------------------------------------------


@router.post(
    "/campaigns/{campaign_id}/listings/{listing_id}/outreach",
    response_model=OutreachMessageResponse,
    status_code=201,
)
async def create_outreach_draft(
    campaign_id: str,
    listing_id: str,
    body: DraftOutreachRequest | None = None,
) -> Any:
    """Draft an outreach message for a listing using LLM."""
    listing = await queries.get_listing(listing_id)
    if not listing or listing.get("campaign_id") != campaign_id:
        raise HTTPException(status_code=404, detail="Listing not found")

    campaign = await queries.get_campaign(campaign_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")

    custom_notes = body.custom_notes if body else None

    logger.info(f"Drafting outreach for listing {listing_id}")
    draft_text = await draft_outreach_message(listing, campaign, custom_notes)

    message = await queries.create_outreach_message(
        listing_id=listing_id,
        campaign_id=campaign_id,
        draft_text=draft_text,
        landlord_phone=listing.get("landlord_phone"),
    )

    return message


@router.post(
    "/campaigns/{campaign_id}/listings/{listing_id}/outreach/send",
    response_model=OutreachMessageResponse,
)
async def send_outreach_message(
    campaign_id: str,
    listing_id: str,
    body: SendOutreachRequest,
) -> Any:
    """Send an outreach message via Zalo."""
    listing = await queries.get_listing(listing_id)
    if not listing or listing.get("campaign_id") != campaign_id:
        raise HTTPException(status_code=404, detail="Listing not found")

    message = await queries.get_outreach_message(body.message_id)
    if not message or message.get("listing_id") != listing_id:
        raise HTTPException(status_code=404, detail="Outreach message not found")

    phone = listing.get("landlord_phone")
    if not phone:
        raise HTTPException(status_code=400, detail="Listing has no landlord phone")

    final_text = body.final_text or message.get("draft_text")
    if not final_text:
        raise HTTPException(status_code=400, detail="No message text to send")

    # Check Zalo status first
    try:
        status = await _proxy_to_zalo("GET", "/auth/status")
        if not status.get("connected"):
            raise HTTPException(
                status_code=400,
                detail={
                    "error": "Zalo not connected",
                    "code": "ZALO_NOT_CONNECTED",
                },
            )
    except HTTPException as e:
        if e.status_code == 503:
            raise HTTPException(
                status_code=503,
                detail={
                    "error": "Zalo service unavailable",
                    "code": "SERVICE_UNAVAILABLE",
                },
            )
        raise

    # Send message via Zalo
    try:
        send_result = await _proxy_to_zalo(
            "POST",
            "/message/send",
            {"phone": phone, "text": final_text},
        )
        zalo_user_id = send_result.get("userId")

        # Update outreach status to sent
        updated = await queries.update_outreach_status(
            message_id=body.message_id,
            status="sent",
            final_text=final_text,
            zalo_user_id=zalo_user_id,
        )

        # Update listing stage to contacted
        await queries.update_listing_stage(listing_id, "contacted")

        # Add activity log
        await queries.add_activity(
            campaign_id=campaign_id,
            event_type="outreach_sent",
            message=f"Sent outreach message to {phone}",
            metadata={
                "listing_id": listing_id,
                "message_id": body.message_id,
            },
        )

        logger.info(f"Outreach sent for listing {listing_id} to {phone}")
        return updated

    except HTTPException as e:
        error_msg = str(e.detail) if hasattr(e, "detail") else str(e)
        await queries.update_outreach_status(
            message_id=body.message_id,
            status="failed",
            error_message=error_msg,
        )
        raise


@router.get(
    "/campaigns/{campaign_id}/listings/{listing_id}/outreach",
    response_model=list[OutreachMessageResponse],
)
async def get_outreach_history(campaign_id: str, listing_id: str) -> Any:
    """Get outreach history for a listing."""
    listing = await queries.get_listing(listing_id)
    if not listing or listing.get("campaign_id") != campaign_id:
        raise HTTPException(status_code=404, detail="Listing not found")

    messages = await queries.get_outreach_for_listing(listing_id)
    return messages
