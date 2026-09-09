from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.policy import Actor, can_crew_view
from app.catalog.models import Region
from app.core.enums import Role
from app.core.holidays import holiday_for_date
from app.rostering.models import Assignment, Workday, WorkdayRevision
from app.rostering.participation import person_day_participation


def month_items(db: Session, actor: Actor, start: date, end: date) -> list[dict[str, object]]:
    broad_roles = {Role.SUB_MANAGER.value, Role.MANAGER.value, Role.VIEWER.value}
    broad_month = actor.is_admin or any(
        bool(set(roles) & broad_roles) for roles in actor.regional_roles.values()
    )
    statement = (
        select(Workday, WorkdayRevision, Region)
        .join(WorkdayRevision, Workday.current_published_revision_id == WorkdayRevision.id)
        .join(Region, Region.id == Workday.region_id)
        .where(WorkdayRevision.work_date >= start, WorkdayRevision.work_date < end)
        .order_by(WorkdayRevision.work_date)
    )
    if not broad_month:
        if actor.person_id is None:
            return []
        statement = (
            statement.join(Assignment, Assignment.revision_id == WorkdayRevision.id)
            .where(Assignment.person_id == actor.person_id)
            .distinct()
        )
    rows = db.execute(statement).all()
    own_by_revision: dict[uuid.UUID, list[Assignment]] = {}
    if actor.person_id and rows:
        revision_ids = [revision.id for _workday, revision, _region in rows]
        for assignment in db.scalars(
                select(Assignment).where(
                    Assignment.revision_id.in_(revision_ids), Assignment.person_id == actor.person_id
                )
            ):
            own_by_revision.setdefault(assignment.revision_id, []).append(assignment)
    result: list[dict[str, object]] = []
    for workday, revision, region in rows:
        own = own_by_revision.get(revision.id, [])
        if not own and not broad_month:
            continue
        if not own and not can_crew_view(actor, workday.region_id):
            continue
        participation = person_day_participation(revision, own) if own else None
        result.append(
            {
                "id": str(workday.id),
                "date": revision.work_date,
                "category": workday.category,
                "title": revision.title,
                "track": revision.track_name_snapshot,
                "colour": revision.track_colour_snapshot,
                "start": participation.start if participation else revision.start_time,
                "end": participation.end if participation else revision.end_time,
                "minutes": participation.minutes if participation else 0,
                "role": participation.role_summary if participation else "Crew view",
                "status": " / ".join(participation.statuses) if participation else "PUBLISHED",
                "cross_region": bool(
                    own and actor.person_id and workday.region_id not in actor.regional_roles
                ),
                "holiday": holiday_for_date(revision.work_date, region.statutory_holiday_region or ""),
                "own": bool(own),
                "published_at": revision.published_at,
            }
        )
    return result


def day_assignments(
    db: Session, actor: Actor, revision: WorkdayRevision, management: bool
) -> list[dict[str, object]]:
    rows = list(
        db.scalars(
            select(Assignment)
            .where(Assignment.revision_id == revision.id)
            .order_by(Assignment.display_name_snapshot)
        )
    )
    result = []
    for row in rows:
        is_own = actor.person_id == row.person_id
        note = row.note if (not row.note_private or is_own or management) else ""
        result.append(
            {
                "slot_key": str(row.slot_key),
                "person_id": str(row.person_id) if row.person_id else None,
                "role": row.display_name_snapshot,
                "person": row.person_name_snapshot or row.status.replace("_", " ").title(),
                "status": row.status,
                "start": row.start_time,
                "end": row.end_time,
                "note": note,
                "is_own": is_own,
                "can_decline": bool(is_own and row.status == "ASSIGNED"),
            }
        )
    return result
