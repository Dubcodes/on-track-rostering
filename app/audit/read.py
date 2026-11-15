from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit.models import AuditEvent
from app.auth.policy import Actor
from app.core.enums import AssignmentStatus, RevisionState, Role
from app.identity.models import Person
from app.rostering.diff import PublicationChange, publication_diff
from app.rostering.models import Assignment, Workday, WorkdayRevision

ROSTER_AUDIT_ACTIONS = {
    "assignment.declined",
    "workday.published",
    "workday.rescheduled",
    "workday.status_changed",
}
PRIVATE_CHANGE_TERMS = {
    "accommodation",
    "address",
    "custom transport",
    "default hotel",
    "finish destination",
    "note",
}


@dataclass(frozen=True)
class EmployeeImpact:
    person_id: uuid.UUID
    person_name: str
    summary: str


@dataclass(frozen=True)
class AuditRecord:
    event: AuditEvent
    impacts: tuple[EmployeeImpact, ...]
    workday: Workday | None
    revision: WorkdayRevision | None
    region_id: uuid.UUID | None
    cross_region_employee_oversight: bool
    visible_detail: dict[str, object]


def managed_region_ids(actor: Actor) -> set[uuid.UUID] | None:
    if actor.is_admin:
        return None
    return {
        region_id
        for region_id, roles in actor.regional_roles.items()
        if Role.MANAGER.value in roles
    }


def _event_workday(db: Session, event: AuditEvent) -> Workday | None:
    if event.target_type != "workday" or not event.target_id:
        return None
    try:
        workday_id = uuid.UUID(event.target_id)
    except (TypeError, ValueError):
        return None
    return db.get(Workday, workday_id)


def _revision_for_event(
    db: Session, event: AuditEvent, workday: Workday
) -> WorkdayRevision | None:
    revision_id = event.detail.get("revision_id")
    if revision_id:
        try:
            revision = db.get(WorkdayRevision, uuid.UUID(str(revision_id)))
        except (TypeError, ValueError):
            revision = None
        if (
            revision
            and revision.workday_id == workday.id
            and revision.state == RevisionState.PUBLISHED.value
        ):
            return revision
    revision_number = event.detail.get("revision")
    if revision_number is not None:
        try:
            number = int(str(revision_number))
        except ValueError:
            number = -1
        revision = db.scalar(
            select(WorkdayRevision).where(
                WorkdayRevision.workday_id == workday.id,
                WorkdayRevision.revision_number == number,
            )
        )
        if revision and revision.state == RevisionState.PUBLISHED.value:
            return revision
    # Never project a historical event through the Workday's *current*
    # publication. Older audit rows without a stable revision reference remain
    # visible to the Workday Region, but cannot safely grant cross-region
    # employee visibility.
    return None


def _previous_publication(
    db: Session, revision: WorkdayRevision
) -> WorkdayRevision | None:
    return db.scalar(
        select(WorkdayRevision)
        .where(
            WorkdayRevision.workday_id == revision.workday_id,
            WorkdayRevision.state == RevisionState.PUBLISHED.value,
            WorkdayRevision.revision_number < revision.revision_number,
        )
        .order_by(WorkdayRevision.revision_number.desc())
        .limit(1)
    )


def _assignment_map(db: Session, revision: WorkdayRevision | None) -> dict[uuid.UUID, Assignment]:
    if revision is None:
        return {}
    return {
        row.slot_key: row
        for row in db.scalars(
            select(Assignment).where(Assignment.revision_id == revision.id)
        )
    }


def _assigned_people(rows: dict[uuid.UUID, Assignment]) -> dict[uuid.UUID, str]:
    return {
        row.person_id: row.person_name_snapshot or "Crew member"
        for row in rows.values()
        if row.person_id is not None and row.status == AssignmentStatus.ASSIGNED.value
    }


def _private_change(change: PublicationChange) -> bool:
    label = change.label.casefold()
    return change.sensitive or any(term in label for term in PRIVATE_CHANGE_TERMS)


def _slot_impact_summary(
    change: PublicationChange,
    *,
    person_id: uuid.UUID,
    old: Assignment | None,
    new: Assignment | None,
) -> str:
    old_person_id = old.person_id if old else None
    new_person_id = new.person_id if new else None
    old_role = old.display_name_snapshot if old else "Position"
    new_role = new.display_name_snapshot if new else old_role
    if old_person_id != new_person_id:
        if person_id == old_person_id:
            return f"Removed from {old_role}."
        if person_id == new_person_id:
            return f"Assigned to {new_role}."
    if _private_change(change):
        return f"Private roster details for {new_role} were updated."
    return change.summary


