from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.time import utcnow
from app.external_calendar.http import SourceHTTPClient, SourceHTTPError
from app.external_calendar.models import ExternalCalendarEvent, ExternalEventObservation
from app.external_calendar.programme import (
    ProgrammeAccessRestrictedError,
    ProgrammeStructureError,
    failure_backoff,
    parse_love_racing_programme,
    programme_refresh_due,
)
from app.external_calendar.providers.love_racing import love_racing_day_id
from app.external_calendar.service import ProviderObservation, normalized_key, reconcile_observation


@dataclass(frozen=True)
class ProgrammeRefreshResult:
    outcome: str
    programme_status: str | None


def _trusted_programme_destination(url: str, day_id: str) -> bool:
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError:
        return False
    return bool(
        parsed.scheme.casefold() == "https"
        and (parsed.hostname or "").casefold().rstrip(".")
        in {"loveracing.nz", "www.loveracing.nz"}
        and parsed.username is None
        and parsed.password is None
        and port in (None, 443)
        and parsed.path.casefold()
        == f"/raceinfo/{day_id}/meeting-overview.aspx".casefold()
        and not parsed.query
        and not parsed.fragment
    )


def _valid_programme_identity(
    db: Session, event: ExternalCalendarEvent
) -> ExternalEventObservation | None:
    identities = db.scalars(
        select(ExternalEventObservation)
        .where(
            ExternalEventObservation.event_id == event.id,
            ExternalEventObservation.provider == "LOVE_RACING",
            ExternalEventObservation.provider_event_id.is_not(None),
        )
        .order_by(ExternalEventObservation.retrieved_at.desc())
    )
    return next(
        (
            row
            for row in identities
            if love_racing_day_id(row.provider_event_id) is not None
        ),
        None,
    )


def has_valid_programme_identity(db: Session, event: ExternalCalendarEvent) -> bool:
    return _valid_programme_identity(db, event) is not None


def due_love_racing_events(db: Session, *, now=None) -> list[ExternalCalendarEvent]:  # type: ignore[no-untyped-def]
    now = now or utcnow()
    rows = db.execute(
        select(ExternalCalendarEvent, ExternalEventObservation.provider_event_id)
        .join(
            ExternalEventObservation,
            ExternalEventObservation.event_id == ExternalCalendarEvent.id,
        )
        .where(
            ExternalEventObservation.provider == "LOVE_RACING",
            ExternalCalendarEvent.event_kind.in_(("RACE", "TRIAL")),
            ExternalCalendarEvent.event_date >= now.astimezone(get_settings().timezone).date(),
        )
    )
    events_by_id = {
        event.id: event
        for event, provider_event_id in rows
        if love_racing_day_id(provider_event_id) is not None
    }
    events = sorted(
        events_by_id.values(),
        key=lambda event: (
            event.event_date,
            event.detail_checked_at.timestamp() if event.detail_checked_at else float("-inf"),
            str(event.id),
        ),
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
) -> ProgrammeRefreshResult:
    identity = _valid_programme_identity(db, event)
    if not identity or not identity.provider_event_id:
        event.latest_detail_error = (
            "DETAIL_ID_UNAVAILABLE: Official Love Racing meeting identity is unavailable."
        )
        db.flush()
        return ProgrammeRefreshResult("UNAVAILABLE", None)
    day_id = love_racing_day_id(identity.provider_event_id)
    assert day_id is not None
    settings = get_settings()
    client = client or SourceHTTPClient(
        timeout=settings.racing_source_timeout_seconds,
        max_bytes=settings.racing_source_max_bytes,
    )
    now = utcnow()
    url = f"https://loveracing.nz/RaceInfo/{day_id}/Meeting-Overview.aspx"
    try:
        response = client.get(
            url,
            accept="text/html",
            destination_validator=lambda destination: _trusted_programme_destination(
                destination, day_id
            ),
        )
        if response.content_type not in {"text/html", "application/xhtml+xml"}:
            raise ProgrammeStructureError("Official programme response was not HTML.")
        programme = parse_love_racing_programme(
            response.text,
            event_kind=event.event_kind,
            expected_date=event.event_date,
            expected_track_name=identity.source_track_name,
        )
        facts: dict[str, object] = {
            "programme_status": programme.status,
        }
        prefix = "race" if event.event_kind == "RACE" else "trial"
        facts[f"{prefix}_count"] = programme.race_count
        facts[f"first_{prefix}_time"] = (
            programme.first_race_time.strftime("%H:%M") if programme.first_race_time else None
        )
        facts[f"last_{prefix}_time"] = (
            programme.last_race_time.strftime("%H:%M") if programme.last_race_time else None
        )
        if programme.meeting_name:
            facts["meeting_name"] = programme.meeting_name
        _event, outcome = reconcile_observation(
            db,
            ProviderObservation(
                provider="LOVE_RACING",
                provider_event_id=day_id,
                event_date=event.event_date,
                source_track_name=identity.source_track_name,
                discipline=event.discipline,
                event_kind=event.event_kind,
                facts=facts,
                raw_payload={
                    "meeting_url": url,
                    f"{prefix}s": [
                        {
                            "number": number,
                            "scheduled_start": value.strftime("%H:%M") if value else None,
                        }
                        for number, value in programme.races
                    ],
                    "diagnostics": list(programme.diagnostics),
                    "content_hash": programme.content_hash,
                },
                dedupe_payload={
                    "provider_event_id": day_id,
                    "meeting_name": normalized_key(programme.meeting_name or ""),
                    "programme_status": programme.status,
                    f"{prefix}_count": programme.race_count,
                    f"{prefix}s": [
                        {
                            "number": number,
                            "scheduled_start": value.strftime("%H:%M") if value else None,
                        }
                        for number, value in programme.races
                    ],
                },
                retrieved_at=now,
            ),
        )
        event.detail_checked_at = now
        event.detail_failure_count = 0
        event.next_detail_due_at = None
        event.latest_detail_error = None
        db.flush()
        return ProgrammeRefreshResult(outcome, programme.status)
    except Exception as exc:
        event.detail_checked_at = now
        event.detail_failure_count += 1
        event.next_detail_due_at = now + failure_backoff(event.detail_failure_count)
        if isinstance(exc, ProgrammeAccessRestrictedError):
            label = "ACCESS_RESTRICTED"
        elif isinstance(exc, ProgrammeStructureError):
            label = "INVALID_STRUCTURE"
        elif isinstance(exc, SourceHTTPError):
            label = "SOURCE_FAILURE"
        else:
            label = "SOURCE_FAILURE"
        event.latest_detail_error = f"{label}: {str(exc)[:400]}"
        db.flush()
        return ProgrammeRefreshResult("ERROR", None)


def refresh_due_programmes(db: Session, *, now=None, limit: int = 20) -> dict[str, int]:  # type: ignore[no-untyped-def]
    counts = {"checked": 0, "updated": 0, "failed": 0}
    for event in due_love_racing_events(db, now=now)[:limit]:
        result = refresh_love_racing_programme(db, event)
        counts["checked"] += 1
        if result.outcome == "ERROR":
            counts["failed"] += 1
        elif result.outcome in {"ENRICHED", "MATCHED", "CREATED"}:
            counts["updated"] += 1
    db.commit()
    return counts
