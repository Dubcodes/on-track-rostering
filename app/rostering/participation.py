from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta

from app.rostering.models import Assignment, WorkdayRevision


@dataclass(frozen=True)
class PersonDayParticipation:
    start: time | None
    end: time | None
    minutes: int
    role_summary: str
    statuses: tuple[str, ...]


def _span(revision: WorkdayRevision, assignment: Assignment) -> tuple[datetime, datetime] | None:
    start = assignment.start_time or revision.start_time
    end = assignment.end_time or revision.end_time
    if start is None or end is None:
        return None
    start_at = datetime.combine(revision.work_date, start)
    end_at = datetime.combine(revision.work_date, end)
    # On an overnight workday, explicit times earlier than the workday start
    # belong to the following calendar day. This keeps multiple after-midnight
    # assignments on the same operational workday instead of creating a 24h+ span.
    if (
        revision.start_time is not None
        and revision.end_time is not None
        and revision.end_time < revision.start_time
    ):
        if start < revision.start_time:
            start_at += timedelta(days=1)
        if end < revision.start_time:
            end_at += timedelta(days=1)
    if end_at < start_at:
        end_at += timedelta(days=1)
    return start_at, end_at


def person_day_participation(
    revision: WorkdayRevision, assignments: list[Assignment]
) -> PersonDayParticipation:
    ordered = sorted(
        assignments,
        key=lambda row: (
            row.display_name_snapshot.casefold(),
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
            (row.start_time for row in ordered if row.start_time is not None), revision.start_time
        )
        end = next((row.end_time for row in ordered if row.end_time is not None), revision.end_time)
        minutes = 0
    return PersonDayParticipation(
        start=start,
        end=end,
        minutes=minutes,
        role_summary=" + ".join(roles) if roles else "Crew",
        statuses=tuple(dict.fromkeys(row.status for row in ordered)),
    )
