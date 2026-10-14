from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import date, datetime, time, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.policy import Actor
from app.catalog.models import Region, Track
from app.catalog.presentation import track_token
from app.core.config import get_settings
from app.core.enums import Role, WorkdayStatus
from app.core.holidays import holiday_for_date, holiday_info_for_date
from app.core.time import local_today
from app.identity.models import Person
from app.rostering.models import (
    AllowanceIndicator,
    Assignment,
    PersonalWorkdayEntry,
    Workday,
    WorkdayRevision,
)
from app.rostering.participation import active_published_assignments, person_day_participation
from app.rostering.timing import ceil_to_quarter, clock_after, derive_race_day_timing
from app.rostering.travel import EffectivePersonTravel, effective_person_travel
from app.unavailability.models import PersonUnavailability


def fortnight_bounds(
    offset: int = 0, today: date | None = None, *, anchor: date
) -> tuple[date, date]:
    current = today or local_today()
    start = anchor + timedelta(days=((current - anchor).days // 14 + offset) * 14)
    return start, start + timedelta(days=13)


def format_minutes(minutes: int) -> str:
    hours, remainder = divmod(minutes, 60)
    return f"{hours}h" + (f" {remainder}m" if remainder else "")


def _visible_regions(actor: Actor) -> set[uuid.UUID] | None:
    if actor.is_admin:
        return None
    allowed = {Role.MANAGER.value, Role.SUB_MANAGER.value, Role.VIEWER.value}
    return {region_id for region_id, roles in actor.regional_roles.items() if set(roles) & allowed}


def _timeline_item(
    label: str, *, at: time | None = None, detail: str = ""
) -> dict[str, object]:
    return {"label": label, "time": at, "detail": detail}


def _operational_timeline(
    revision: WorkdayRevision,
    assignments: list[Assignment],
    *,
    normal_start: time | None,
    normal_end: time | None,
) -> list[dict[str, object]]:
    effective: list[tuple[Assignment, EffectivePersonTravel]] = [
        (assignment, effective_person_travel(revision, assignment))
        for assignment in assignments
    ]
    start_assignment, start_travel = max(
        (item for item in effective if item[1].start == normal_start),
        key=lambda item: sum(
            value is not None
            for value in (
                item[0].start_time,
                item[0].hotel_to_track_minutes_override,
                item[0].accommodation_name,
            )
        ),
        default=effective[0],
    )
    _, finish_travel = max(
        (item for item in effective if item[1].finish == normal_end),
        key=lambda item: sum(
            value is not None
            for value in (
                item[0].end_time,
                item[0].return_travel_minutes_override,
                item[0].finish_destination_override,
            )
        ),
        default=effective[0],
    )
    origin = (
        start_travel.accommodation
        if start_travel.uses_standard_travel and start_travel.accommodation
        else revision.start_origin
    )
    timeline: list[dict[str, object]] = []
    if normal_start is not None:
        timeline.append(_timeline_item("Start / leave origin", at=normal_start, detail=origin))

    outbound_minutes = (
        start_travel.hotel_to_track_minutes
        if start_travel.uses_standard_travel
        else (
            revision.track_travel_minutes
            if start_assignment.start_time is None
            and start_travel.start == revision.start_time
            else None
        )
    )
    if outbound_minutes is not None:
        timeline.append(
            _timeline_item("Outbound / track travel", detail=format_minutes(outbound_minutes))
        )
    if revision.on_track_time is not None:
        timeline.append(_timeline_item("On track", at=revision.on_track_time))

    reference = datetime.combine(
        revision.work_date, revision.on_track_time or time.min
    )
    first_events = [
        value
        for value in (revision.first_trial_time, revision.first_race_time)
        if value is not None
    ]
    if revision.on_track_time is not None and first_events:
        first_event_at = min(clock_after(value, reference=reference) for value in first_events)
        setup_minutes = int((first_event_at - reference).total_seconds() // 60)
        timeline.append(
            _timeline_item("Setup / pre-event", detail=format_minutes(setup_minutes))
        )

    for label, value in (
        ("First trial", revision.first_trial_time),
        ("Last trial", revision.last_trial_time),
        ("First race", revision.first_race_time),
        ("Last race", revision.last_race_time),
    ):
        if value is not None:
            timeline.append(_timeline_item(label, at=value))

    if revision.last_race_time:
        timing = derive_race_day_timing(
            work_date=revision.work_date,
            first_race_time=revision.first_race_time,
            last_race_time=revision.last_race_time,
            setup_lead_minutes=0,
            track_travel_minutes=revision.track_travel_minutes,
            pack_up_minutes=revision.pack_up_minutes,
            return_travel_minutes=revision.return_travel_minutes,
            on_track_time=revision.on_track_time,
            on_track_is_override=True,
            start_time=revision.start_time,
            start_is_override=True,
            finish_time=revision.end_time,
            finish_is_override=revision.end_time_is_override,
        )
        timeline.append(_timeline_item("Race clear", at=timing.race_clear))
        timeline.append(
            _timeline_item(
                "Pack-up complete",
                at=timing.pack_up_complete,
                detail=format_minutes(revision.pack_up_minutes),
            )
        )
    elif revision.last_trial_time:
        event_clear = ceil_to_quarter(
            clock_after(revision.last_trial_time, reference=reference)
        )
        timeline.append(_timeline_item("Event clear", at=event_clear.time()))
        timeline.append(
            _timeline_item(
                "Pack-up complete",
                at=(event_clear + timedelta(minutes=revision.pack_up_minutes)).time(),
                detail=format_minutes(revision.pack_up_minutes),
            )
        )

    if finish_travel.return_travel_minutes is not None:
        timeline.append(
            _timeline_item(
                "Return travel", detail=format_minutes(finish_travel.return_travel_minutes)
            )
        )
    if normal_end is not None:
        timeline.append(
            _timeline_item(
                "Finish / destination",
                at=normal_end,
                detail=finish_travel.finish_destination or "",
            )
        )
    return timeline


def published_hours(
    db: Session,
    *,
    actor: Actor,
    start: date,
    end: date,
    management: bool,
) -> list[dict[str, object]]:
    statement = (
        select(Assignment, WorkdayRevision, Workday, Person, Region, Track.palette_slot)
        .join(WorkdayRevision, WorkdayRevision.id == Assignment.revision_id)
        .join(Workday, Workday.current_published_revision_id == WorkdayRevision.id)
        .join(Person, Person.id == Assignment.person_id)
        .join(Region, Region.id == Workday.region_id)
        .outerjoin(Track, Track.id == WorkdayRevision.track_id)
        .where(
            WorkdayRevision.work_date >= start,
            WorkdayRevision.work_date <= end,
            Assignment.person_id.is_not(None),
            Workday.status == WorkdayStatus.SCHEDULED.value,
        )
        .order_by(Person.display_name, WorkdayRevision.work_date, Assignment.display_name_snapshot)
    )
    if management:
        region_ids = _visible_regions(actor)
        if region_ids is not None:
            if not region_ids:
                return []
            statement = statement.where(Workday.region_id.in_(region_ids))
    else:
        if actor.person_id is None:
            return []
        statement = statement.where(Assignment.person_id == actor.person_id)

    rows = list(db.execute(statement).all())
    entry_workday_ids = {workday.id for _, _, workday, _, _, _ in rows}
    entry_workday_ids.update(
        workday.generated_from_workday_id
        for _, _, workday, _, _, _ in rows
        if workday.generated_from_workday_id is not None
    )
    personal_entries = {} if management else {
        (row.workday_id, row.person_id): row
        for row in db.scalars(
            select(PersonalWorkdayEntry).where(
                PersonalWorkdayEntry.workday_id.in_(entry_workday_ids or {uuid.uuid4()}),
                PersonalWorkdayEntry.person_id.in_({item[3].id for item in rows} or {uuid.uuid4()}),
            )
        )
    }
    revision_people = {(revision.id, person.id) for _, revision, _, person, _, _ in rows}
    indicators: dict[tuple[uuid.UUID, uuid.UUID], list[AllowanceIndicator]] = defaultdict(list)
    if revision_people:
        revision_ids = {revision_id for revision_id, _ in revision_people}
        person_ids = {person_id for _, person_id in revision_people}
        for indicator in db.scalars(
            select(AllowanceIndicator).where(
                AllowanceIndicator.revision_id.in_(revision_ids),
                AllowanceIndicator.person_id.in_(person_ids),
            )
        ):
            indicators[(indicator.revision_id, indicator.person_id)].append(indicator)

    grouped_rows: dict[
        tuple[uuid.UUID, uuid.UUID],
        tuple[WorkdayRevision, Workday, Person, Region, int | None, list[Assignment]],
    ] = {}
    for assignment, revision, workday, person, region, palette_slot in rows:
        if not active_published_assignments(db, workday, revision, [assignment]):
            continue
        key = (revision.id, person.id)
        grouped_rows.setdefault(
            key, (revision, workday, person, region, palette_slot, [])
        )[5].append(assignment)

    result: list[dict[str, object]] = []
    for key, (revision, workday, person, region, palette_slot, assignments) in grouped_rows.items():
        participation = person_day_participation(revision, assignments)
        start_time, end_time, minutes = participation.start, participation.end, participation.minutes
        normal_start, normal_end = start_time, end_time
        entry = personal_entries.get((workday.id, person.id))
        if not management and entry and (entry.start_time is not None or entry.end_time is not None):
            start_time = entry.start_time or start_time
            end_time = entry.end_time or end_time
            if start_time is not None and end_time is not None:
                from app.core.time import worked_minutes

                minutes = worked_minutes(revision.work_date, start_time, end_time)
        allowance_rows = [
            {
                "kind": item.kind,
                "label": item.kind.replace("_", " ").title(),
                "hours": item.entitlement_hours,
                "explanation": item.explanation,
            }
            for item in indicators.get(key, [])
        ]
        if minutes >= int(get_settings().lunch_allowance_hours * 60) and not any(
            item["kind"] == "LUNCH" for item in allowance_rows
        ):
            allowance_rows.append(
                {
                    "kind": "LUNCH",
                    "label": "Lunch allowance",
                    "hours": None,
                    "explanation": (
                        f"Published span reached the configured "
                        f"{get_settings().lunch_allowance_hours:g}-hour reminder threshold."
                    ),
                }
            )
        result.append(
            {
                "person_id": person.id,
                "person": person.display_name,
                "date": revision.work_date,
                "category": workday.category.replace("_", " ").title(),
                "region": region.name,
                "track": revision.track_name_snapshot,
                "presentation": track_token(palette_slot, workday.category),
                "role": participation.role_summary,
                "start": start_time,
                "end": end_time,
                "minutes": minutes,
                "duration": format_minutes(minutes),
                "normal_start": normal_start,
                "normal_end": normal_end,
                "personal_override": bool(
                    not management and entry and (entry.start_time is not None or entry.end_time is not None)
                ),
                "holiday": holiday_for_date(revision.work_date, region.statutory_holiday_region or ""),
                "holiday_info": holiday_info_for_date(
                    revision.work_date, region.statutory_holiday_region or ""
                ),
                "allowances": allowance_rows,
                "timeline": _operational_timeline(
                    revision,
                    assignments,
                    normal_start=normal_start,
                    normal_end=normal_end,
                ),
                "raw": (
                    f"{start_time.strftime('%H:%M') if start_time else 'not set'} → "
                    f"{end_time.strftime('%H:%M') if end_time else 'not set'} = "
                    f"{format_minutes(minutes)} authoritative Person/day span; "
                    "multiple positions are combined once; no automatic break deduction."
                ),
            }
        )
    return result


def group_people(
    rows: list[dict[str, object]], *, start: date, end: date,
    leave_rows: list[tuple[Person, PersonUnavailability]] | None = None,
) -> list[dict[str, object]]:
    grouped: dict[uuid.UUID, dict[str, object]] = {}
    for row in rows:
        person_id = row["person_id"]
        assert isinstance(person_id, uuid.UUID)
        group = grouped.setdefault(
            person_id,
            {"person": row["person"], "minutes": 0, "days": []},
        )
        group["minutes"] = int(group["minutes"]) + int(row["minutes"])
        days = group["days"]
        assert isinstance(days, list)
        days.append(row)
    leave_by_person: dict[uuid.UUID, list[PersonUnavailability]] = defaultdict(list)
    for person, leave in leave_rows or []:
        leave_by_person[person.id].append(leave)
        grouped.setdefault(
            person.id,
            {"person": person.display_name, "minutes": 0, "days": []},
        )
    for person_id, group in grouped.items():
        group["duration"] = format_minutes(int(group["minutes"]))
        days = group["days"]
        assert isinstance(days, list)
        by_date: dict[date, list[dict[str, object]]] = defaultdict(list)
        for day in days:
            by_date[day["date"]].append(day)
        buckets = []
        for index in range((end - start).days + 1):
            bucket_date = start + timedelta(days=index)
            bucket_rows = by_date.get(bucket_date, [])
            minutes = sum(int(item["minutes"]) for item in bucket_rows)
            locations = list(
                dict.fromkeys(
                    f'{item["track"]} · {item["region"]}' for item in bucket_rows
                )
            )
            location_summary = ", ".join(locations) if locations else "Not worked"
            duration = format_minutes(minutes)
            presentations = list(
                dict.fromkeys(str(item["presentation"]) for item in bucket_rows)
            )
            details = "; ".join(
                f'{item["track"]} · {item["region"]} · {item["category"]} · {item["role"]}'
                for item in bucket_rows
            )
            leave = next(
                (
                    item for item in leave_by_person.get(person_id, [])
                    if item.start_date <= bucket_date <= item.end_date
                ),
                None,
            )
            buckets.append(
                {
                    "date": bucket_date,
                    "minutes": minutes,
                    "duration": duration,
                    "worked": bool(minutes),
                    "leave": leave is not None,
                    "location": location_summary,
                    "presentations": presentations,
                    "items": bucket_rows,
                    "label": (
                        f"{bucket_date.strftime('%a %d %b')}; {duration}; {details}"
                        + ("; On leave" if leave else "")
                        if details
                        else f"{bucket_date.strftime('%a %d %b')}; 0h; "
                        + ("On leave" if leave else "Not worked")
                    ),
                }
            )
        group["buckets"] = buckets
    return list(grouped.values())
