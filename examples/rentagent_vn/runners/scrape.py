"""Background scrape runner — multi-URL fan-out using TinyFish SSE."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from loguru import logger

from examples.rentagent_vn.models import (
    ListingSummary,
    ScrapeResult,
    TinyFishListingResponse,
)
from examples.rentagent_vn.prompts import build_goal
from examples.rentagent_vn.runners.base import (
    BaseTinyFishRunner,
    ErrorCallback,
    ProgressCallback,
    StreamingUrlCallback,
)
from examples.rentagent_vn.tinyfish.client import TinyFishClient
from langclaw import Langclaw

ScrapeResultCallback = Callable[
    [Langclaw, str, ScrapeResult, dict[str, Any]],
    Awaitable[None],
]


class BackgroundScrapeRunner(BaseTinyFishRunner):
    """Runs multi-URL scrape jobs in the background using TinyFish.

    Args:
        app: The Langclaw application instance.
        result_callback: Called with aggregated ScrapeResult when all URLs finish.
        tinyfish_client: TinyFish SSE streaming client.
        progress_callback: Called on PROGRESS events.
        streaming_url_callback: Called on STREAMING_URL events.
        error_callback: Called on ERROR events.
    """

    def __init__(
        self,
        app: Langclaw,
        result_callback: ScrapeResultCallback,
        tinyfish_client: TinyFishClient,
        *,
        progress_callback: ProgressCallback | None = None,
        streaming_url_callback: StreamingUrlCallback | None = None,
        error_callback: ErrorCallback | None = None,
    ) -> None:
        super().__init__(
            app,
            tinyfish_client,
            progress_callback=progress_callback,
            streaming_url_callback=streaming_url_callback,
            error_callback=error_callback,
        )
        self._result_callback = result_callback

    async def start(
        self,
        urls: list[str],
        query: str,
        channel_context: dict[str, Any],
        user_preference: str | None = None,
    ) -> str:
        """Start a background scrape job. Returns job_id."""
        job_id = self._generate_job_id()
        task = asyncio.create_task(
            self._run(job_id, urls, query, user_preference, channel_context),
            name=f"scrape-{job_id}",
        )
        self._tasks[job_id] = task
        logger.info(f"Background scrape {job_id} started — {len(urls)} URLs")
        return job_id

    async def _run(
        self,
        job_id: str,
        urls: list[str],
        query: str,
        user_preference: str | None,
        channel_context: dict[str, Any],
    ) -> None:
        all_listings: list[ListingSummary] = []
        all_errors: list[dict[str, Any]] = []

        try:

            async def _stream_run(url: str) -> None:
                goal = build_goal(url, query, user_preference)
                logger.debug("TinyFish goal for {}: {}", url, goal[:200])

                async for event in self._tinyfish_client.stream_run(url, goal):
                    logger.debug(f"TinyFish event: {event.type}")

                    if event.type in ("PROGRESS", "STREAMING_URL"):
                        await self._dispatch_event(
                            event,
                            id_primary=job_id,
                            id_secondary=event.run_id,
                            id_tertiary=url,
                            channel_context=channel_context,
                        )
                    elif event.type == "COMPLETE":
                        validated = TinyFishListingResponse.from_raw(event.result_json or {})
                        all_listings.extend(validated.listings)
                        logger.info(
                            "URL {} returned {} listings",
                            url,
                            len(validated.listings),
                        )
                    elif event.type == "ERROR":
                        all_errors.append({"url": url, "error": event.message or "Unknown error"})
                        if self._error_callback:
                            await self._error_callback(
                                self._app,
                                job_id,
                                event.run_id,
                                url,
                                event.message,
                                channel_context,
                            )

            tasks = [_stream_run(url) for url in urls]
            await asyncio.gather(*tasks)

        except Exception:
            logger.exception(f"Background scrape {job_id} failed")
            all_errors.append({"error": f"Background scrape {job_id} failed"})

        # Deliver aggregated results in a single callback.
        combined = ScrapeResult(
            listings=all_listings,
            errors=all_errors,
            urls_scanned=len(urls),
        )
        logger.info(
            "Background scrape {} finished — {} listings, {} errors",
            job_id,
            len(combined.listings),
            len(combined.errors),
        )
        try:
            await self._result_callback(self._app, job_id, combined, channel_context)
        except Exception:
            logger.exception("Failed to deliver result callback for {}", job_id)
        finally:
            self._tasks.pop(job_id, None)
