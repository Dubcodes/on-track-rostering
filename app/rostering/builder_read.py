from __future__ import annotations

import uuid
from collections import Counter
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.catalog.models import BasePosition, PersonCrewGroup, Region
from app.identity.models import Person
from app.positions.service import bulk_eligibility
from app.rostering.models import Assignment, Workday, WorkdayRevision


@dataclass(frozen=True)
class CrewPickerPerson:
    id: uuid.UUID
    display_name: str
    hint: str
    context_label: str


@dataclass(frozen=True)
class CrewPickerView:
    relevant: tuple[CrewPickerPerson, ...]
    other: tuple[CrewPickerPerson, ...]

    @property
    def groups(self) -> tuple[tuple[str, tuple[CrewPickerPerson, ...]], ...]:
        return (("Relevant crew", self.relevant), ("Other crew", self.other))


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
    workday: Workday,
    draft: WorkdayRevision,
    position_ids: set[uuid.UUID | None],
    forced_relevant: dict[uuid.UUID | None, set[uuid.UUID]] | None = None,
) -> dict[uuid.UUID | None, CrewPickerView]:
    """Build canonical position-aware crew groups without moving policy into JavaScript."""
    if not position_ids:
        return {}
    forced_relevant = forced_relevant or {}
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
                WorkdayRevision.work_date == draft.work_date,
                Workday.id != workday.id,
                Assignment.person_id.is_not(None),
            )
        )
    )
    eligibility_by_pair = bulk_eligibility(db, person_ids, concrete_position_ids)
    crew_groups_by_person: dict[uuid.UUID, set[uuid.UUID]] = {}
    if person_ids:
        for person_id, crew_group_id in db.execute(
            select(PersonCrewGroup.person_id, PersonCrewGroup.crew_group_id).where(
                PersonCrewGroup.person_id.in_(person_ids)
            )
        ):
            crew_groups_by_person.setdefault(person_id, set()).add(crew_group_id)

    region_ids = {person.home_region_id for person in people if person.home_region_id}
    region_names = {
        region.id: region.name
        for region in db.scalars(select(Region).where(Region.id.in_(region_ids)))
    }
    duplicate_names = {
        name for name, count in Counter(person.display_name for person in people).items() if count > 1
    }

    views: dict[uuid.UUID | None, CrewPickerView] = {}
    for position_id in position_ids:
        position = positions.get(position_id)
        relevant: list[CrewPickerPerson] = []
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
            context_label = ""
            if person.display_name in duplicate_names:
                region_name = region_names.get(person.home_region_id, "No home region")
                context_label = f"{region_name} · crew record {str(person.id)[:8]}"
            option = CrewPickerPerson(
                id=person.id,
                display_name=person.display_name,
                hint=hint,
                context_label=context_label,
            )
            is_relevant = bool(
                person.id in forced_relevant.get(position_id, set())
                or eligible
                or person.home_region_id == workday.region_id
                or (
                    position
                    and position.crew_group_id
                    and position.crew_group_id in crew_groups_by_person.get(person.id, set())
                )
            )
            (relevant if is_relevant else other).append(option)
        views[position_id] = CrewPickerView(tuple(relevant), tuple(other))
    return views
