from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, time

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.audit.models import HumanChange
from app.audit.service import record_audit
from app.catalog.models import BasePosition, Region, Track
from app.core.enums import (
    AssignmentStatus,
    CapabilitySignal,
    DeclinePolicy,
    OpenApplicationStatus,
    RevisionState,
)
from app.core.time import utcnow
from app.identity.models import Person, User
from app.notifications.service import record_event
from app.positions.service import set_signal
from app.rostering.diff import PublicationChange, publication_diff
from app.rostering.models import (
    AllowanceIndicator,
    Assignment,
    OpenPositionApplication,
    ProgrammeItem,
    Workday,
    WorkdayRevision,
)


class PublishConflict(ValueError):
    pass


class DraftConflict(ValueError):
    pass


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


def _track_snapshot(db: Session, track_id: uuid.UUID | None) -> tuple[str, str]:
    track = db.get(Track, track_id) if track_id else None
    return (track.name, track.display_colour) if track else ("To be confirmed", "#667085")


def _validated_track(db: Session, track_id: uuid.UUID | None, region_id: uuid.UUID) -> tuple[str, str]:
    if track_id is None:
        return "To be confirmed", "#667085"
    track = db.get(Track, track_id)
    if not track or track.lifecycle != "ACTIVE" or track.region_id != region_id:
        raise ValueError("Select an active track from the workday region.")
    return track.name, track.display_colour


def create_workday(
    db: Session,
    *,
    region_id: uuid.UUID,
    category: str,
    work_date: date,
    track_id: uuid.UUID | None,
    title: str,
    actor_user_id: uuid.UUID,
) -> Workday:
    track_name, track_colour = _validated_track(db, track_id, region_id)
    workday = Workday(region_id=region_id, category=category, created_by_user_id=actor_user_id)
    db.add(workday)
    db.flush()
    draft = WorkdayRevision(
        workday_id=workday.id,
        revision_number=1,
        state=RevisionState.DRAFT.value,
        work_date=work_date,
        track_id=track_id,
        track_name_snapshot=track_name,
        track_colour_snapshot=track_colour,
        title=title.strip() or category.replace("_", " ").title(),
        created_by_user_id=actor_user_id,
    )
    db.add(draft)
    db.flush()
    workday.current_draft_revision_id = draft.id
    record_audit(db, "workday.created", "workday", workday.id, actor_user_id, region_id=region_id)
    db.commit()
    return workday


def ensure_draft(db: Session, workday: Workday, actor_user_id: uuid.UUID) -> WorkdayRevision:
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
        track_colour_snapshot=published.track_colour_snapshot,
        title=published.title,
        start_time=published.start_time,
        end_time=published.end_time,
        on_track_time=published.on_track_time,
        first_trial_time=published.first_trial_time,
        first_race_time=published.first_race_time,
        last_race_time=published.last_race_time,
        race_count=published.race_count,
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
                accommodation_name=old.accommodation_name,
            )
        )
    workday.current_draft_revision_id = draft.id
    workday.lock_version += 1
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
) -> None:
    workday, draft = lock_current_draft(
        db, workday_id=workday_id, draft_id=draft_id, expected_version=expected_version
    )
    draft.work_date = work_date
    draft.track_id = track_id
    draft.track_name_snapshot, draft.track_colour_snapshot = _validated_track(db, track_id, workday.region_id)
    draft.title = title.strip() or "Workday"
    draft.start_time, draft.end_time, draft.on_track_time = start_time, end_time, on_track_time
    draft.first_trial_time = first_trial_time
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
    person = db.get(Person, person_id) if person_id else None
    if person and person.lifecycle != "ACTIVE":
        raise ValueError("Select an active person.")
    if status not in {item.value for item in AssignmentStatus}:
        raise ValueError("Invalid assignment status.")
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
        for row in db.scalars(
            select(Assignment).where(
                Assignment.revision_id == draft.id,
                Assignment.status == AssignmentStatus.OPEN.value,
                Assignment.base_position_id.is_not(None),
            )
        ):
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
            track_colour_snapshot=published.track_colour_snapshot,
            title=published.title,
            start_time=published.start_time,
            end_time=published.end_time,
            on_track_time=published.on_track_time,
            first_trial_time=published.first_trial_time,
            first_race_time=published.first_race_time,
            last_race_time=published.last_race_time,
            race_count=published.race_count,
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
                    accommodation_name=old.accommodation_name,
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
