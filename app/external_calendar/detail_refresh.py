from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.time import utcnow
from app.external_calendar.http import SourceHTTPClient
from app.external_calendar.models import ExternalCalendarEvent, ExternalEventObservation
from app.external_calendar.programme import (
    failure_backoff,
    parse_love_racing_programme,
    programme_refresh_due,
)
from app.external_calendar.service import ProviderObservation, reconcile_observation


def due_love_racing_events(db: Session, *, now=None) -> list[ExternalCalendarEvent]:  # type: ignore[no-untyped-def]
    now = now or utcnow()
    events = list(
        db.scalars(
            select(ExternalCalendarEvent)
            .join(ExternalEventObservation)
            .where(
                ExternalEventObservation.provider == "LOVE_RACING",
                ExternalEventObservation.provider_event_id.is_not(None),
                ExternalCalendarEvent.event_kind == "RACE",
                ExternalCalendarEvent.event_date >= now.astimezone(get_settings().timezone).date(),
            )
            .distinct()
            .order_by(ExternalCalendarEvent.event_date)
        )
    )
    return [
        event
        for event in events
        if programme_refresh_due(
            event.event_date,
            event.detail_checked_at,
            event.programme_status,
            now.astimezone(get_settings().timezone),
            next_due=event.next_detail_due_at,
        )[0]
    ]


def refresh_love_racing_programme(
    db: Session,
    event: ExternalCalendarEvent,
    *,
    client: SourceHTTPClient | None = None,
) -> str:
    identity = db.scalar(
        select(ExternalEventObservation)
        .where(
            ExternalEventObservation.event_id == event.id,
            ExternalEventObservation.provider == "LOVE_RACING",
            ExternalEventObservation.provider_event_id.is_not(None),
        )
        .order_by(ExternalEventObservation.retrieved_at.desc())
    )
    if not identity or not identity.provider_event_id:
        raise ValueError("Love Racing stable meeting identity is unavailable.")
    settings = get_settings()
    client = client or SourceHTTPClient(
        timeout=settings.racing_source_timeout_seconds,
        max_bytes=settings.racing_source_max_bytes,
    )
    now = utcnow()
    url = f"https://loveracing.nz/RaceInfo/{identity.provider_event_id}/Meeting-Overview.aspx"
    try:
        response = client.get(url, accept="text/html")
        programme = parse_love_racing_programme(response.text)
        facts: dict[str, object] = {
            "programme_status": programme.status,
            "race_count": programme.race_count,
            "first_race_time": programme.first_race_time,
            "last_race_time": programme.last_race_time,
        }
        if programme.meeting_name:
            facts["meeting_name"] = programme.meeting_name
        _event, outcome = reconcile_observation(
            db,
            ProviderObservation(
                provider="LOVE_RACING",
                provider_event_id=identity.provider_event_id,
                event_date=event.event_date,
                source_track_name=identity.source_track_name,
                discipline=event.discipline,
                event_kind=event.event_kind,
                facts=facts,
                raw_payload={
                    "meeting_url": url,
                    "races": [
                        {
                            "number": number,
                            "scheduled_start": value.strftime("%H:%M") if value else None,
                        }
                        for number, value in programme.races
                    ],
                    "diagnostics": list(programme.diagnostics),
                    "content_hash": programme.content_hash,
                },
                retrieved_at=now,
            ),
        )
        event.detail_checked_at = now
        event.detail_failure_count = 0
        event.next_detail_due_at = None
        event.latest_detail_error = None
        db.flush()
        return outcome
    except Exception as exc:
        event.detail_checked_at = now
        event.detail_failure_count += 1
        event.next_detail_due_at = now + failure_backoff(event.detail_failure_count)
        event.latest_detail_error = f"{type(exc).__name__}: {str(exc)[:400]}"
        db.flush()
        return "ERROR"


def refresh_due_programmes(db: Session, *, now=None, limit: int = 20) -> dict[str, int]:  # type: ignore[no-untyped-def]
    counts = {"checked": 0, "updated": 0, "failed": 0}
    for event in due_love_racing_events(db, now=now)[:limit]:
        outcome = refresh_love_racing_programme(db, event)
        counts["checked"] += 1
        if outcome == "ERROR":
            counts["failed"] += 1
        elif outcome in {"ENRICHED", "MATCHED", "CREATED"}:
            counts["updated"] += 1
    db.commit()
    return counts
