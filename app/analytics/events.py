from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import AnalyticsEvent, Channel, Digest, Post, User


DEFAULT_DASHBOARD_PERIOD_DAYS = 30
RECENT_EVENTS_LIMIT = 20

FUNNEL_STEPS: tuple[tuple[str, str], ...] = (
    ("signup_completed", "Web login completed"),
    ("telegram_linked", "Telegram link generated"),
    ("channels_selected", "Channel selected"),
    ("digest_schedule_created", "Digest period selected"),
    ("first_digest_generated", "First digest generated"),
    ("first_digest_opened", "First digest opened"),
    ("first_rag_query", "First RAG query"),
)


@dataclass(slots=True)
class KpiMetric:
    label: str
    value: int
    helper: str = ""


@dataclass(slots=True)
class DailyUsage:
    day: str
    events_count: int
    active_users: int


@dataclass(slots=True)
class FunnelStep:
    event_name: str
    label: str
    users_count: int
    conversion_percent: float


@dataclass(slots=True)
class RecentEvent:
    name: str
    source: str
    user_id: int | None
    occurred_at_text: str
    payload_text: str


@dataclass(slots=True)
class AnalyticsDashboard:
    period_days: int
    kpis: list[KpiMetric]
    daily_usage: list[DailyUsage]
    funnel_steps: list[FunnelStep]
    recent_events: list[RecentEvent]
    max_daily_events: int


