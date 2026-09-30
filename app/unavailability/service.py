from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.audit.service import record_audit
from app.core.enums import WorkdayStatus
from app.core.time import utcnow
from app.identity.models import Person
from app.rostering.models import Assignment, Workday, WorkdayRevision
from app.unavailability.models import PersonUnavailability


@dataclass(frozen=True)
class RosterConflict:
    workday_id: uuid.UUID
    work_date: date
    track_name: str
    positions: tuple[str, ...]


def unavailability_label(start_date: date, end_date: date) -> str:
    if start_date.year != end_date.year:
        dates = f"{start_date.day} {start_date:%b %Y}–{end_date.day} {end_date:%b %Y}"
    elif start_date.month != end_date.month:
        dates = f"{start_date.day} {start_date:%b}–{end_date.day} {end_date:%b}"
    else:
        dates = f"{start_date.day}–{end_date.day} {end_date:%b}"
    return f"On leave · {dates}"


def active_for_people_on_date(
    db: Session, person_ids: set[uuid.UUID], work_date: date
) -> dict[uuid.UUID, PersonUnavailability]:
    if not person_ids:
        return {}
    rows = db.scalars(
        select(PersonUnavailability)
        .where(
            PersonUnavailability.person_id.in_(person_ids),
            PersonUnavailability.cancelled_at.is_(None),
            PersonUnavailability.start_date <= work_date,
            PersonUnavailability.end_date >= work_date,
        )
        .order_by(PersonUnavailability.start_date, PersonUnavailability.id)
    )
    return {row.person_id: row for row in rows}


def active_overlapping(
    db: Session, *, person_id: uuid.UUID, start_date: date, end_date: date
) -> list[PersonUnavailability]:
    return list(
        db.scalars(
            select(PersonUnavailability)
            .where(
                PersonUnavailability.person_id == person_id,
                PersonUnavailability.cancelled_at.is_(None),
                PersonUnavailability.start_date <= end_date,
                PersonUnavailability.end_date >= start_date,
            )
            .order_by(PersonUnavailability.start_date)
        )
    )


def create_unavailability(
    db: Session,
    *,
    person: Person,
    start_date: date,
    end_date: date,
    note: str,
    actor_user_id: uuid.UUID,
) -> PersonUnavailability:
    if end_date < start_date:
        raise ValueError("End date must be on or after the start date.")
    clean_note = note.strip()
    if len(clean_note) > 1000:
        raise ValueError("Note must be 1000 characters or fewer.")
    locked_person = db.scalar(
        select(Person).where(Person.id == person.id).with_for_update()
    )
    if locked_person is None:
        raise ValueError("Select an active person.")
    if active_overlapping(
        db, person_id=locked_person.id, start_date=start_date, end_date=end_date
    ):
        raise ValueError("This person already has active leave overlapping those dates.")
    row = PersonUnavailability(
        person_id=locked_person.id,
        start_date=start_date,
        end_date=end_date,
        note=clean_note,
        created_by_user_id=actor_user_id,
    )
    db.add(row)
    db.flush()
    record_audit(
        db,
        "person_unavailability.created",
        "person_unavailability",
        row.id,
        actor_user_id,
        region_id=locked_person.home_region_id,
        detail={
            "person_id": str(locked_person.id),
            "person_name": locked_person.display_name,
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
        },
    )
    db.commit()
    return row


def cancel_unavailability(
    db: Session,
    *,
    row: PersonUnavailability,
    person: Person,
    actor_user_id: uuid.UUID,
) -> None:
    if row.cancelled_at is not None:
        raise ValueError("This leave record is already cancelled.")
    row.cancelled_at = utcnow()
    row.cancelled_by_user_id = actor_user_id
    record_audit(
        db,
        "person_unavailability.cancelled",
        "person_unavailability",
        row.id,
        actor_user_id,
        region_id=person.home_region_id,
        detail={
            "person_id": str(person.id),
            "person_name": person.display_name,
            "start_date": row.start_date.isoformat(),
            "end_date": row.end_date.isoformat(),
        },
    )
    db.commit()


def roster_conflicts(
    db: Session, *, person_id: uuid.UUID, start_date: date, end_date: date
) -> list[RosterConflict]:
    rows = db.execute(
        select(Workday, WorkdayRevision, Assignment)
        .join(
            WorkdayRevision,
            or_(
                Workday.current_published_revision_id == WorkdayRevision.id,
                and_(
                    Workday.current_published_revision_id.is_(None),
                    Workday.current_draft_revision_id == WorkdayRevision.id,
                ),
            ),
        )
        .join(Assignment, Assignment.revision_id == WorkdayRevision.id)
        .where(
            Workday.status == WorkdayStatus.SCHEDULED.value,
            WorkdayRevision.work_date >= start_date,
            WorkdayRevision.work_date <= end_date,
            Assignment.person_id == person_id,
        )
        .order_by(WorkdayRevision.work_date, Workday.id, Assignment.display_name_snapshot)
    ).all()
    grouped: dict[uuid.UUID, tuple[WorkdayRevision, set[str]]] = {}
    for workday, revision, assignment in rows:
        grouped.setdefault(workday.id, (revision, set()))[1].add(
            assignment.display_name_snapshot
        )
    return [
        RosterConflict(
            workday_id=workday_id,
            work_date=revision.work_date,
            track_name=revision.track_name_snapshot,
            positions=tuple(sorted(positions)),
        )
        for workday_id, (revision, positions) in grouped.items()
    ]