def _publication_impacts(
    db: Session, revision: WorkdayRevision
) -> tuple[EmployeeImpact, ...]:
    previous = _previous_publication(db, revision)
    old_rows = _assignment_map(db, previous)
    new_rows = _assignment_map(db, revision)
    old_people = _assigned_people(old_rows)
    new_people = _assigned_people(new_rows)
    impacts: dict[tuple[uuid.UUID, str], EmployeeImpact] = {}
    for change in publication_diff(db, previous, revision):
        if change.category == "equipment":
            continue
        if change.slot_key is None:
            if change.category == "publication":
                continue
            summary = (
                "Private Workday details were updated."
                if _private_change(change)
                else change.summary
            )
            for person_id, person_name in {**old_people, **new_people}.items():
                impacts[(person_id, summary)] = EmployeeImpact(
                    person_id, person_name, summary
                )
            continue
        old = old_rows.get(change.slot_key)
        new = new_rows.get(change.slot_key)
        person_ids = {
            person_id
            for person_id in (
                old.person_id if old else None,
                new.person_id if new else None,
            )
            if person_id is not None
        }
        for person_id in person_ids:
            person_name = (
                (new.person_name_snapshot if new and new.person_id == person_id else None)
                or (old.person_name_snapshot if old and old.person_id == person_id else None)
                or "Crew member"
            )
            summary = _slot_impact_summary(
                change, person_id=person_id, old=old, new=new
            )
            impacts[(person_id, summary)] = EmployeeImpact(
                person_id, person_name, summary
            )
    return tuple(impacts.values())


def _whole_workday_impacts(
    db: Session,
    revision: WorkdayRevision | None,
    summary: str,
) -> tuple[EmployeeImpact, ...]:
    people = _assigned_people(_assignment_map(db, revision))
    return tuple(
        EmployeeImpact(person_id, person_name, summary)
        for person_id, person_name in people.items()
    )


def roster_impacts(
    db: Session, event: AuditEvent, workday: Workday | None
) -> tuple[tuple[EmployeeImpact, ...], WorkdayRevision | None]:
    if workday is None or event.action not in ROSTER_AUDIT_ACTIONS:
        return (), None
    revision = _revision_for_event(db, event, workday)
    if event.action == "workday.published" and revision:
        return _publication_impacts(db, revision), revision
    if event.action == "assignment.declined":
        person_id = event.detail.get("person_id")
        if person_id:
            try:
                parsed_person_id = uuid.UUID(str(person_id))
            except (TypeError, ValueError):
                parsed_person_id = None
            if parsed_person_id:
                person = db.get(Person, parsed_person_id)
                summary = str(event.detail.get("summary") or "Assignment was declined.")
                person_name = str(
                    event.detail.get("person_name_snapshot")
                    or (person.display_name if person else "Crew member")
                )
                return (
                    EmployeeImpact(
                        parsed_person_id,
                        person_name,
                        summary,
                    ),
                ), revision
    if event.action == "workday.rescheduled":
        source = str(event.detail.get("from") or "the original date")
        destination = str(event.detail.get("to") or "a replacement date")
        return _whole_workday_impacts(
            db, revision, f"Workday was rescheduled from {source} to {destination}."
        ), revision
    if event.action == "workday.status_changed":
        before = str(event.detail.get("from") or "the previous status").replace("_", " ").title()
        after = str(event.detail.get("to") or "the new status").replace("_", " ").title()
        return _whole_workday_impacts(
            db, revision, f"Workday status changed from {before} to {after}."
        ), revision
    return (), revision


def permitted_audit_records(
    db: Session,
    *,
    actor: Actor,
    events: list[AuditEvent],
) -> list[AuditRecord]:
    managed_regions = managed_region_ids(actor)
    if managed_regions == set():
        return []
    managed_people = (
        None
        if managed_regions is None
        else set(
            db.scalars(
                select(Person.id).where(Person.home_region_id.in_(managed_regions))
            )
        )
    )
    records: list[AuditRecord] = []
    seen_event_ids: set[uuid.UUID] = set()
    for event in events:
        if event.id in seen_event_ids:
            continue
        seen_event_ids.add(event.id)
        roster_event = event.action in ROSTER_AUDIT_ACTIONS
        workday = _event_workday(db, event) if roster_event else None
        scope_region_id = workday.region_id if workday is not None else event.region_id
        region_visible = actor.is_admin or (
            scope_region_id is not None
            and managed_regions is not None
            and scope_region_id in managed_regions
        )
        if not region_visible and not roster_event:
            continue
        impacts, revision = roster_impacts(db, event, workday)
        if region_visible:
            records.append(
                AuditRecord(
                    event=event,
                    impacts=impacts,
                    workday=workday,
                    revision=revision,
                    region_id=scope_region_id,
                    cross_region_employee_oversight=False,
                    visible_detail=event.detail,
                )
            )
            continue
        visible_impacts = tuple(
            impact
            for impact in impacts
            if impact.person_id in (managed_people or set())
        )
        if visible_impacts:
            records.append(
                AuditRecord(
                    event=event,
                    impacts=visible_impacts,
                    workday=workday,
                    revision=revision,
                    region_id=scope_region_id,
                    cross_region_employee_oversight=True,
                    visible_detail={},
                )
            )
    return records
