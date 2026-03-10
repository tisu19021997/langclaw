"""LLM-powered outreach message drafting for landlord contact."""

from __future__ import annotations

import os
from typing import Any

from dotenv import load_dotenv
from langchain.chat_models import init_chat_model
from loguru import logger

from examples.rentagent_vn.prompts import OUTREACH_DRAFT_PROMPT

load_dotenv()


def _format_price(price_vnd: float | None) -> str:
    """Format VND price to readable string."""
    if not price_vnd:
        return "Contact"
    if price_vnd >= 1_000_000:
        millions = price_vnd / 1_000_000
        if millions == int(millions):
            return f"{int(millions)}M/month"
        return f"{millions:.1f}M/month"
    return f"{price_vnd:,.0f}d/month"


def _format_area(area_sqm: float | None) -> str:
    """Format area to readable string."""
    if not area_sqm:
        return "unknown"
    return f"{area_sqm:.0f}m²"


async def draft_outreach_message(
    listing: dict[str, Any],
    campaign: dict[str, Any] | None = None,
    custom_notes: str | None = None,
) -> str:
    """Draft a personalized outreach message for a listing using LLM.

    Args:
        listing: Listing data dict with address, price_vnd, area_sqm,
            district.
        campaign: Optional campaign data (for future preference-based
            customization).
        custom_notes: Optional custom notes to include in the prompt.

    Returns:
        Draft message text in Vietnamese.
    """
    address = listing.get("address") or listing.get("title") or "this address"
    price = _format_price(listing.get("price_vnd"))
    area = _format_area(listing.get("area_sqm"))
    district = listing.get("district") or "this area"
    landlord_name = listing.get("landlord_name") or "unknown"

    custom_notes_section = ""
    if custom_notes:
        custom_notes_section = f"## Additional notes from tenant:\n{custom_notes}"

    prompt = OUTREACH_DRAFT_PROMPT.format(
        landlord_name=landlord_name,
        address=address,
        price=price,
        area=area,
        district=district,
        custom_notes_section=custom_notes_section,
    )

    model_name = os.environ.get("LANGCLAW__AGENTS__MODEL", "azure_openai:gpt-5")
    llm = init_chat_model(model=model_name, temperature=1.0)

    logger.info(f"Drafting outreach message for listing {listing.get('id')}")

    response = await llm.ainvoke(prompt)
    draft = response.content

    if isinstance(draft, list):
        draft = " ".join(str(part) for part in draft)

    return draft.strip()
