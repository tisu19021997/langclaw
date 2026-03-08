"""Start all RentAgent VN services: Langclaw agent, FastAPI REST API, and Zalo service.

Usage:
    python -m examples.rentagent_vn.run_all
"""

from __future__ import annotations

import asyncio
import subprocess
import time
from pathlib import Path
from typing import Any

import uvicorn
from loguru import logger

from examples.rentagent_vn.api.brokers import ScanEvent, scan_broker
from examples.rentagent_vn.api.server import (
    create_api_app,
    set_research_trigger,
    set_scan_trigger,
)
from examples.rentagent_vn.db import queries
from examples.rentagent_vn.db.connection import init_db

# Global reference to Zalo subprocess for cleanup
_zalo_process: subprocess.Popen[bytes] | None = None


async def _build_scan_trigger(app_module: Any) -> Any:
    """Create a scan trigger function that bridges the API to the agent's scrape runner."""

    async def trigger_scan(campaign_id: str, query_override: str | None = None) -> dict[str, Any]:
        campaign = await queries.get_campaign(campaign_id)
        if not campaign:
            raise ValueError(f"Campaign {campaign_id} not found")

        prefs = campaign.get("preferences", {})
        sources = campaign.get("sources", [])

        if not sources:
            sources = ["https://www.facebook.com/groups/1930421007111976/"]

        # Build query from preferences if not overridden
        query = query_override
        if not query:
            parts: list[str] = []
            if prefs.get("district"):
                parts.append(prefs["district"])
            if prefs.get("bedrooms"):
                parts.append(f"{prefs['bedrooms']} phòng ngủ")
            if prefs.get("max_price"):
                parts.append(f"dưới {prefs['max_price']}")
            if prefs.get("property_type"):
                parts.append(prefs["property_type"])
            query = ", ".join(parts) if parts else "phòng trọ cho thuê"

        # Create scan record in DB
        runner = app_module.scrape_runner
        job_id_placeholder = "pending"
        scan = await queries.create_scan(campaign_id, job_id_placeholder)
        scan_id = scan["id"]

        # Add to activity log
        await queries.add_activity(
            campaign_id,
            "scan_start",
            f"Bắt đầu quét {len(sources)} nguồn...",
            scan_id=scan_id,
        )

        # Channel context for the background runner to deliver results
        channel_context: dict[str, Any] = {
            "channel": "websocket",
            "user_id": "tisu1902",
            "context_id": campaign_id,
            "chat_id": f"web-user:{campaign_id}",
            "metadata": {
                "campaign_id": campaign_id,
                "scan_id": scan_id,
            },
        }

        job_id = await runner.start(
            urls=sources,
            query=query,
            channel_context=channel_context,
            user_preference=str(prefs) if prefs else None,
        )

        # Publish 'started' event to scan broker for SSE streaming
        scan_broker.publish(
            scan_id,
            ScanEvent(
                type="started",
                url=None,
                data={"job_id": job_id, "urls": sources, "total_urls": len(sources)},
                timestamp=time.monotonic(),
            ),
        )

        # Update scan with actual job_id
        db = await queries.get_db()
        await db.execute("UPDATE scans SET job_id = ? WHERE id = ?", (job_id, scan_id))
        await db.commit()

        logger.info("Triggered scan {} (job {}) for campaign {}", scan_id, job_id, campaign_id)
        return {
            "id": scan_id,
            "campaign_id": campaign_id,
            "job_id": job_id,
            "status": "running",
            "started_at": scan["started_at"],
        }

    return trigger_scan


async def _build_research_trigger(app_module: Any) -> Any:
    """Create a research trigger that bridges the API to the research runner."""

    async def trigger_research(research_id: str, campaign_id: str) -> None:
        research = await queries.get_area_research(research_id)
        if not research:
            logger.error("Research {} not found, cannot trigger", research_id)
            return

        listing = await queries.get_listing(research["listing_id"])
        if not listing:
            logger.error(
                "Listing {} not found for research {}",
                research["listing_id"],
                research_id,
            )
            return

        address = listing.get("address", "")
        if not address:
            logger.warning(
                "Listing {} has no address, skipping research {}",
                research["listing_id"],
                research_id,
            )
            await queries.update_research_status(
                research_id, "failed", error_message="Listing has no address"
            )
            return

        criteria = research.get("criteria", [])
        if not criteria:
            from examples.rentagent_vn.models import RESEARCH_CRITERIA_KEYS

            criteria = list(RESEARCH_CRITERIA_KEYS)

        channel_context: dict[str, Any] = {
            "channel": "websocket",
            "user_id": "tisu1902",
            "context_id": campaign_id,
            "chat_id": f"web-user:{campaign_id}",
            "metadata": {
                "campaign_id": campaign_id,
                "research_id": research_id,
            },
        }

        runner = app_module.research_runner
        await runner.start(
            research_id=research_id,
            listing_id=research["listing_id"],
            address=address,
            criteria=criteria,
            campaign_id=campaign_id,
            channel_context=channel_context,
        )

    return trigger_research


def _start_zalo_service() -> subprocess.Popen[bytes] | None:
    """Start the Zalo Node.js service if available.

    Returns the subprocess handle or None if not available.
    """
    global _zalo_process

    zalo_service_dir = Path(__file__).parent / "zalo-service"
    node_modules = zalo_service_dir / "node_modules"

    if not zalo_service_dir.exists():
        logger.info("Zalo service directory not found, skipping")
        return None

    if not node_modules.exists():
        logger.info(
            "Zalo service not installed (no node_modules). Run: cd {} && npm install",
            zalo_service_dir,
        )
        return None

    try:
        _zalo_process = subprocess.Popen(
            ["node", "index.js"],
            cwd=zalo_service_dir,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        logger.info("Zalo service started on :8001 (pid={})", _zalo_process.pid)
        return _zalo_process
    except FileNotFoundError:
        logger.warning("Node.js not found, Zalo service not started")
        return None
    except Exception as exc:
        logger.warning("Failed to start Zalo service: {}", exc)
        return None


def _stop_zalo_service() -> None:
    """Stop the Zalo service subprocess if running."""
    global _zalo_process

    if _zalo_process is not None:
        logger.info("Stopping Zalo service (pid={})", _zalo_process.pid)
        _zalo_process.terminate()
        try:
            _zalo_process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            _zalo_process.kill()
        _zalo_process = None


async def main() -> None:
    """Start all services."""
    # Start Zalo service (optional)
    zalo_proc = _start_zalo_service()

    try:
        # Initialize database first
        await init_db()

        # Import the app module to access its components
        from examples.rentagent_vn import app as app_module

        # Build and register the scan trigger
        trigger = await _build_scan_trigger(app_module)
        set_scan_trigger(trigger)

        # Build and register the research trigger
        research_trigger = await _build_research_trigger(app_module)
        set_research_trigger(research_trigger)

        # Create FastAPI app (skip lifespan DB init since we already did it)
        api_app = create_api_app()

        # Configure uvicorn for the REST API
        config = uvicorn.Config(
            api_app,
            host="0.0.0.0",
            port=8000,
            log_level="info",
        )
        api_server = uvicorn.Server(config)

        services = ["REST API on :8000", "WS gateway on :18789"]
        if zalo_proc:
            services.append("Zalo service on :8001")
        logger.info("Starting RentAgent VN — {}", ", ".join(services))

        # Run both servers concurrently
        await asyncio.gather(
            api_server.serve(),
            app_module.app._run_async(),
        )
    finally:
        _stop_zalo_service()


if __name__ == "__main__":
    asyncio.run(main())
