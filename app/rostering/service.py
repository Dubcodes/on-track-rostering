from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, time

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.audit.models import HumanChange
from app.audit.service import record_audit
from app.catalog.models import BasePosition, Region, Track, Vehicle
from app.core.enums import (
    AssignmentStatus,
    CapabilitySignal,
    DeclinePolicy,
    OpenApplicationStatus,
    RacingDiscipline,
    RevisionState,
    WorkdayCategory,
    WorkdayStatus,
)
from app.core.time import utcnow
from app.identity.models import Person, User
from app.notifications.service import record_event
from app.positions.service import set_signal
from app.rostering.conflicts import publication_conflicts
from app.rostering.diff import PublicationChange, publication_diff
from app.rostering.models import (
    AllowanceIndicator,
    Assignment,
    OpenPositionApplication,
    Operation,
    PersonalWorkdayEntry,
    ProgrammeItem,
    TravelLeg,
    Workday,
    WorkdayRevision,
)
from app.rostering.travel import (
    TRANSPORT_CUSTOM,
    TRANSPORT_MODES,
    TRANSPORT_UNASSIGNED,
    TRANSPORT_VEHICLE,
    calculate_standard_travel,
)


class PublishConflict(ValueError):
    pass


class DraftConflict(ValueError):
    pass


_UNCHANGED = object()


@dataclass(frozen=True)
class AssignmentInput:
    base_position_id: uuid.UUID | None
    slot_index: int | None
    person_id: uuid.UUID | None
    status: str
    note: str = ""
    note_private: bool = True
    start_time: time | None = None
    end_time: time | None = None
    transport_mode: str = TRANSPORT_UNASSIGNED
    vehicle_id: uuid.UUID | None = None
    custom_transport_text: str = ""
    accommodation_name: str = ""
    uses_standard_travel: bool = True
    hotel_to_track_minutes_override: int | None = None
    finish_destination_override: str = ""
    return_travel_minutes_override: int | None = None


@dataclass(frozen=True)
class DraftAssignmentInput(AssignmentInput):
    assignment_id: uuid.UUID | None = None


@dataclass(frozen=True)
class DraftDetailsInput:
    work_date: date
    track_id: uuid.UUID | None
    title: str
    start_time: time | None
    end_time: time | None
    on_track_time: time | None
    first_trial_time: time | None
    first_race_time: time | None
    last_race_time: time | None
    race_count: int | None
    day_note: str
    change_reason: str
    end_time_is_override: bool = False
    last_trial_time: time | None = None
    start_origin: str = ""
    finish_destination: str = ""
    category: str = WorkdayCategory.RACE_DAY.value
    racing_discipline: str | None = RacingDiscipline.THOROUGHBRED.value
    standard_travel_enabled: bool = False
    travel_departure_time: time | None = None
    travel_to_hotel_minutes: int | None = None
    default_hotel: str = ""
    hotel_to_track_minutes: int | None = None
    return_travel_minutes: int | None = None
    pack_up_minutes: int = 60


def _validated_track(db: Session, track_id: uuid.UUID | None, region_id: uuid.UUID) -> str:
    if track_id is None:
        return "To be confirmed"
    track = db.get(Track, track_id)
    if not track or track.lifecycle != "ACTIVE" or track.region_id != region_id:
        raise ValueError("Select an active track from the workday region.")
    return track.name


def create_workday(
    db: Session,
    *,
    region_id: uuid.UUID,
    category: str,
    work_date: date,
    track_id: uuid.UUID | None,
    title: str,
    actor_user_id: uuid.UUID,
    racing_discipline: str | None = None,
    commit: bool = True,
) -> Workday:
    region = db.get(Region, region_id)
    if not region or region.lifecycle != "ACTIVE":
        raise ValueError("Select an active region.")
    track_name = _validated_track(db, track_id, region_id)
    if category in {WorkdayCategory.RACE_DAY.value, WorkdayCategory.TRIALS.value}:
        racing_discipline = racing_discipline or RacingDiscipline.THOROUGHBRED.value
    else:
        racing_discipline = None
    workday = Workday(
        region_id=region_id,
        category=category,
        racing_discipline=racing_discipline,
        created_by_user_id=actor_user_id,
    )
    db.add(workday)
    db.flush()
    draft = WorkdayRevision(
        workday_id=workday.id,
        revision_number=1,
        state=RevisionState.DRAFT.value,
        work_date=work_date,
        track_id=track_id,
        track_name_snapshot=track_name,
        title=title.strip() or category.replace("_", " ").title(),
        created_by_user_id=actor_user_id,
    )
    db.add(draft)
    db.flush()
    workday.current_draft_revision_id = draft.id
    record_audit(db, "workday.created", "workday", workday.id, actor_user_id, region_id=region_id)
    if commit:
        db.commit()
    return workday


