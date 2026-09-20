from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.policy import Actor, can_view_published, external_calendar_region_ids
from app.catalog.models import Track
from app.catalog.presentation import track_token
from app.external_calendar.models import CalendarDisplayPreference, ExternalCalendarEvent
from app.rostering.models import Workday, WorkdayRevision


def calendar_preference(db: Session, user_id) -> CalendarDisplayPreference:  # type: ignore[no-untyped-def]
    return db.get(CalendarDisplayPreference, user_id) or CalendarDisplayPreference(
        user_id=user_id,
        show_thoroughbred=True,
        show_harness=True,
        show_trials=False,
        minimal_external_detail=False,
    )


def external_calendar_items(
    db: Session, actor: Actor, start: date, end: date
) -> list[dict[str, object]]:
    preference = calendar_preference(db, actor.user_id)
    region_ids = external_calendar_region_ids(db, actor)
    if region_ids == set():
        return []
    statement = (
        select(ExternalCalendarEvent, Track)
        .outerjoin(Track, Track.id == ExternalCalendarEvent.track_id)
        .where(
            ExternalCalendarEvent.event_date >= start,
            ExternalCalendarEvent.event_date < end,
        )
    )
    if region_ids is not None:
        statement = statement.where(Track.region_id.in_(region_ids))
    result = []
    for event, track in db.execute(statement):
        linked = db.scalar(select(Workday).where(Workday.external_event_id == event.id))
        if linked and linked.current_published_revision_id:
            published = db.get(WorkdayRevision, linked.current_published_revision_id)
            if published and can_view_published(db, actor, linked, published):
                continue
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
