"""Concrete event brokers for scan and research SSE streams."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from examples.rentagent_vn.api.broker import EventBroker


@dataclass
class ScanEvent:
    """A single event in the scan stream."""

    type: str  # started | progress | streaming_url | url_complete | error | complete
    url: str | None
    data: dict[str, Any]
    timestamp: float = field(default_factory=time.monotonic)


@dataclass
class ResearchEvent:
    """A single event in the research stream."""

    type: str  # started | progress | streaming_url | completed | failed | done
    research_id: str | None
    data: dict[str, Any]
    timestamp: float = field(default_factory=time.monotonic)


class _ResearchBroker(EventBroker[ResearchEvent]):
    """Research broker with auto-done when active_count reaches 0."""

    def _make_done_event(self, stream_id: str) -> ResearchEvent:
        return ResearchEvent(type="done", research_id=None, data={})


# Module-level singletons
scan_broker: EventBroker[ScanEvent] = EventBroker(done_event_type="complete")
research_broker = _ResearchBroker(done_event_type="done", track_active=True)