class AnalyticsService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def track(
        self,
        name: str,
        user_id: int | None = None,
        source: str = "system",
        payload: dict[str, Any] | None = None,
    ) -> AnalyticsEvent:
        event = AnalyticsEvent(
            name=name.strip(),
            user_id=user_id,
            source=source.strip() or "system",
            payload_json=_dump_payload(payload or {}),
            occurred_at=datetime.now(timezone.utc),
        )
        self.session.add(event)
        self.session.commit()
        self.session.refresh(event)
        return event

    def track_once(
        self,
        name: str,
        user_id: int,
        source: str = "system",
        payload: dict[str, Any] | None = None,
    ) -> AnalyticsEvent | None:
        existing_event = self.session.scalar(
            select(AnalyticsEvent).where(
                AnalyticsEvent.name == name,
                AnalyticsEvent.user_id == user_id,
            )
        )
        if existing_event is not None:
            return None
        return self.track(name=name, user_id=user_id, source=source, payload=payload)

    def get_dashboard(self, period_days: int = DEFAULT_DASHBOARD_PERIOD_DAYS) -> AnalyticsDashboard:
        normalized_period_days = max(int(period_days or DEFAULT_DASHBOARD_PERIOD_DAYS), 1)
        since = datetime.now(timezone.utc) - timedelta(days=normalized_period_days)
        events = list(
            self.session.scalars(
                select(AnalyticsEvent)
                .where(AnalyticsEvent.occurred_at >= since)
                .order_by(AnalyticsEvent.occurred_at.asc(), AnalyticsEvent.id.asc())
            )
        )
        daily_usage = self._build_daily_usage(events, normalized_period_days)
        max_daily_events = max((item.events_count for item in daily_usage), default=0)

        return AnalyticsDashboard(
            period_days=normalized_period_days,
            kpis=self._build_kpis(since),
            daily_usage=daily_usage,
            funnel_steps=self._build_funnel_steps(since),
            recent_events=self._list_recent_events(),
            max_daily_events=max_daily_events,
        )

    def _build_kpis(self, since: datetime) -> list[KpiMetric]:
        users_count = _scalar_count(self.session, select(func.count(User.id)))
        active_users_count = _scalar_count(
            self.session,
            select(func.count(func.distinct(AnalyticsEvent.user_id))).where(
                AnalyticsEvent.user_id.is_not(None),
                AnalyticsEvent.occurred_at >= since,
            ),
        )
        channels_count = _scalar_count(self.session, select(func.count(Channel.id)))
        posts_count = _scalar_count(self.session, select(func.count(Post.id)))
        digests_count = _scalar_count(self.session, select(func.count(Digest.id)))
        rag_queries_count = _scalar_count(
            self.session,
            select(func.count(AnalyticsEvent.id)).where(
                AnalyticsEvent.name == "rag_query",
                AnalyticsEvent.occurred_at >= since,
            ),
        )
        voice_queries_count = _scalar_count(
            self.session,
            select(func.count(AnalyticsEvent.id)).where(
                AnalyticsEvent.name == "voice_query_transcribed",
                AnalyticsEvent.occurred_at >= since,
            ),
        )
        return [
            KpiMetric("Users", users_count, "total"),
            KpiMetric("Active users", active_users_count, "with events in period"),
            KpiMetric("Channels", channels_count, "catalog and user-added"),
            KpiMetric("Posts", posts_count, "stored"),
            KpiMetric("Digests", digests_count, "generated"),
            KpiMetric("RAG queries", rag_queries_count, "web and bot"),
            KpiMetric("Voice queries", voice_queries_count, "transcribed"),
        ]

    def _build_daily_usage(
        self,
        events: list[AnalyticsEvent],
        period_days: int,
    ) -> list[DailyUsage]:
        today = datetime.now(timezone.utc).date()
        days = [today - timedelta(days=offset) for offset in range(period_days - 1, -1, -1)]
        event_counts = {day.isoformat(): 0 for day in days}
        active_user_ids = {day.isoformat(): set() for day in days}

        for event in events:
            occurred_at = _coerce_utc(event.occurred_at)
            day_key = occurred_at.date().isoformat()
            if day_key not in event_counts:
                continue
            event_counts[day_key] += 1
            if event.user_id is not None:
                active_user_ids[day_key].add(event.user_id)

        return [
            DailyUsage(
                day=day.isoformat(),
                events_count=event_counts[day.isoformat()],
                active_users=len(active_user_ids[day.isoformat()]),
            )
            for day in days
        ]

    def _build_funnel_steps(self, since: datetime) -> list[FunnelStep]:
        steps: list[FunnelStep] = []
        previous_count: int | None = None
        for event_name, label in FUNNEL_STEPS:
            users_count = _scalar_count(
                self.session,
                select(func.count(func.distinct(AnalyticsEvent.user_id))).where(
                    AnalyticsEvent.name == event_name,
                    AnalyticsEvent.user_id.is_not(None),
                    AnalyticsEvent.occurred_at >= since,
                ),
            )
            conversion_percent = 100.0
            if previous_count is not None:
                conversion_percent = (users_count / previous_count * 100.0) if previous_count else 0.0
            steps.append(
                FunnelStep(
                    event_name=event_name,
                    label=label,
                    users_count=users_count,
                    conversion_percent=round(conversion_percent, 1),
                )
            )
            previous_count = users_count
        return steps

    def _list_recent_events(self) -> list[RecentEvent]:
        events = list(
            self.session.scalars(
                select(AnalyticsEvent)
                .order_by(AnalyticsEvent.occurred_at.desc(), AnalyticsEvent.id.desc())
                .limit(RECENT_EVENTS_LIMIT)
            )
        )
        return [
            RecentEvent(
                name=event.name,
                source=event.source,
                user_id=event.user_id,
                occurred_at_text=_coerce_utc(event.occurred_at).strftime("%Y-%m-%d %H:%M UTC"),
                payload_text=_compact_payload(event.payload_json),
            )
            for event in events
        ]


def _scalar_count(session: Session, statement) -> int:
    value = session.scalar(statement)
    return int(value or 0)


def _dump_payload(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, default=_json_default)


def _compact_payload(payload_json: str, limit: int = 140) -> str:
    payload = payload_json.strip() if payload_json else "{}"
    if len(payload) <= limit:
        return payload
    return f"{payload[: limit - 3].rstrip()}..."


def _json_default(value: Any) -> str:
    if isinstance(value, datetime):
        return _coerce_utc(value).isoformat()
    return str(value)


def _coerce_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
