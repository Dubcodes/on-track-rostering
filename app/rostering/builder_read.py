from __future__ import annotations

import uuid
from collections import Counter
from dataclasses import dataclass
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.catalog.models import BasePosition, PersonCrewGroup, Region
from app.core.enums import AssignmentStatus, Role, WorkdayCategory, WorkdayStatus
from app.identity.models import Person, RoleGrant, User, UserPersonLink
from app.positions.service import bulk_eligibility
from app.rostering.models import Assignment, Workday, WorkdayRevision
from app.unavailability.service import active_for_people_on_date, unavailability_label


@dataclass(frozen=True)
class CrewPickerPerson:
    id: uuid.UUID
    display_name: str
    eligible: bool
    hint: str
    context_label: str
    same_date: bool
    on_leave: bool
    leave_start: date | None
    leave_end: date | None
    dice_eligible: bool
    dice_employment_known: bool
    dice_familiarity: int

    @property
    def leave_label(self) -> str:
        if not self.on_leave or not self.leave_start or not self.leave_end:
            return ""
        return unavailability_label(self.leave_start, self.leave_end)


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
    work_date: date | None,
    position_ids: set[uuid.UUID | None],
    track_id: uuid.UUID | None = None,
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
    active_roles_by_person: dict[uuid.UUID, set[str]] = {}
    if person_ids:
        for person_id, role in db.execute(
            select(UserPersonLink.person_id, RoleGrant.role)
            .join(User, User.id == UserPersonLink.user_id)
            .join(RoleGrant, RoleGrant.user_id == User.id)
            .where(
                UserPersonLink.person_id.in_(person_ids),
                User.status == "ACTIVE",
                RoleGrant.status == "ACTIVE",
            )
        ):
            active_roles_by_person.setdefault(person_id, set()).add(role)
    concrete_position_ids = {position_id for position_id in position_ids if position_id}
    all_positions = {
        position.id: position
        for position in db.scalars(
            select(BasePosition).where(BasePosition.lifecycle == "ACTIVE")
        )
    }
    positions = {
        position_id: all_positions[position_id]
        for position_id in concrete_position_ids
        if position_id in all_positions
    }
    missing = concrete_position_ids - positions.keys()
    if missing:
        raise ValueError("Select an active base position.")

    same_date_people = (
        set(db.scalars(
            select(Assignment.person_id)
            .join(WorkdayRevision, WorkdayRevision.id == Assignment.revision_id)
            .join(Workday, Workday.current_published_revision_id == WorkdayRevision.id)
            .where(
                WorkdayRevision.work_date == work_date,
                Workday.status == WorkdayStatus.SCHEDULED.value,
                Assignment.status == AssignmentStatus.ASSIGNED.value,
                *( [Workday.id != exclude_workday_id] if exclude_workday_id else [] ),
                Assignment.person_id.is_not(None),
            )
        ))
        if work_date else set()
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
    leave_by_person = active_for_people_on_date(db, person_ids, work_date) if work_date else {}
    memberships_by_person: dict[uuid.UUID, set[uuid.UUID]] = {}
    if person_ids:
        for person_id, crew_group_id in db.execute(
            select(PersonCrewGroup.person_id, PersonCrewGroup.crew_group_id).where(
                PersonCrewGroup.person_id.in_(person_ids)
            )
        ):
            memberships_by_person.setdefault(person_id, set()).add(crew_group_id)
    track_history: dict[uuid.UUID, set[uuid.UUID]] = {}
    if track_id and work_date and person_ids:
        history_statement = (
            select(Assignment.person_id, Assignment.base_position_id)
            .join(WorkdayRevision, WorkdayRevision.id == Assignment.revision_id)
            .join(Workday, Workday.current_published_revision_id == WorkdayRevision.id)
            .where(
                WorkdayRevision.track_id == track_id,
                WorkdayRevision.work_date < work_date,
                Workday.status == WorkdayStatus.SCHEDULED.value,
                Workday.category != WorkdayCategory.TRAVEL_DAY.value,
                Assignment.status == AssignmentStatus.ASSIGNED.value,
                Assignment.person_id.in_(person_ids),
                Assignment.base_position_id.is_not(None),
                *([Workday.id != exclude_workday_id] if exclude_workday_id else []),
            )
            .distinct()
        )
        for person_id, historical_position_id in db.execute(history_statement):
            track_history.setdefault(person_id, set()).add(historical_position_id)

    views: dict[uuid.UUID | None, CrewPickerView] = {}
    for position_id in position_ids:
        primary: list[CrewPickerPerson] = []
        other: list[CrewPickerPerson] = []
        for person in people:
            position = positions.get(position_id) if position_id else None
            eligible, reason = (
                eligibility_by_pair[(person.id, position_id)]
                if position_id is not None
                else (False, "No capability signal")
            )
            hint = HINT_LABELS[reason]
            if person.id in same_date_people:
                hint += "; published on another Workday this date — Preview checks actual overlap"
            region_name = region_names.get(person.home_region_id, "No primary region")
            context_label = region_name if person.home_region_id != region_id else ""
            if person.display_name in duplicate_names:
                context_label = f"{region_name} · crew record {str(person.id)[:8]}"
            roles = active_roles_by_person.get(person.id, set())
            employment_roles = {
                Role.EMPLOYEE.value,
                Role.SUB_MANAGER.value,
                Role.MANAGER.value,
            }
            group_match = bool(
                position
                and position.crew_group_id
                and position.crew_group_id in memberships_by_person.get(person.id, set())
            )
            dice_eligible = bool(
                eligible
                and person.home_region_id == region_id
                and roles & employment_roles
                and Role.CONTRACTOR.value not in roles
                and Role.CASUAL.value not in roles
                and group_match
            )
            historical_positions = track_history.get(person.id, set())
            related_history = bool(
                position
                and position.crew_group_id
                and any(
                    all_positions.get(historical_id)
                    and all_positions[historical_id].crew_group_id == position.crew_group_id
                    for historical_id in historical_positions
                )
            )
            option = CrewPickerPerson(
                id=person.id,
                display_name=person.display_name,
                eligible=eligible,
                hint=hint,
                context_label=context_label,
                same_date=person.id in same_date_people,
                on_leave=person.id in leave_by_person,
                leave_start=(leave_by_person[person.id].start_date if person.id in leave_by_person else None),
                leave_end=(leave_by_person[person.id].end_date if person.id in leave_by_person else None),
                dice_eligible=dice_eligible,
                dice_employment_known=person.id in active_roles_by_person,
                dice_familiarity=(
                    2
                    if position_id is not None
                    and position_id in historical_positions
                    else 1 if related_history else 0
                ),
            )
            (primary if person.home_region_id == region_id else other).append(option)
        views[position_id] = CrewPickerView(
            f"{region_names.get(region_id, 'Primary region')} crew",
            tuple(primary),
            tuple(other),
        )
    return views
