from __future__ import annotations

import uuid
from collections import Counter
from dataclasses import dataclass
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.catalog.models import BasePosition, Region
from app.core.enums import WorkdayStatus
from app.identity.models import Person
from app.positions.service import bulk_eligibility
from app.rostering.models import Assignment, Workday, WorkdayRevision
from app.unavailability.service import active_for_people_on_date


@dataclass(frozen=True)
class CrewPickerPerson:
    id: uuid.UUID
    display_name: str
    hint: str
    context_label: str
    same_date: bool
    on_leave: bool
    leave_start: date | None
    leave_end: date | None

    @property
    def leave_label(self) -> str:
        if not self.on_leave or not self.leave_start or not self.leave_end:
            return ""
        start = f"{self.leave_start.day} {self.leave_start:%b}"
        end = (
            str(self.leave_end.day)
            if self.leave_start.month == self.leave_end.month
            and self.leave_start.year == self.leave_end.year
            else f"{self.leave_end.day} {self.leave_end:%b}"
        )
        return f"On leave · {start}–{end}"


@dataclass(frozen=True)
class CrewPickerView:
    primary_label: str
    primary: tuple[CrewPickerPerson, ...]
    other: tuple[CrewPickerPerson, ...]

    @property
    def groups(self) -> tuple[tuple[str, tuple[CrewPickerPerson, ...]], ...]:
        return ((self.primary_label, self.primary), ("Other regions", self.other))


HINT_LABELS = {
    "Allowed": "Preferred or approved",
    "Worked before": "Worked this position before",
    "Manager restricted": "Manager marked unavailable for this position",
    "Employee opted out": "Crew member opted out of this position",
    "No capability signal": "No position history recorded",
}


def crew_picker_views(
    db: Session,
    *,
    region_id: uuid.UUID,
    work_date: date,
    position_ids: set[uuid.UUID | None],
    exclude_workday_id: uuid.UUID | None = None,
) -> dict[uuid.UUID | None, CrewPickerView]:
    """Build canonical position-aware crew groups without moving policy into JavaScript."""
    if not position_ids:
        return {}
    people = list(
        db.scalars(
            select(Person)
            .where(Person.lifecycle == "ACTIVE")
            .order_by(Person.display_name, Person.id)
        )
    )
    person_ids = {person.id for person in people}
    concrete_position_ids = {position_id for position_id in position_ids if position_id}
    positions = {
        position.id: position
        for position in db.scalars(
            select(BasePosition).where(
                BasePosition.id.in_(concrete_position_ids), BasePosition.lifecycle == "ACTIVE"
            )
        )
    }
    missing = concrete_position_ids - positions.keys()
    if missing:
        raise ValueError("Select an active base position.")

    same_date_people = set(
        db.scalars(
            select(Assignment.person_id)
            .join(WorkdayRevision, WorkdayRevision.id == Assignment.revision_id)
            .join(Workday, Workday.current_published_revision_id == WorkdayRevision.id)
            .where(
                WorkdayRevision.work_date == work_date,
                Workday.status == WorkdayStatus.SCHEDULED.value,
                *( [Workday.id != exclude_workday_id] if exclude_workday_id else [] ),
                Assignment.person_id.is_not(None),
            )
        )
    )
    eligibility_by_pair = bulk_eligibility(db, person_ids, concrete_position_ids)
    region_ids = {region_id, *(person.home_region_id for person in people if person.home_region_id)}
    region_names = {
        region.id: region.name
        for region in db.scalars(select(Region).where(Region.id.in_(region_ids)))
    }
    duplicate_names = {
        name for name, count in Counter(person.display_name for person in people).items() if count > 1
    }
    leave_by_person = active_for_people_on_date(db, person_ids, work_date)

    views: dict[uuid.UUID | None, CrewPickerView] = {}
    for position_id in position_ids:
        primary: list[CrewPickerPerson] = []
        other: list[CrewPickerPerson] = []
        for person in people:
            eligible, reason = (
                eligibility_by_pair[(person.id, position_id)]
                if position_id is not None
                else (False, "No capability signal")
            )
            hint = HINT_LABELS[reason]
            if person.id in same_date_people:
                hint += "; also rostered this date"
            region_name = region_names.get(person.home_region_id, "No primary region")
            context_label = region_name if person.home_region_id != region_id else ""
            if person.display_name in duplicate_names:
                context_label = f"{region_name} · crew record {str(person.id)[:8]}"
            option = CrewPickerPerson(
                id=person.id,
                display_name=person.display_name,
                hint=hint,
                context_label=context_label,
                same_date=person.id in same_date_people,
                on_leave=person.id in leave_by_person,
                leave_start=(leave_by_person[person.id].start_date if person.id in leave_by_person else None),
                leave_end=(leave_by_person[person.id].end_date if person.id in leave_by_person else None),
            )
            (primary if person.home_region_id == region_id else other).append(option)
        views[position_id] = CrewPickerView(
            f"{region_names.get(region_id, 'Primary region')} crew",
            tuple(primary),
            tuple(other),
        )
    return views
