from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import time

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.enums import AssignmentStatus
from app.rostering.models import Assignment, Workday, WorkdayRevision
from app.rostering.travel import TRANSPORT_VEHICLE


@dataclass(frozen=True)
class RosterConflict:
    kind: str
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


def _minutes(value: time) -> int:
    return value.hour * 60 + value.minute


def _overlap(
    left_start: time | None,
    left_end: time | None,
    right_start: time | None,
    right_end: time | None,
) -> tuple[bool, str]:
    if None in (left_start, left_end, right_start, right_end):
        known = "–".join(
            value.strftime("%H:%M") if value else "TBC" for value in (right_start, right_end)
        )
        return True, f"{known}; potential same-day conflict"
    left_a, left_b = _minutes(left_start), _minutes(left_end)
    right_a, right_b = _minutes(right_start), _minutes(right_end)
    if left_b <= left_a:
        left_b += 24 * 60
    if right_b <= right_a:
        right_b += 24 * 60
    overlaps = left_a < right_b and right_a < left_b
    known = f"{right_start.strftime('%H:%M')}–{right_end.strftime('%H:%M')}"
    return overlaps, f"{known}; overlapping time" if overlaps else known


def publication_conflicts(
    db: Session,
    workday: Workday,
    draft: WorkdayRevision,
    visible_region_ids: set[uuid.UUID] | None = None,
) -> list[RosterConflict]:
    """Compare a draft with authoritative published and active-draft rosters on its date."""
    current_rows = list(
        db.scalars(
            select(Assignment).where(
                Assignment.revision_id == draft.id,
                Assignment.status == AssignmentStatus.ASSIGNED.value,
            )
        )
    )
    if not current_rows:
        return []
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
        .where(
            Workday.id != workday.id,
            WorkdayRevision.work_date == draft.work_date,
            Assignment.status == AssignmentStatus.ASSIGNED.value,
        )
    ).all()
    conflicts: list[RosterConflict] = []
    seen: set[tuple[str, uuid.UUID | None, uuid.UUID]] = set()
    for current in current_rows:
        for other_workday, other_revision, other in candidates:
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
            overlap, timing = _overlap(
                current.start_time or draft.start_time,
                current.end_time or draft.end_time,
                other.start_time or other_revision.start_time,
                other.end_time or other_revision.end_time,
            )
            for kind, identity, subject in matches:
                key = (kind, identity, other_workday.id)
                if overlap and key not in seen:
                    seen.add(key)
                    conflicts.append(
                        RosterConflict(
                            kind=kind,
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
