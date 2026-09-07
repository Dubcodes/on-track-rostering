from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.policy import Actor, can_apply_for_open_position
from app.catalog.models import BasePosition
from app.core.enums import AssignmentStatus, OpenApplicationStatus
from app.identity.models import Person
from app.positions.service import eligibility
from app.rostering.models import Assignment, OpenPositionApplication, Workday, WorkdayRevision


@dataclass(frozen=True)
class AvailablePosition:
    workday: Workday
    revision: WorkdayRevision
    assignment: Assignment
    position: BasePosition
    eligibility_reason: str


def available_positions(db: Session, actor: Actor) -> list[AvailablePosition]:
    if actor.person_id is None:
        return []
    rows = db.execute(
        select(Workday, WorkdayRevision, Assignment, BasePosition)
        .join(WorkdayRevision, Workday.current_published_revision_id == WorkdayRevision.id)
        .join(Assignment, Assignment.revision_id == WorkdayRevision.id)
        .join(BasePosition, BasePosition.id == Assignment.base_position_id)
        .where(
            Assignment.status == AssignmentStatus.OPEN.value,
            WorkdayRevision.work_date >= date.today(),
        )
        .order_by(WorkdayRevision.work_date, Assignment.display_name_snapshot)
    ).all()
    result: list[AvailablePosition] = []
    for workday, revision, assignment, position in rows:
        if not can_apply_for_open_position(actor, workday.region_id):
            continue
        eligible, reason = eligibility(db, actor.person_id, position.id)
        if eligible:
            result.append(AvailablePosition(workday, revision, assignment, position, reason))
    return result


def apply_for_position(
    db: Session,
    *,
    actor: Actor,
    workday_id: uuid.UUID,
    slot_key: uuid.UUID,
) -> OpenPositionApplication:
    workday = db.scalar(select(Workday).where(Workday.id == workday_id).with_for_update())
    if not workday or not workday.current_published_revision_id:
        raise ValueError("Open position is no longer available.")
    if not can_apply_for_open_position(actor, workday.region_id) or actor.person_id is None:
        raise PermissionError("Employee regional access is required.")
    assignment = db.scalar(
        select(Assignment).where(
            Assignment.revision_id == workday.current_published_revision_id,
            Assignment.slot_key == slot_key,
            Assignment.status == AssignmentStatus.OPEN.value,
            Assignment.base_position_id.is_not(None),
        )
    )
    if not assignment or assignment.base_position_id is None:
        raise ValueError("Open position is no longer available.")
    eligible, reason = eligibility(db, actor.person_id, assignment.base_position_id)
    if not eligible:
        raise PermissionError(reason)
    existing = db.scalar(
        select(OpenPositionApplication).where(
            OpenPositionApplication.revision_id == assignment.revision_id,
            OpenPositionApplication.slot_key == assignment.slot_key,
            OpenPositionApplication.person_id == actor.person_id,
        )
    )
    if existing:
        if existing.status in {
            OpenApplicationStatus.APPLIED.value,
            OpenApplicationStatus.SELECTED.value,
        }:
            return existing
        raise ValueError("This application is already closed.")
    application = OpenPositionApplication(
        revision_id=assignment.revision_id,
        slot_key=assignment.slot_key,
        person_id=actor.person_id,
        status=OpenApplicationStatus.APPLIED.value,
    )
    db.add(application)
    db.commit()
    return application


def select_application(
    db: Session,
    *,
    workday: Workday,
    draft: WorkdayRevision,
    application: OpenPositionApplication,
) -> Assignment:
    if workday.current_published_revision_id != application.revision_id:
        raise ValueError("The published roster changed after this application was made.")
    if application.status != OpenApplicationStatus.APPLIED.value:
        raise ValueError("This application is no longer awaiting a decision.")
    published_slot = db.scalar(
        select(Assignment).where(
            Assignment.revision_id == application.revision_id,
            Assignment.slot_key == application.slot_key,
            Assignment.status == AssignmentStatus.OPEN.value,
        )
    )
    draft_slot = db.scalar(
        select(Assignment).where(
            Assignment.revision_id == draft.id,
            Assignment.slot_key == application.slot_key,
        )
    )
    person = db.get(Person, application.person_id)
    if not published_slot or not draft_slot or not person or person.lifecycle != "ACTIVE":
        raise ValueError("The position or applicant is no longer available.")
    draft_slot.person_id = person.id
    draft_slot.person_name_snapshot = person.display_name
    draft_slot.status = AssignmentStatus.ASSIGNED.value
    application.status = OpenApplicationStatus.SELECTED.value
    application.selected_draft_revision_id = draft.id
    db.commit()
    return draft_slot
