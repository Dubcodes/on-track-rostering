from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.policy import Actor
from app.catalog.models import Region
from app.core.config import get_settings
from app.core.enums import Role
from app.core.holidays import holiday_for_date
from app.core.time import local_today
from app.identity.models import Person
from app.rostering.models import AllowanceIndicator, Assignment, Workday, WorkdayRevision
from app.rostering.participation import person_day_participation


def fortnight_bounds(offset: int = 0, today: date | None = None) -> tuple[date, date]:
    anchor = date.fromisoformat(get_settings().fortnight_anchor)
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
    return {
        region_id
        for region_id, roles in actor.regional_roles.items()
        if set(roles) & allowed
    }


def published_hours(
    db: Session,
    *,
    actor: Actor,
    start: date,
    end: date,
    management: bool,
) -> list[dict[str, object]]:
    statement = (
        select(Assignment, WorkdayRevision, Workday, Person, Region)
        .join(WorkdayRevision, WorkdayRevision.id == Assignment.revision_id)
        .join(Workday, Workday.current_published_revision_id == WorkdayRevision.id)
        .join(Person, Person.id == Assignment.person_id)
        .join(Region, Region.id == Workday.region_id)
        .where(
            WorkdayRevision.work_date >= start,
            WorkdayRevision.work_date <= end,
            Assignment.person_id.is_not(None),
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
    revision_people = {(revision.id, person.id) for _, revision, _, person, _ in rows}
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
        tuple[WorkdayRevision, Workday, Person, Region, list[Assignment]],
    ] = {}
    for assignment, revision, workday, person, region in rows:
        key = (revision.id, person.id)
        grouped_rows.setdefault(key, (revision, workday, person, region, []))[4].append(assignment)

    result: list[dict[str, object]] = []
    for key, (revision, workday, person, region, assignments) in grouped_rows.items():
        participation = person_day_participation(revision, assignments)
        start_time, end_time, minutes = participation.start, participation.end, participation.minutes
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
                "role": participation.role_summary,
                "start": start_time,
                "end": end_time,
                "minutes": minutes,
                "duration": format_minutes(minutes),
                "holiday": holiday_for_date(revision.work_date, region.statutory_holiday_region or ""),
                "allowances": allowance_rows,
                "raw": (
                    f"{start_time.strftime('%H:%M') if start_time else 'not set'} → "
                    f"{end_time.strftime('%H:%M') if end_time else 'not set'} = "
                    f"{format_minutes(minutes)} published roster span; no automatic break deduction."
                ),
            }
        )
    return result


def group_people(rows: list[dict[str, object]]) -> list[dict[str, object]]:
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
    for group in grouped.values():
        group["duration"] = format_minutes(int(group["minutes"]))
    return list(grouped.values())
