from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from sqlalchemy import case, or_, select
from sqlalchemy.orm import Session, joinedload

from app.core.enums import AssignmentStatus, WorkdayStatus
from app.rostering.models import Assignment, Workday, WorkdayRevision
from app.rostering.travel import TRANSPORT_VEHICLE, effective_person_interval

# All persisted minute offsets are validated to 0..1440. A hotel/truck call can
# therefore start at most one civil day early. A final event may roll to the next
# day, followed by up to one day of pack-up and one day of return travel.
_MAX_START_DAYS_BEFORE_WORKDAY = 1
_MAX_FINISH_DAYS_AFTER_WORKDAY = 3


@dataclass(frozen=True)
class RosterConflict:
    kind: str
    subject_id: uuid.UUID
    subject: str
    other_workday_id: uuid.UUID
    other_title: str
    other_location: str
    timing: str
    detail_visible: bool = True

    @property
    def message(self) -> str:
        if not self.detail_visible:
            return f"{self.subject} is already rostered on another Workday ({self.timing})."
        return (
            f"{self.subject} also appears on {self.other_title} at "
            f"{self.other_location} ({self.timing})."
        )


def _overlap(
    left_start: datetime | None,
    left_end: datetime | None,
    right_start: datetime | None,
    right_end: datetime | None,
    *,
    left_date: date,
    right_date: date,
) -> tuple[bool, str]:
    if None in (left_start, left_end, right_start, right_end):
        known = "–".join(
            value.strftime("%H:%M") if value else "TBC" for value in (right_start, right_end)
        )
        return (
            left_date == right_date,
            f"{right_date.isoformat()} {known}; potential same-day conflict",
        )
    overlaps = left_start < right_end and right_start < left_end
    if right_start.date() == right_end.date():
        known = f"{right_start:%Y-%m-%d %H:%M}–{right_end:%H:%M}"
    else:
        known = f"{right_start:%Y-%m-%d %H:%M}–{right_end:%Y-%m-%d %H:%M}"
    return overlaps, f"{known}; overlapping time" if overlaps else known


def _operation_related(left: Workday, right: Workday) -> bool:
    return left.generated_from_workday_id == right.id or right.generated_from_workday_id == left.id


def publication_conflicts(
    db: Session,
    workday: Workday,
    draft: WorkdayRevision,
    visible_region_ids: set[uuid.UUID] | None = None,
    *,
    include_inactive_current: bool = False,
) -> list[RosterConflict]:
    """Compare a draft with current rosters whose effective intervals may intersect."""
    if not include_inactive_current and workday.status != WorkdayStatus.SCHEDULED.value:
        return []
    current_rows = list(
        db.scalars(
            select(Assignment)
            .where(
                Assignment.revision_id == draft.id,
                Assignment.status == AssignmentStatus.ASSIGNED.value,
            )
            .options(joinedload(Assignment.vehicle))
            .order_by(Assignment.id)
        )
    )
    if not current_rows:
        return []
    current_intervals = [effective_person_interval(draft, row) for row in current_rows]
    known_points = [
        point
        for interval in current_intervals
        for point in (interval.start, interval.finish)
        if point is not None
    ]
    earliest = min((point.date() for point in known_points), default=draft.work_date)
    latest = max((point.date() for point in known_points), default=draft.work_date)
    candidate_start = earliest - timedelta(days=_MAX_FINISH_DAYS_AFTER_WORKDAY)
    candidate_end = latest + timedelta(days=_MAX_START_DAYS_BEFORE_WORKDAY)
    candidates = db.execute(
        select(Workday, WorkdayRevision, Assignment)
        .join(
            WorkdayRevision,
            or_(
                Workday.current_published_revision_id == WorkdayRevision.id,
                Workday.current_draft_revision_id == WorkdayRevision.id,
            ),
        )
        .join(Assignment, Assignment.revision_id == WorkdayRevision.id)
        .options(joinedload(Assignment.vehicle))
        .where(
            Workday.id != workday.id,
            Workday.status == WorkdayStatus.SCHEDULED.value,
            WorkdayRevision.work_date.between(candidate_start, candidate_end),
            Assignment.status == AssignmentStatus.ASSIGNED.value,
        )
        .order_by(
            WorkdayRevision.work_date,
            Workday.id,
            case((Workday.current_published_revision_id == WorkdayRevision.id, 0), else_=1),
            Assignment.id,
        )
    ).all()
    conflicts: list[RosterConflict] = []
    seen: set[tuple[str, uuid.UUID, uuid.UUID]] = set()
    for current, current_interval in zip(current_rows, current_intervals, strict=True):
        for other_workday, other_revision, other in candidates:
            if _operation_related(workday, other_workday):
                continue
            matches: list[tuple[str, uuid.UUID, str]] = []
            if current.person_id and current.person_id == other.person_id:
                matches.append(
                    ("PERSON", current.person_id, current.person_name_snapshot or "Crew member")
                )
            if (
                current.transport_mode == TRANSPORT_VEHICLE
                and other.transport_mode == TRANSPORT_VEHICLE
                and current.vehicle_id
                and current.vehicle_id == other.vehicle_id
            ):
                matches.append(
                    ("VEHICLE", current.vehicle_id, current.vehicle_name_snapshot or "Vehicle")
                )
            if not matches:
                continue
            other_interval = effective_person_interval(other_revision, other)
            overlap, timing = _overlap(
                current_interval.start,
                current_interval.finish,
                other_interval.start,
                other_interval.finish,
                left_date=draft.work_date,
                right_date=other_revision.work_date,
            )
            for kind, identity, subject in matches:
                key = (kind, identity, other_workday.id)
                if overlap and key not in seen:
                    seen.add(key)
                    conflicts.append(
                        RosterConflict(
                            kind=kind,
                            subject_id=identity,
                            subject=subject,
                            other_workday_id=other_workday.id,
                            other_title=other_revision.title,
                            other_location=other_revision.track_name_snapshot,
                            timing=timing,
                            detail_visible=(
                                visible_region_ids is None
                                or other_workday.region_id in visible_region_ids
                            ),
                        )
                    )
    return conflicts


def operation_publication_conflicts(
    db: Session,
    workday: Workday,
    draft: WorkdayRevision,
    visible_region_ids: set[uuid.UUID] | None = None,
) -> list[RosterConflict]:
    """Return conflicts for a parent draft and its generated Travel draft."""
    conflicts = publication_conflicts(
        db, workday, draft, visible_region_ids=visible_region_ids
    )
    generated = (
        db.scalar(select(Workday).where(Workday.generated_from_workday_id == workday.id))
        if draft.standard_travel_enabled
        else None
    )
    generated_draft = (
        db.get(WorkdayRevision, generated.current_draft_revision_id)
        if generated and generated.current_draft_revision_id
        else None
    )
    if generated and generated_draft:
        conflicts.extend(
            publication_conflicts(
                db,
                generated,
                generated_draft,
                visible_region_ids=visible_region_ids,
                include_inactive_current=True,
            )
        )
    unique: dict[tuple[str, uuid.UUID, uuid.UUID], RosterConflict] = {}
    for conflict in conflicts:
        unique.setdefault(
            (conflict.kind, conflict.subject_id, conflict.other_workday_id), conflict
        )
    return list(unique.values())
