from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from langclaw import LangclawContext

if TYPE_CHECKING:
    from examples.rentagent_vn.runners import BackgroundResearchRunner, BackgroundScrapeRunner


@dataclass(kw_only=True)
class RentAgentContext(LangclawContext):
    scrape_runner: BackgroundScrapeRunner
    rental_urls: list[str]
    research_runner: BackgroundResearchRunner | None = field(default=None)
