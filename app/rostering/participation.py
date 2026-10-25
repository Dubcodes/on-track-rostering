from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.enums import AssignmentStatus, WorkdayStatus
from app.positions.ordering import position_order
from app.rostering.models import Assignment, PersonalWorkdayEntry, Workday, WorkdayRevision
from app.rostering.travel import effective_person_interval, effective_person_travel


@dataclass(frozen=True)
class PersonDayParticipation:
    start: time | None
    end: time | None
    minutes: int
    role_summary: str
    statuses: tuple[str, ...]


def active_published_assignments(
    db: Session,
    workday: Workday,
    revision: WorkdayRevision,
    assignments: list[Assignment],
) -> list[Assignment]:
    """Return assignments that still represent active published participation."""
    if workday.current_published_revision_id != revision.id:
        return []
    if workday.generated_from_workday_id is None:
        return assignments
    if workday.status != WorkdayStatus.SCHEDULED.value:
        return []
    parent = db.get(Workday, workday.generated_from_workday_id)
    if (
        parent is None
        or parent.status != WorkdayStatus.SCHEDULED.value
        or parent.current_published_revision_id is None
    ):
        return []
    candidate_ids = {
        row.person_id
        for row in assignments
        if row.person_id is not None and row.status == AssignmentStatus.ASSIGNED.value
    }
    if not candidate_ids:
        return []
    parent_participants = set(
        db.scalars(
            select(Assignment.person_id).where(
                Assignment.revision_id == parent.current_published_revision_id,
                Assignment.person_id.in_(candidate_ids),
                Assignment.status == AssignmentStatus.ASSIGNED.value,
                Assignment.uses_standard_travel.is_(True),
            )
        )
    )
    opted_out = set(
        db.scalars(
            select(PersonalWorkdayEntry.person_id).where(
                PersonalWorkdayEntry.workday_id == parent.id,
                PersonalWorkdayEntry.person_id.in_(candidate_ids),
                PersonalWorkdayEntry.standard_travel_opt_out.is_(True),
            )
        )
    )
    active_ids = parent_participants - opted_out
    return [row for row in assignments if row.person_id in active_ids]


def _span(revision: WorkdayRevision, assignment: Assignment) -> tuple[datetime, datetime] | None:
    effective = effective_person_interval(revision, assignment)
    if effective.start is None or effective.finish is None:
        return None
    return effective.start, effective.finish


def person_day_participation(
    revision: WorkdayRevision, assignments: list[Assignment]
) -> PersonDayParticipation:
    ordered = sorted(
        assignments,
        key=lambda row: (
            position_order(row.display_name_snapshot),
            row.slot_index if row.slot_index is not None else -1,
            str(row.slot_key),
        ),
    )
    roles = list(dict.fromkeys(row.display_name_snapshot for row in ordered))
    spans = [span for row in ordered if (span := _span(revision, row)) is not None]
    if spans:
        start_at = min(span[0] for span in spans)
        end_at = max(span[1] for span in spans)
        minutes = max(0, int((end_at - start_at).total_seconds() // 60))
        start, end = start_at.time(), end_at.time()
    else:
        start = next(
            (effective_person_travel(revision, row).start for row in ordered), revision.start_time
        )
        end = next((effective_person_travel(revision, row).finish for row in ordered), revision.end_time)
        minutes = 0
    return PersonDayParticipation(
        start=start,
        end=end,
        minutes=minutes,
        role_summary=" + ".join(roles) if roles else "Crew",
        statuses=tuple(dict.fromkeys(row.status for row in ordered)),
    )