def ensure_draft(
    db: Session, workday: Workday, actor_user_id: uuid.UUID, *, commit: bool = True
) -> WorkdayRevision:
    workday = db.scalar(
        select(Workday)
        .where(Workday.id == workday.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if workday is None:
        raise ValueError("Workday not found.")
    if workday.current_draft_revision_id:
        return db.get(WorkdayRevision, workday.current_draft_revision_id)  # type: ignore[return-value]
    published = db.get(WorkdayRevision, workday.current_published_revision_id)
    if published is None:
        raise ValueError("Workday has no draft or published revision.")
    draft = WorkdayRevision(
        workday_id=workday.id,
        revision_number=published.revision_number + 1,
        state=RevisionState.DRAFT.value,
        based_on_revision_id=published.id,
        work_date=published.work_date,
        track_id=published.track_id,
        track_name_snapshot=published.track_name_snapshot,
        title=published.title,
        start_time=published.start_time,
        end_time=published.end_time,
        end_time_is_override=published.end_time_is_override,
        on_track_time=published.on_track_time,
        first_trial_time=published.first_trial_time,
        last_trial_time=published.last_trial_time,
        first_race_time=published.first_race_time,
        last_race_time=published.last_race_time,
        race_count=published.race_count,
        start_origin=published.start_origin,
        finish_destination=published.finish_destination,
        standard_travel_enabled=published.standard_travel_enabled,
        travel_departure_time=published.travel_departure_time,
        travel_to_hotel_minutes=published.travel_to_hotel_minutes,
        default_hotel=published.default_hotel,
        hotel_to_track_minutes=published.hotel_to_track_minutes,
        return_travel_minutes=published.return_travel_minutes,
        pack_up_minutes=published.pack_up_minutes,
        day_note=published.day_note,
        created_by_user_id=actor_user_id,
    )
    db.add(draft)
    db.flush()
    for old in db.scalars(select(Assignment).where(Assignment.revision_id == published.id)):
        db.add(
            Assignment(
                revision_id=draft.id,
                slot_key=old.slot_key,
                base_position_id=old.base_position_id,
                slot_index=old.slot_index,
                display_name_snapshot=old.display_name_snapshot,
                person_id=old.person_id,
                person_name_snapshot=old.person_name_snapshot,
                status=old.status,
                start_time=old.start_time,
                end_time=old.end_time,
                note=old.note,
                note_private=old.note_private,
                vehicle_id=old.vehicle_id,
                vehicle_name_snapshot=old.vehicle_name_snapshot,
                transport_mode=old.transport_mode,
                custom_transport_text=old.custom_transport_text,
                accommodation_name=old.accommodation_name,
                uses_standard_travel=old.uses_standard_travel,
                hotel_to_track_minutes_override=old.hotel_to_track_minutes_override,
                finish_destination_override=old.finish_destination_override,
                return_travel_minutes_override=old.return_travel_minutes_override,
            )
        )
    workday.current_draft_revision_id = draft.id
    workday.lock_version += 1
    if commit:
        db.commit()
    return draft


def lock_current_draft(
    db: Session,
    *,
    workday_id: uuid.UUID,
    draft_id: uuid.UUID,
    expected_version: int,
) -> tuple[Workday, WorkdayRevision]:
    workday = db.scalar(
        select(Workday)
        .where(Workday.id == workday_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if not workday or workday.lock_version != expected_version:
        raise DraftConflict(
            "This roster was changed by someone else. Refresh the builder before making further changes."
        )
    draft = db.scalar(
        select(WorkdayRevision)
        .where(WorkdayRevision.id == draft_id)
        .execution_options(populate_existing=True)
    )
    if not draft or workday.current_draft_revision_id != draft.id:
        raise DraftConflict("This draft is no longer current. Refresh the builder before making changes.")
    if draft.state != RevisionState.DRAFT.value:
        raise DraftConflict("Published revisions are immutable. Refresh the builder.")
    return workday, draft


def _sync_standard_travel(db: Session, workday: Workday, draft: WorkdayRevision) -> None:
    """Project the editable standard plan into Operation, legs and one generated Travel Day."""
    if not draft.standard_travel_enabled:
        return
    if workday.category not in {WorkdayCategory.RACE_DAY.value, WorkdayCategory.TRIALS.value}:
        raise ValueError("Standard overnight travel is available only for Race Days and Trials.")
    last_event_time = (
        draft.last_trial_time if workday.category == WorkdayCategory.TRIALS.value
        else draft.last_race_time
    )
    required = (
        draft.on_track_time,
        draft.travel_departure_time,
        draft.travel_to_hotel_minutes,
        draft.hotel_to_track_minutes,
    )
    if any(value is None for value in required) or (
        not draft.end_time_is_override
        and (last_event_time is None or draft.return_travel_minutes is None)
    ):
        raise ValueError("Complete the overnight travel timing before saving.")
    if not draft.start_origin or not draft.default_hotel or not draft.finish_destination:
        raise ValueError("Enter the travel origin, default hotel and return destination.")
    calculation = calculate_standard_travel(
        race_date=draft.work_date,
        on_track_time=draft.on_track_time,  # type: ignore[arg-type]
        last_race_time=last_event_time,
        departure_time=draft.travel_departure_time,  # type: ignore[arg-type]
        travel_to_hotel_minutes=draft.travel_to_hotel_minutes,  # type: ignore[arg-type]
        hotel_to_track_minutes=draft.hotel_to_track_minutes,  # type: ignore[arg-type]
        pack_up_minutes=draft.pack_up_minutes,
        return_travel_minutes=draft.return_travel_minutes,
        explicit_finish_time=draft.end_time if draft.end_time_is_override else None,
    )
    draft.start_time = calculation.race_start
    if not draft.end_time_is_override:
        draft.end_time = calculation.race_finish
    operation = db.get(Operation, workday.operation_id) if workday.operation_id else None
    if operation is None:
        operation = Operation(
            name=f"{draft.track_name_snapshot} {draft.work_date.isoformat()}",
            region_id=workday.region_id,
            starts_on=calculation.travel_date,
            ends_on=draft.work_date,
        )
        db.add(operation)
        db.flush()
        workday.operation_id = operation.id
    else:
        operation.name = f"{draft.track_name_snapshot} {draft.work_date.isoformat()}"
        operation.starts_on = calculation.travel_date
        operation.ends_on = draft.work_date
    for leg in db.scalars(select(TravelLeg).where(TravelLeg.operation_id == operation.id)):
        db.delete(leg)
    db.add_all(
        [
            TravelLeg(
                operation_id=operation.id,
                travel_date=calculation.travel_date,
                origin=draft.start_origin,
                destination=draft.default_hotel,
                starts_at=calculation.travel_start,
                ends_at=calculation.travel_finish,
                notes="Generated standard travel to hotel",
            ),
            TravelLeg(
                operation_id=operation.id,
                travel_date=draft.work_date,
                origin=draft.default_hotel,
                destination=draft.track_name_snapshot,
                starts_at=calculation.race_start,
                ends_at=draft.on_track_time,
                notes="Race-day hotel to track",
            ),
            TravelLeg(
                operation_id=operation.id,
                travel_date=draft.work_date,
                origin=draft.track_name_snapshot,
                destination=draft.finish_destination,
                starts_at=calculation.pack_up_done,
                ends_at=calculation.race_finish,
                notes="Return after pack-up",
            ),
        ]
    )
    travel_workday = db.scalar(
        select(Workday).where(Workday.generated_from_workday_id == workday.id)
    )
    if travel_workday is None:
        travel_workday = Workday(
            region_id=workday.region_id,
            operation_id=operation.id,
            generated_from_workday_id=workday.id,
            category=WorkdayCategory.TRAVEL_DAY.value,
            created_by_user_id=draft.created_by_user_id,
        )
        db.add(travel_workday)
        db.flush()
        travel_draft = WorkdayRevision(
            workday_id=travel_workday.id,
            revision_number=1,
            state=RevisionState.DRAFT.value,
            work_date=calculation.travel_date,
            track_name_snapshot=draft.default_hotel,
            title="Travel Day",
            start_time=calculation.travel_start,
            end_time=calculation.travel_finish,
            start_origin=draft.start_origin,
            finish_destination=draft.default_hotel,
            default_hotel=draft.default_hotel,
            day_note=f"Standard travel for {draft.track_name_snapshot}",
            created_by_user_id=draft.created_by_user_id,
        )
        db.add(travel_draft)
        db.flush()
        travel_workday.current_draft_revision_id = travel_draft.id
    else:
        travel_workday.operation_id = operation.id
        travel_draft = (
            db.get(WorkdayRevision, travel_workday.current_draft_revision_id)
            if travel_workday.current_draft_revision_id
            else None
        )
        if travel_draft is None:
            travel_draft = ensure_draft(db, travel_workday, draft.created_by_user_id, commit=False)
        travel_draft.work_date = calculation.travel_date
        travel_draft.track_name_snapshot = draft.default_hotel
        travel_draft.start_time = calculation.travel_start
        travel_draft.end_time = calculation.travel_finish
        travel_draft.start_origin = draft.start_origin
        travel_draft.finish_destination = draft.default_hotel
        travel_draft.default_hotel = draft.default_hotel
    for row in db.scalars(select(Assignment).where(Assignment.revision_id == travel_draft.id)):
        db.delete(row)
    race_rows = list(
        db.scalars(
            select(Assignment).where(
                Assignment.revision_id == draft.id,
                Assignment.status == AssignmentStatus.ASSIGNED.value,
                Assignment.person_id.is_not(None),
                Assignment.uses_standard_travel.is_(True),
            )
        )
    )
    seen_people: set[uuid.UUID] = set()
    opted_out_people = set(
        db.scalars(
            select(PersonalWorkdayEntry.person_id).where(
                PersonalWorkdayEntry.workday_id == workday.id,
                PersonalWorkdayEntry.standard_travel_opt_out.is_(True),
            )
        )
    )
    for row in race_rows:
        if row.person_id in seen_people or row.person_id in opted_out_people:
            continue
        seen_people.add(row.person_id)  # type: ignore[arg-type]
        db.add(
            Assignment(
                revision_id=travel_draft.id,
                display_name_snapshot="Travel",
                person_id=row.person_id,
                person_name_snapshot=row.person_name_snapshot,
                status=AssignmentStatus.ASSIGNED.value,
                start_time=calculation.travel_start,
                end_time=calculation.travel_finish,
                transport_mode=row.transport_mode,
                vehicle_id=row.vehicle_id,
                vehicle_name_snapshot=row.vehicle_name_snapshot,
                custom_transport_text=row.custom_transport_text,
                accommodation_name=row.accommodation_name,
                uses_standard_travel=True,
            )
        )


def update_draft_details(
    db: Session,
    *,
    workday_id: uuid.UUID,
    draft_id: uuid.UUID,
    expected_version: int,
    work_date: date,
    track_id: uuid.UUID | None,
    title: str,
    start_time: time | None,
    end_time: time | None,
    on_track_time: time | None,
    first_trial_time: time | None,
    first_race_time: time | None,
    last_race_time: time | None,
    race_count: int | None,
    day_note: str,
    change_reason: str,
    last_trial_time: time | None = None,
) -> None:
    workday, draft = lock_current_draft(
        db, workday_id=workday_id, draft_id=draft_id, expected_version=expected_version
    )
    draft.work_date = work_date
    draft.track_id = track_id
    draft.track_name_snapshot = _validated_track(db, track_id, workday.region_id)
    draft.title = title.strip() or "Workday"
    draft.start_time, draft.end_time, draft.on_track_time = start_time, end_time, on_track_time
    draft.first_trial_time = first_trial_time
    draft.last_trial_time = last_trial_time
    draft.first_race_time, draft.last_race_time, draft.race_count = (
        first_race_time,
        last_race_time,
        race_count,
    )
    draft.day_note, draft.change_reason = day_note.strip(), change_reason.strip()
    workday.lock_version += 1
    db.commit()


def add_assignment(
    db: Session,
    *,
    workday_id: uuid.UUID,
    draft_id: uuid.UUID,
    expected_version: int,
    item: AssignmentInput,
) -> Assignment:
    workday, draft = lock_current_draft(
        db, workday_id=workday_id, draft_id=draft_id, expected_version=expected_version
    )
    position = db.get(BasePosition, item.base_position_id) if item.base_position_id else None
    person = db.get(Person, item.person_id) if item.person_id else None
    if position and position.lifecycle != "ACTIVE":
        raise ValueError("Select an active base position.")
    if person and person.lifecycle != "ACTIVE":
        raise ValueError("Select an active person.")
    if person and db.scalar(
        select(Assignment.id).where(
            Assignment.revision_id == draft.id,
            Assignment.person_id == person.id,
            Assignment.status == AssignmentStatus.ASSIGNED.value,
        )
    ):
        raise ValueError(f"{person.display_name} can hold only one position on this Workday.")
    if item.status not in {status.value for status in AssignmentStatus}:
        raise ValueError("Invalid assignment status.")
    name = position.name if position else "Crew"
    display = f"{name} {item.slot_index}" if item.slot_index else name
    status = AssignmentStatus.ASSIGNED.value if person else item.status
    assignment = Assignment(
        revision_id=draft.id,
        base_position_id=item.base_position_id,
        slot_index=item.slot_index,
        display_name_snapshot=display,
        person_id=item.person_id,
        person_name_snapshot=person.display_name if person else None,
        status=status,
        note=item.note.strip(),
        note_private=item.note_private,
        start_time=item.start_time,
        end_time=item.end_time,
    )
    db.add(assignment)
    workday.lock_version += 1
    db.commit()
    return assignment


def update_assignment(
    db: Session,
    *,
    workday_id: uuid.UUID,
    draft_id: uuid.UUID,
    expected_version: int,
    assignment_id: uuid.UUID,
    base_position_id: uuid.UUID | None | object = _UNCHANGED,
    slot_index: int | None | object = _UNCHANGED,
    person_id: uuid.UUID | None,
    status: str,
    note: str,
    note_private: bool,
    start_time: time | None = None,
    end_time: time | None = None,
) -> Assignment:
    workday, draft = lock_current_draft(
        db, workday_id=workday_id, draft_id=draft_id, expected_version=expected_version
    )
    assignment = db.scalar(
        select(Assignment).where(Assignment.id == assignment_id, Assignment.revision_id == draft.id)
    )
    if assignment is None:
        raise ValueError("Assignment not found in this draft.")
    position = (
        db.get(BasePosition, base_position_id)
        if isinstance(base_position_id, uuid.UUID)
        else None
    )
    if base_position_id is not _UNCHANGED and base_position_id is not None and position is None:
        raise ValueError("Select an active base position.")
    if position and position.lifecycle != "ACTIVE":
        raise ValueError("Select an active base position.")
    person = db.get(Person, person_id) if person_id else None
    if person and person.lifecycle != "ACTIVE":
        raise ValueError("Select an active person.")
    if person and db.scalar(
        select(Assignment.id).where(
            Assignment.revision_id == draft.id,
            Assignment.person_id == person.id,
            Assignment.status == AssignmentStatus.ASSIGNED.value,
            Assignment.id != assignment.id,
        )
    ):
        raise ValueError(f"{person.display_name} can hold only one position on this Workday.")
    if status not in {item.value for item in AssignmentStatus}:
        raise ValueError("Invalid assignment status.")
    if base_position_id is not _UNCHANGED:
        assignment.base_position_id = base_position_id  # type: ignore[assignment]
    if slot_index is not _UNCHANGED:
        assignment.slot_index = slot_index  # type: ignore[assignment]
    if base_position_id is not _UNCHANGED or slot_index is not _UNCHANGED:
        position = db.get(BasePosition, assignment.base_position_id) if assignment.base_position_id else None
        position_name = position.name if position else "Crew"
        assignment.display_name_snapshot = (
            f"{position_name} {assignment.slot_index}" if assignment.slot_index else position_name
        )
    assignment.person_id = person_id
    assignment.person_name_snapshot = person.display_name if person else None
    assignment.status = AssignmentStatus.ASSIGNED.value if person else status
    assignment.note = note.strip()
    assignment.note_private = note_private
    assignment.start_time = start_time
    assignment.end_time = end_time
    workday.lock_version += 1
    db.commit()
    return assignment


def save_draft(
    db: Session,
    *,
    workday_id: uuid.UUID,
    draft_id: uuid.UUID,
    expected_version: int,
    details: DraftDetailsInput,
    assignments: list[DraftAssignmentInput],
    commit: bool = True,
) -> WorkdayRevision:
    """Atomically replace the editable draft state while preserving stable slot identities."""
    workday, draft = lock_current_draft(
        db, workday_id=workday_id, draft_id=draft_id, expected_version=expected_version
    )
    if details.race_count is not None and not 0 <= details.race_count <= 99:
        raise ValueError("Race count must be between 0 and 99.")

    assignments = [
        item
        for item in assignments
        if not (
            item.assignment_id is None
            and item.base_position_id is None
            and item.person_id is None
            and item.status == AssignmentStatus.TBC.value
            and item.slot_index is None
            and not item.note.strip()
            and item.start_time is None
            and item.end_time is None
            and item.transport_mode == TRANSPORT_UNASSIGNED
            and not item.custom_transport_text.strip()
            and not item.accommodation_name.strip()
        )
    ]
    hotels_by_person: dict[uuid.UUID, str] = {}
    travel_plan_by_person: dict[uuid.UUID, tuple[bool, int | None]] = {}
    for item in assignments:
        hotel = item.accommodation_name.strip()
        if not item.person_id:
            continue
        if hotel:
            existing_hotel = hotels_by_person.get(item.person_id)
            if existing_hotel and existing_hotel.casefold() != hotel.casefold():
                raise ValueError("Use one consistent hotel for each person on this workday.")
            hotels_by_person[item.person_id] = hotel
        plan = (item.uses_standard_travel, item.hotel_to_track_minutes_override)
        existing_plan = travel_plan_by_person.get(item.person_id)
        if existing_plan is not None and existing_plan != plan:
            raise ValueError("Use one consistent standard-travel plan for each person.")
        travel_plan_by_person[item.person_id] = plan

    active_positions = {
        row.id: row
        for row in db.scalars(select(BasePosition).where(BasePosition.lifecycle == "ACTIVE"))
    }
    active_people = {
        row.id: row for row in db.scalars(select(Person).where(Person.lifecycle == "ACTIVE"))
    }
    active_vehicles = {
        row.id: row for row in db.scalars(select(Vehicle).where(Vehicle.lifecycle == "ACTIVE"))
    }
    current = {
        row.id: row
        for row in db.scalars(select(Assignment).where(Assignment.revision_id == draft.id))
    }
    submitted_ids = [row.assignment_id for row in assignments if row.assignment_id is not None]
    if len(submitted_ids) != len(set(submitted_ids)) or any(
        row_id not in current for row_id in submitted_ids
    ):
        raise ValueError("One or more assignment rows do not belong to this draft.")

    track_name = _validated_track(db, details.track_id, workday.region_id)
    valid_categories = {item.value for item in WorkdayCategory}
    valid_disciplines = {item.value for item in RacingDiscipline}
    if details.category not in valid_categories:
        raise ValueError("Select a valid day type.")
    discipline = details.racing_discipline if details.category in {"RACE_DAY", "TRIALS"} else None
    if discipline not in valid_disciplines | {None}:
        raise ValueError("Select a valid racing discipline.")
    if details.category in {"RACE_DAY", "TRIALS"} and discipline is None:
        raise ValueError("Select Thoroughbred or Harness.")
    if workday.current_published_revision_id and (
        details.category != workday.category or discipline != workday.racing_discipline
    ):
        raise ValueError("Day type is fixed after first publication.")
    workday.category = details.category
    workday.racing_discipline = discipline
    normal_statuses = {
        AssignmentStatus.ASSIGNED.value,
        AssignmentStatus.OPEN.value,
        AssignmentStatus.TBC.value,
    }
    prepared: list[tuple[DraftAssignmentInput, BasePosition | None, Person | None]] = []
    assigned_people: set[uuid.UUID] = set()
    for item in assignments:
        if item.assignment_id is None and item.base_position_id is None:
            raise ValueError("Select a position for each new assignment.")
        position = active_positions.get(item.base_position_id) if item.base_position_id else None
        person = active_people.get(item.person_id) if item.person_id else None
        if item.base_position_id and position is None:
            raise ValueError("Select an active base position.")
        if item.person_id and person is None:
            raise ValueError("Select an active person.")
        prior = current.get(item.assignment_id) if item.assignment_id else None
        preserved_manager_action = bool(
            prior
            and prior.status == AssignmentStatus.MANAGER_ACTION_REQUIRED.value
            and item.status == AssignmentStatus.MANAGER_ACTION_REQUIRED.value
            and item.person_id is None
        )
        if item.status not in normal_statuses and not preserved_manager_action:
            raise ValueError("Invalid assignment status.")
        if item.person_id is None and item.status == AssignmentStatus.ASSIGNED.value:
            raise ValueError("Choose a person, Open position, or Unassigned.")
        if person and item.person_id in assigned_people:
            raise ValueError(f"{person.display_name} can hold only one position on this Workday.")
        if person:
            assigned_people.add(person.id)
        if item.slot_index is not None and item.slot_index < 1:
            raise ValueError("Slot index must be at least 1.")
        if item.transport_mode not in TRANSPORT_MODES:
            raise ValueError("Select a valid transport option.")
        vehicle = active_vehicles.get(item.vehicle_id) if item.vehicle_id else None
        if item.transport_mode == TRANSPORT_VEHICLE and vehicle is None:
            raise ValueError("Select an active vehicle for vehicle transport.")
        if item.transport_mode != TRANSPORT_VEHICLE and item.vehicle_id is not None:
            raise ValueError("Vehicle selection does not match the transport option.")
        if item.transport_mode == TRANSPORT_CUSTOM and not item.custom_transport_text.strip():
            raise ValueError("Enter the custom transport arrangement.")
        prepared.append((item, position, person))

    draft.work_date = details.work_date
    draft.track_id = details.track_id
    draft.track_name_snapshot = track_name
    draft.title = details.title.strip() or workday.category.replace("_", " ").title()
    draft.start_time, draft.end_time, draft.on_track_time = (
        details.start_time,
        details.end_time,
        details.on_track_time,
    )
    draft.end_time_is_override = details.end_time_is_override
    draft.first_trial_time = details.first_trial_time
    draft.last_trial_time = details.last_trial_time
    draft.first_race_time = details.first_race_time
    draft.last_race_time = details.last_race_time
    draft.race_count = details.race_count
    draft.day_note = details.day_note.strip()
    draft.change_reason = details.change_reason.strip()
    draft.start_origin = details.start_origin.strip()
    draft.finish_destination = details.finish_destination.strip()
    draft.standard_travel_enabled = details.standard_travel_enabled
    draft.travel_departure_time = details.travel_departure_time
    draft.travel_to_hotel_minutes = details.travel_to_hotel_minutes
    draft.default_hotel = details.default_hotel.strip()
    draft.hotel_to_track_minutes = details.hotel_to_track_minutes
    draft.return_travel_minutes = details.return_travel_minutes
    draft.pack_up_minutes = details.pack_up_minutes

    kept_ids = set(submitted_ids)
    for assignment_id, assignment in current.items():
        if assignment_id not in kept_ids:
            db.delete(assignment)
    for item, position, person in prepared:
        assignment = current.get(item.assignment_id) if item.assignment_id else None
        if assignment is None:
            assignment = Assignment(revision_id=draft.id)
            db.add(assignment)
        position_name = position.name if position else "Crew"
        assignment.base_position_id = item.base_position_id
        assignment.slot_index = item.slot_index
        assignment.display_name_snapshot = (
            f"{position_name} {item.slot_index}" if item.slot_index else position_name
        )
        assignment.person_id = item.person_id
        assignment.person_name_snapshot = person.display_name if person else None
        assignment.status = AssignmentStatus.ASSIGNED.value if person else item.status
        assignment.note = item.note.strip()
        assignment.note_private = item.note_private
        assignment.start_time = item.start_time
        assignment.end_time = item.end_time
        assignment.transport_mode = item.transport_mode
        assignment.vehicle_id = item.vehicle_id
        assignment.vehicle_name_snapshot = (
            active_vehicles[item.vehicle_id].name if item.vehicle_id else None
        )
        assignment.custom_transport_text = item.custom_transport_text.strip()
        assignment.accommodation_name = (
            hotels_by_person.get(item.person_id)
            if item.person_id
            else item.accommodation_name.strip()
        ) or None
        assignment.uses_standard_travel = item.uses_standard_travel
        assignment.hotel_to_track_minutes_override = item.hotel_to_track_minutes_override
        assignment.finish_destination_override = item.finish_destination_override.strip() or None
        assignment.return_travel_minutes_override = item.return_travel_minutes_override
    workday.lock_version += 1
    _sync_standard_travel(db, workday, draft)
    if commit:
        db.commit()
    return draft


def remove_assignment(
    db: Session,
    *,
    workday_id: uuid.UUID,
    draft_id: uuid.UUID,
    expected_version: int,
    assignment_id: uuid.UUID,
) -> None:
    workday, draft = lock_current_draft(
        db, workday_id=workday_id, draft_id=draft_id, expected_version=expected_version
    )
    assignment = db.scalar(
        select(Assignment).where(Assignment.id == assignment_id, Assignment.revision_id == draft.id)
    )
    if assignment is None:
        raise ValueError("Assignment not found in this draft.")
    db.delete(assignment)
    workday.lock_version += 1
    db.commit()


def preview_diff(db: Session, workday: Workday, draft: WorkdayRevision) -> list[PublicationChange]:
    previous = (
        db.get(WorkdayRevision, workday.current_published_revision_id)
        if workday.current_published_revision_id
        else None
    )
    return publication_diff(db, previous, draft)


def publish(
    db: Session,
    workday_id: uuid.UUID,
    draft_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    expected_version: int,
    confirm_conflicts: bool = False,
) -> WorkdayRevision:
    with db.begin():
        workday = db.scalar(
            select(Workday)
            .where(Workday.id == workday_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        draft = db.get(WorkdayRevision, draft_id)
        if not workday or workday.lock_version != expected_version:
            raise PublishConflict(
                "This roster was changed by someone else. Refresh the builder before publishing."
            )
        if not workday or not draft or workday.current_draft_revision_id != draft.id:
            raise PublishConflict("This draft is no longer current. Refresh before publishing.")
        if draft.state != RevisionState.DRAFT.value:
            raise PublishConflict("This revision has already been published.")
        if draft.based_on_revision_id != workday.current_published_revision_id:
            raise PublishConflict("The published roster changed while this draft was being edited.")
        from app.auth.policy import actor_for, can_manage_region

        actor_record = db.get(User, actor_user_id)
        actor = actor_for(db, actor_record) if actor_record else None
        visible_regions = None if actor and actor.is_admin else {
            region_id
            for region_id in (actor.regional_roles if actor else {})
            if actor and can_manage_region(actor, region_id)
        }
        conflicts = publication_conflicts(
            db, workday, draft, visible_region_ids=visible_regions
        )
        generated_candidate = (
            db.scalar(select(Workday).where(Workday.generated_from_workday_id == workday.id))
            if draft.standard_travel_enabled
            else None
        )
        generated_draft = (
            db.get(WorkdayRevision, generated_candidate.current_draft_revision_id)
            if generated_candidate and generated_candidate.current_draft_revision_id
            else None
        )
        if generated_candidate and generated_draft:
            conflicts.extend(
                publication_conflicts(
                    db,
                    generated_candidate,
                    generated_draft,
                    visible_region_ids=visible_regions,
                    include_inactive_current=True,
                )
            )
        if conflicts and not confirm_conflicts:
            raise PublishConflict(
                "Roster conflicts changed or remain unresolved. Return to Preview and explicitly confirm Publish anyway."
            )
        previous = (
            db.get(WorkdayRevision, workday.current_published_revision_id)
            if workday.current_published_revision_id
            else None
        )
        changes = publication_diff(db, previous, draft)
        draft.state = RevisionState.PUBLISHED.value
        draft.published_at = utcnow()
        draft.published_by_user_id = actor_user_id
        workday.current_published_revision_id = draft.id
        workday.current_draft_revision_id = None
        workday.lock_version += 1
        linked_travel: tuple[Workday, WorkdayRevision] | None = None
        if workday.operation_id:
            travel_workday = db.scalar(
                select(Workday)
                .where(Workday.generated_from_workday_id == workday.id)
                .with_for_update()
            )
            travel_draft = (
                db.get(WorkdayRevision, travel_workday.current_draft_revision_id)
                if travel_workday and travel_workday.current_draft_revision_id
                else None
            )
            if travel_workday and not draft.standard_travel_enabled:
                if travel_workday.status != WorkdayStatus.CANCELLED.value:
                    before = travel_workday.status
                    travel_workday.status = WorkdayStatus.CANCELLED.value
                    travel_workday.lock_version += 1
                    record_audit(
                        db,
                        "workday.status_changed",
                        "workday",
                        travel_workday.id,
                        actor_user_id,
                        region_id=travel_workday.region_id,
                        detail={"from": before, "to": WorkdayStatus.CANCELLED.value, "generated": True},
                    )
                    record_event(
                        db,
                        event_key=f"generated-travel-cancelled:{travel_workday.id}:{travel_workday.lock_version}",
                        event_type="ROSTER_PUBLISHED",
                        region_id=travel_workday.region_id,
                        workday_id=travel_workday.id,
                        payload={
                            "revision_id": str(travel_workday.current_published_revision_id),
                            "previous_revision_id": None,
                            "summary": "Generated Travel Day was cancelled with its parent operation.",
                        },
                    )
            elif travel_workday and travel_draft:
                reinstated = travel_workday.status != WorkdayStatus.SCHEDULED.value
                travel_workday.status = WorkdayStatus.SCHEDULED.value
                if travel_draft.state != RevisionState.DRAFT.value:
                    raise PublishConflict("The generated Travel Day is no longer an editable draft.")
                travel_draft.state = RevisionState.PUBLISHED.value
                travel_draft.published_at = utcnow()
                travel_draft.published_by_user_id = actor_user_id
                travel_workday.current_published_revision_id = travel_draft.id
                travel_workday.current_draft_revision_id = None
                travel_workday.lock_version += 1
                linked_travel = (travel_workday, travel_draft)
                if reinstated:
                    record_audit(
                        db,
                        "workday.status_changed",
                        "workday",
                        travel_workday.id,
                        actor_user_id,
                        region_id=travel_workday.region_id,
                        detail={"to": WorkdayStatus.SCHEDULED.value, "generated": True},
                    )
        assigned = list(
            db.scalars(
                select(Assignment).where(
                    Assignment.revision_id == draft.id,
                    Assignment.status == AssignmentStatus.ASSIGNED.value,
                    Assignment.person_id.is_not(None),
                    Assignment.base_position_id.is_not(None),
                )
            )
        )
        for row in assigned:
            capability = set_signal(
                db,
                row.person_id,
                row.base_position_id,
                CapabilitySignal.WORKED.value,
                actor_user_id,  # type: ignore[arg-type]
            )
            capability.source_revision_id = draft.id
        applications = list(
            db.scalars(
                select(OpenPositionApplication).where(
                    OpenPositionApplication.revision_id == draft.based_on_revision_id
                )
            )
        )
        draft_slots = {
            row.slot_key: row
            for row in db.scalars(select(Assignment).where(Assignment.revision_id == draft.id))
        }
        for application in applications:
            selected = (
                application.status == OpenApplicationStatus.SELECTED.value
                and application.selected_draft_revision_id == draft.id
                and draft_slots.get(application.slot_key) is not None
                and draft_slots[application.slot_key].person_id == application.person_id
            )
            application.status = (
                OpenApplicationStatus.ACCEPTED.value
                if selected
                else OpenApplicationStatus.NOT_SELECTED.value
            )
            application.decided_at = utcnow()
        actor = db.get(User, actor_user_id)
        actor_name = actor.display_name if actor else "A roster manager"
        summaries = [f"{actor_name} {change.summary[0].lower() + change.summary[1:]}" for change in changes]
        if not summaries:
            summaries = [f"{actor_name} published revision {draft.revision_number} with no crew-visible changes."]
        if draft.change_reason:
            summaries.append(f"{actor_name} recorded a reason for this publication.")
        db.add_all(
            [
                HumanChange(
                    workday_id=workday.id,
                    revision_id=draft.id,
                    actor_user_id=actor_user_id,
                    summary=summary,
                )
                for summary in summaries
            ]
        )
        record_audit(
            db,
            "workday.published",
            "workday",
            workday.id,
            actor_user_id,
            region_id=workday.region_id,
            detail={"revision": draft.revision_number},
        )
        record_event(
            db,
            event_key=f"roster-published:{workday.id}:{draft.id}",
            event_type="ROSTER_PUBLISHED",
            region_id=workday.region_id,
            workday_id=workday.id,
            payload={
                "revision_id": str(draft.id),
                "previous_revision_id": str(previous.id) if previous else None,
                "summary": summaries[0],
            },
        )
        if linked_travel:
            travel_workday, travel_draft = linked_travel
            record_audit(
                db,
                "workday.published",
                "workday",
                travel_workday.id,
                actor_user_id,
                region_id=travel_workday.region_id,
                detail={"revision": travel_draft.revision_number, "generated": True},
            )
            record_event(
                db,
                event_key=f"roster-published:{travel_workday.id}:{travel_draft.id}",
                event_type="ROSTER_PUBLISHED",
                region_id=travel_workday.region_id,
                workday_id=travel_workday.id,
                payload={
                    "revision_id": str(travel_draft.id),
                    "previous_revision_id": (
                        str(travel_draft.based_on_revision_id)
                        if travel_draft.based_on_revision_id
                        else None
                    ),
                    "summary": "Generated Travel Day published with its Race Day.",
                },
            )
        open_rows = (
            db.scalars(
                select(Assignment).where(
                    Assignment.revision_id == draft.id,
                    Assignment.status == AssignmentStatus.OPEN.value,
                    Assignment.base_position_id.is_not(None),
                )
            )
            if workday.status == WorkdayStatus.SCHEDULED.value
            else ()
        )
        for row in open_rows:
            record_event(
                db,
                event_key=f"open-position:{workday.id}:{draft.id}:{row.slot_key}",
                event_type="OPEN_POSITION_AVAILABLE",
                region_id=workday.region_id,
                workday_id=workday.id,
                slot_key=row.slot_key,
                payload={"base_position_id": str(row.base_position_id)},
            )
    return draft


def decline_published_assignment(
    db: Session,
    *,
    workday_id: uuid.UUID,
    slot_key: uuid.UUID,
    person_id: uuid.UUID,
    actor_user_id: uuid.UUID,
) -> WorkdayRevision:
    """Create a new immutable publication that atomically removes the declining employee."""
    with db.begin():
        workday = db.scalar(select(Workday).where(Workday.id == workday_id).with_for_update())
        if not workday or not workday.current_published_revision_id:
            raise ValueError("Published assignment not found.")
        published = db.get(WorkdayRevision, workday.current_published_revision_id)
        region = db.get(Region, workday.region_id)
        target = db.scalar(
            select(Assignment).where(
                Assignment.revision_id == published.id,
                Assignment.slot_key == slot_key,
                Assignment.person_id == person_id,
                Assignment.status == AssignmentStatus.ASSIGNED.value,
            )
        ) if published else None
        if not published or not region or not target:
            raise ValueError("This assignment is no longer available to decline.")
        next_number = (
            db.scalar(
                select(func.max(WorkdayRevision.revision_number)).where(
                    WorkdayRevision.workday_id == workday.id
                )
            )
            or 0
        ) + 1
        new_revision = WorkdayRevision(
            workday_id=workday.id,
            revision_number=next_number,
            state=RevisionState.PUBLISHED.value,
            based_on_revision_id=published.id,
            work_date=published.work_date,
            track_id=published.track_id,
            track_name_snapshot=published.track_name_snapshot,
            title=published.title,
            start_time=published.start_time,
            end_time=published.end_time,
            end_time_is_override=published.end_time_is_override,
            on_track_time=published.on_track_time,
            first_trial_time=published.first_trial_time,
            last_trial_time=published.last_trial_time,
            first_race_time=published.first_race_time,
            last_race_time=published.last_race_time,
            race_count=published.race_count,
            start_origin=published.start_origin,
            finish_destination=published.finish_destination,
            standard_travel_enabled=published.standard_travel_enabled,
            travel_departure_time=published.travel_departure_time,
            travel_to_hotel_minutes=published.travel_to_hotel_minutes,
            default_hotel=published.default_hotel,
            hotel_to_track_minutes=published.hotel_to_track_minutes,
            return_travel_minutes=published.return_travel_minutes,
            pack_up_minutes=published.pack_up_minutes,
            day_note=published.day_note,
            change_reason="Employee declined assignment",
            created_by_user_id=actor_user_id,
            published_by_user_id=actor_user_id,
            published_at=utcnow(),
        )
        db.add(new_revision)
        db.flush()
        for old in db.scalars(select(Assignment).where(Assignment.revision_id == published.id)):
            is_target = old.slot_key == slot_key
            status = (
                AssignmentStatus.OPEN.value
                if region.decline_policy == DeclinePolicy.OPEN_IMMEDIATELY.value
                else AssignmentStatus.MANAGER_ACTION_REQUIRED.value
            ) if is_target else old.status
            db.add(
                Assignment(
                    revision_id=new_revision.id,
                    slot_key=old.slot_key,
                    base_position_id=old.base_position_id,
                    slot_index=old.slot_index,
                    display_name_snapshot=old.display_name_snapshot,
                    person_id=None if is_target else old.person_id,
                    person_name_snapshot=None if is_target else old.person_name_snapshot,
                    status=status,
                    start_time=old.start_time,
                    end_time=old.end_time,
                    note=old.note,
                    note_private=old.note_private,
                    vehicle_id=old.vehicle_id,
                    vehicle_name_snapshot=old.vehicle_name_snapshot,
                    transport_mode=old.transport_mode,
                    custom_transport_text=old.custom_transport_text,
                    accommodation_name=old.accommodation_name,
                    uses_standard_travel=old.uses_standard_travel,
                    hotel_to_track_minutes_override=old.hotel_to_track_minutes_override,
                    finish_destination_override=old.finish_destination_override,
                    return_travel_minutes_override=old.return_travel_minutes_override,
                )
            )
        for item in db.scalars(select(ProgrammeItem).where(ProgrammeItem.revision_id == published.id)):
            db.add(
                ProgrammeItem(
                    revision_id=new_revision.id,
                    kind=item.kind,
                    sequence=item.sequence,
                    operational_time=item.operational_time,
                    source_time=item.source_time,
                    source_provider=item.source_provider,
                    source_retrieved_at=item.source_retrieved_at,
                    manual_override=item.manual_override,
                )
            )
        for indicator in db.scalars(
            select(AllowanceIndicator).where(AllowanceIndicator.revision_id == published.id)
        ):
            db.add(
                AllowanceIndicator(
                    revision_id=new_revision.id,
                    person_id=indicator.person_id,
                    kind=indicator.kind,
                    entitlement_hours=indicator.entitlement_hours,
                    explanation=indicator.explanation,
                )
            )
        for application in db.scalars(
            select(OpenPositionApplication).where(
                OpenPositionApplication.revision_id == published.id,
                OpenPositionApplication.status.in_(
                    [
                        OpenApplicationStatus.APPLIED.value,
                        OpenApplicationStatus.SELECTED.value,
                    ]
                ),
                OpenPositionApplication.slot_key != slot_key,
            )
        ):
            db.add(
                OpenPositionApplication(
                    revision_id=new_revision.id,
                    slot_key=application.slot_key,
                    person_id=application.person_id,
                    status=OpenApplicationStatus.APPLIED.value,
                    created_at=application.created_at,
                )
            )
            application.status = OpenApplicationStatus.CLOSED.value
            application.decided_at = utcnow()
        workday.current_published_revision_id = new_revision.id
        # A direct authoritative employee change makes any Manager draft stale. Preserve its rows
        # for audit/recovery, but detach it so the next edit starts from this publication.
        workday.current_draft_revision_id = None
        workday.lock_version += 1
        status_label = "Open" if region.decline_policy == DeclinePolicy.OPEN_IMMEDIATELY.value else "Manager action required"
        db.add(
            HumanChange(
                workday_id=workday.id,
                revision_id=new_revision.id,
                actor_user_id=actor_user_id,
                summary=f"{target.person_name_snapshot or 'Crew member'} declined {target.display_name_snapshot}; position is {status_label.lower()}.",
            )
        )
        record_audit(
            db,
            "assignment.declined",
            "workday",
            workday.id,
            actor_user_id,
            region_id=workday.region_id,
            detail={"slot_key": str(slot_key), "result": status_label},
        )
        event_type = (
            "OPEN_POSITION_AVAILABLE"
            if region.decline_policy == DeclinePolicy.OPEN_IMMEDIATELY.value
            else "MANAGER_ACTION_REQUIRED"
        )
        record_event(
            db,
            event_key=f"assignment-declined:{workday.id}:{new_revision.id}:{slot_key}",
            event_type=event_type,
            region_id=workday.region_id,
            workday_id=workday.id,
            slot_key=slot_key,
            payload={"base_position_id": str(target.base_position_id), "policy": region.decline_policy},
        )
    return new_revision
