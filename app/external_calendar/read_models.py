from datetime import date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth.policy import Actor, can_view_published, external_calendar_region_ids
from app.catalog.models import Track
from app.catalog.presentation import track_token
from app.core.time import local_today
from app.external_calendar.models import CalendarDisplayPreference, ExternalCalendarEvent
from app.identity.models import Person
from app.rostering.models import Workday, WorkdayRevision


def _planning_identity(
    workday: Workday, published: WorkdayRevision
) -> tuple[date, object, str, str | None] | None:
    event_kind = {"RACE_DAY": "RACE", "TRIALS": "TRIAL"}.get(workday.category)
    if event_kind is None or published.track_id is None:
        return None
    return (
        published.work_date,
        published.track_id,
        event_kind,
        workday.racing_discipline,
    )


def calendar_preference(db: Session, user_id) -> CalendarDisplayPreference:  # type: ignore[no-untyped-def]
    return db.get(CalendarDisplayPreference, user_id) or CalendarDisplayPreference(
        user_id=user_id,
        show_thoroughbred=True,
        show_harness=True,
        show_trials=False,
        minimal_external_detail=False,
    )


def external_calendar_items(
    db: Session,
    actor: Actor,
    start: date,
    end: date,
    *,
    region_ids_override: set[object] | None = None,
) -> list[dict[str, object]]:
    preference = calendar_preference(db, actor.user_id)
    region_ids = (
        region_ids_override
        if region_ids_override is not None
        else external_calendar_region_ids(db, actor)
    )
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
    event_rows = list(db.execute(statement))
    visible_publication_identities: set[tuple[date, object, str, str | None]] = set()
    publication_statement = (
        select(Workday, WorkdayRevision)
        .join(WorkdayRevision, Workday.current_published_revision_id == WorkdayRevision.id)
        .where(
            Workday.status == "SCHEDULED",
            WorkdayRevision.work_date >= start,
            WorkdayRevision.work_date < end,
        )
    )
    if region_ids is not None:
        publication_statement = publication_statement.where(Workday.region_id.in_(region_ids))
    for workday, published in db.execute(publication_statement):
        identity = _planning_identity(workday, published)
        if identity and can_view_published(db, actor, workday, published):
            visible_publication_identities.add(identity)

    result = []
    for event, track in event_rows:
        linked = db.scalar(select(Workday).where(Workday.external_event_id == event.id))
        if linked and linked.status == "SCHEDULED" and linked.current_published_revision_id:
            published = db.get(WorkdayRevision, linked.current_published_revision_id)
            if published and can_view_published(db, actor, linked, published):
                continue
        semantic_identity = (
            event.event_date,
            event.track_id,
            event.event_kind,
            event.discipline,
        )
        if semantic_identity in visible_publication_identities:
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
                "track_id": event.track_id,
                "discipline": event.discipline,
                "kind": event.event_kind,
                "time": event.first_trial_time or event.first_race_time,
                "presentation": track_token(track.palette_slot if track else None),
                "source": (event.presentation_provider or "source").casefold().replace("_", "-"),
                "minimal": preference.minimal_external_detail,
            }
        )
    result.sort(
        key=lambda item: (
            item["date"], str(item["track_id"] or ""), str(item["discipline"]),
            0 if item["kind"] == "RACE" else 1, str(item["id"]),
        )
    )
    grouped: list[dict[str, object]] = []
    consumed: set[str] = set()
    for item in result:
        item_id = str(item["id"])
        if item_id in consumed:
            continue
        if item["kind"] == "RACE" and item["time"] is not None:
            related = next(
                (
                    candidate
                    for candidate in result
                    if candidate["id"] not in consumed
                    and candidate["kind"] == "TRIAL"
                    and candidate["date"] == item["date"]
                    and candidate["track_id"] == item["track_id"]
                    and candidate["discipline"] == item["discipline"]
                    and candidate["time"] is not None
                    and abs(
                        (
                            datetime.combine(item["date"], candidate["time"])
                            - datetime.combine(item["date"], item["time"])
                        ).total_seconds()
                    )
                    <= 180 * 60
                ),
                None,
            )
            if related:
                item["related"] = [related]
                consumed.add(str(related["id"]))
        grouped.append(item)
        consumed.add(item_id)
    return grouped


def personal_external_calendar_items(
    db: Session, actor: Actor, start: date, end: date
) -> list[dict[str, object]]:
    """Return source-only planning after the Primary Region roster horizon."""
    if actor.person_id is None or actor.is_contractor:
        return []
    home_region_id = db.scalar(
        select(Person.home_region_id).where(Person.id == actor.person_id)
    )
    if home_region_id is None:
        return []
    horizon = db.scalar(
        select(func.max(WorkdayRevision.work_date))
        .join(Workday, Workday.current_published_revision_id == WorkdayRevision.id)
        .where(
            Workday.region_id == home_region_id,
            Workday.status == "SCHEDULED",
        )
    )
    planning_start = max(start, horizon + timedelta(days=1) if horizon else local_today())
    if planning_start >= end:
        return []
    return external_calendar_items(
        db,
        actor,
        planning_start,
        end,
        region_ids_override={home_region_id},
    )
