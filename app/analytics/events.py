from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass(slots=True)
class AnalyticsEvent:
    name: str
    payload: dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class EventTracker:
    def __init__(self) -> None:
        self._events: list[AnalyticsEvent] = []

    def track(self, name: str, payload: dict[str, Any] | None = None) -> AnalyticsEvent:
        event = AnalyticsEvent(name=name, payload=payload or {})
        self._events.append(event)
        return event

    def list_events(self) -> list[AnalyticsEvent]:
        return list(self._events)
