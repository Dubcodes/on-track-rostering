from datetime import date

from sqlalchemy import exists, select
from sqlalchemy.orm import Session

from app.catalog.models import Track
from app.catalog.presentation import track_token
from app.external_calendar.models import CalendarDisplayPreference, ExternalCalendarEvent
from app.rostering.models import Workday


def calendar_preference(db: Session, user_id) -> CalendarDisplayPreference:  # type: ignore[no-untyped-def]
    return db.get(CalendarDisplayPreference, user_id) or CalendarDisplayPreference(
        user_id=user_id,
        show_thoroughbred=True,
        show_harness=True,
        show_trials=False,
        minimal_external_detail=False,
    )


def external_calendar_items(db: Session, user_id, start: date, end: date) -> list[dict[str, object]]:  # type: ignore[no-untyped-def]
    preference = calendar_preference(db, user_id)
    statement = (
        select(ExternalCalendarEvent, Track)
        .outerjoin(Track, Track.id == ExternalCalendarEvent.track_id)
        .where(
            ExternalCalendarEvent.event_date >= start,
            ExternalCalendarEvent.event_date < end,
            ~exists(select(Workday.id).where(Workday.external_event_id == ExternalCalendarEvent.id)),
        )
    )
    result = []
    for event, track in db.execute(statement):
        if event.discipline == "THOROUGHBRED" and not preference.show_thoroughbred:
            continue
        if event.discipline == "HARNESS" and not preference.show_harness:
            continue
        if event.event_kind == "TRIAL" and not preference.show_trials:
            continue
        result.append(
            {
                "id": str(event.id),
                "date": event.event_date,
                "track": track.name if track else event.external_track_name or "Track unresolved",
                "discipline": event.discipline,
                "kind": event.event_kind,
                "time": event.first_trial_time or event.first_race_time,
                "presentation": track_token(track.palette_slot if track else None),
                "source": (event.presentation_provider or "source").casefold().replace("_", "-"),
                "minimal": preference.minimal_external_detail,
            }
        )
    return result
