from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.policy import Actor, can_crew_view, can_manage_region, can_view_management_detail
from app.catalog.models import Region, Track
from app.catalog.presentation import track_token
from app.core.enums import Role, WorkdayStatus
from app.core.holidays import holiday_for_date
from app.identity.models import Person
from app.positions.ordering import position_order
from app.rostering.models import Assignment, Workday, WorkdayRevision
from app.rostering.participation import active_published_assignments, person_day_participation
from app.rostering.travel import effective_person_travel, transport_display


def adjacent_published_workdays(
    db: Session,
    actor: Actor,
    current_date: date,
) -> tuple[uuid.UUID | None, uuid.UUID | None]:
    """Return deterministic adjacent roster dates for the actor, never raw UUID order."""
    personal: list[tuple[uuid.UUID, date]] = []
    if actor.person_id:
        personal_rows = list(
            db.execute(
                select(Workday, WorkdayRevision, Assignment)
                .join(WorkdayRevision, Workday.current_published_revision_id == WorkdayRevision.id)
                .join(Assignment, Assignment.revision_id == WorkdayRevision.id)
                .where(
                    Assignment.person_id == actor.person_id,
                    Assignment.status == "ASSIGNED",
                )
                .distinct()
                .order_by(WorkdayRevision.work_date, Workday.id)
            ).all()
        )
        personal = list(dict.fromkeys(
            (workday.id, revision.work_date)
            for workday, revision, assignment in personal_rows
            if active_published_assignments(db, workday, revision, [assignment])
        ))
    candidates = personal
    if not candidates:
        management_rows = db.execute(
                select(Workday, WorkdayRevision)
                .join(WorkdayRevision, Workday.current_published_revision_id == WorkdayRevision.id)
                .order_by(WorkdayRevision.work_date, Workday.id)
            ).all()
        candidates = []
        for workday, revision in management_rows:
            if not can_view_management_detail(actor, workday.region_id):
                continue
            if workday.generated_from_workday_id:
                assignments = list(
                    db.scalars(select(Assignment).where(Assignment.revision_id == revision.id))
                )
                if not active_published_assignments(db, workday, revision, assignments):
                    continue
            candidates.append((workday.id, revision.work_date))
    previous = [row for row in candidates if row[1] < current_date]
    following = [row for row in candidates if row[1] > current_date]
    previous_id = None
    if previous:
        previous_date = previous[-1][1]
        previous_id = next(row[0] for row in previous if row[1] == previous_date)
    return previous_id, following[0][0] if following else None


def month_items(db: Session, actor: Actor, start: date, end: date) -> list[dict[str, object]]:
    broad_roles = {Role.SUB_MANAGER.value, Role.MANAGER.value, Role.VIEWER.value}
    broad_month = not actor.is_contractor and (actor.is_admin or any(
        bool(set(roles) & broad_roles) for roles in actor.regional_roles.values()
    ))
    statement = (
        select(Workday, WorkdayRevision, Region, Track.palette_slot)
        .join(WorkdayRevision, Workday.current_published_revision_id == WorkdayRevision.id)
        .join(Region, Region.id == Workday.region_id)
        .outerjoin(Track, Track.id == WorkdayRevision.track_id)
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
    all_by_revision: dict[uuid.UUID, list[Assignment]] = {}
    if actor.person_id and rows:
        revision_ids = [revision.id for _workday, revision, _region, _slot in rows]
        for assignment in db.scalars(
            select(Assignment).where(
                Assignment.revision_id.in_(revision_ids), Assignment.person_id == actor.person_id
            )
        ):
            own_by_revision.setdefault(assignment.revision_id, []).append(assignment)
    if broad_month and rows:
        revision_ids = [revision.id for _workday, revision, _region, _slot in rows]
        for assignment in db.scalars(
            select(Assignment).where(Assignment.revision_id.in_(revision_ids))
        ):
            all_by_revision.setdefault(assignment.revision_id, []).append(assignment)
    home_region_id = (
        db.scalar(select(Person.home_region_id).where(Person.id == actor.person_id))
        if actor.person_id
        else None
    )
    result: list[dict[str, object]] = []
    draft_ids = {
        workday.id
        for workday, _revision, _region, _slot in rows
        if workday.current_draft_revision_id and can_manage_region(actor, workday.region_id)
    }
    for workday, revision, region, palette_slot in rows:
        own = own_by_revision.get(revision.id, [])
        if workday.generated_from_workday_id:
            own = active_published_assignments(db, workday, revision, own)
        if not own and not broad_month:
            continue
        if not own and not can_crew_view(actor, workday.region_id):
            continue
        participation = person_day_participation(revision, own) if own else None
        visible_rows = all_by_revision.get(revision.id, [])
        if workday.generated_from_workday_id:
            visible_rows = active_published_assignments(db, workday, revision, visible_rows)
        visible_statuses = (
            set(participation.statuses) if participation else {row.status for row in visible_rows}
        )
        display_statuses = ["UNASSIGNED" if value == "TBC" else value for value in (
            participation.statuses if participation else ("PUBLISHED",)
        )]
        result.append(
            {
                "id": str(workday.id),
                "region_id": workday.region_id,
                "date": revision.work_date,
                "category": workday.category,
                "title": revision.title,
                "track": revision.track_name_snapshot,
                "presentation": track_token(palette_slot, workday.category),
                "start": participation.start if participation else revision.start_time,
                "end": participation.end if participation else revision.end_time,
                "minutes": (
                    participation.minutes
                    if participation and workday.status == WorkdayStatus.SCHEDULED.value
                    else 0
                ),
                "role": participation.role_summary if participation else "Crew view",
                "status": (
                    workday.status
                    if workday.status != WorkdayStatus.SCHEDULED.value
                    else " / ".join(display_statuses)
                ),
                "has_open": "OPEN" in visible_statuses,
                "cross_region": bool(
                    own and home_region_id is not None and workday.region_id != home_region_id
                ),
                "holiday": holiday_for_date(revision.work_date, region.statutory_holiday_region or ""),
                "own": bool(own),
                "published_at": revision.published_at,
                "has_draft": workday.id in draft_ids,
                "draft_only": False,
                "url": f"/day/{workday.id}",
                "workday_status": workday.status,
            }
        )
    draft_rows = db.execute(
        select(Workday, WorkdayRevision, Region, Track.palette_slot)
        .join(WorkdayRevision, Workday.current_draft_revision_id == WorkdayRevision.id)
        .join(Region, Region.id == Workday.region_id)
        .outerjoin(Track, Track.id == WorkdayRevision.track_id)
        .where(
            Workday.current_published_revision_id.is_(None),
            WorkdayRevision.work_date >= start,
            WorkdayRevision.work_date < end,
        )
        .order_by(WorkdayRevision.work_date)
    ).all()
    for workday, revision, region, palette_slot in draft_rows:
        if not can_manage_region(actor, workday.region_id):
            continue
        result.append(
            {
                "id": str(workday.id),
                "region_id": workday.region_id,
                "date": revision.work_date,
                "category": workday.category,
                "title": revision.title,
                "track": revision.track_name_snapshot,
                "presentation": track_token(palette_slot, workday.category),
                "start": revision.start_time,
                "end": revision.end_time,
                "minutes": 0,
                "role": "Private management draft",
                "status": "DRAFT",
                "has_open": False,
                "cross_region": False,
                "holiday": holiday_for_date(revision.work_date, region.statutory_holiday_region or ""),
                "own": False,
                "published_at": None,
                "has_draft": True,
                "draft_only": True,
                "url": f"/manage/workdays/{workday.id}",
                "workday_status": workday.status,
            }
        )
    result.sort(key=lambda item: (item["date"], str(item["id"])))
    return result


def day_assignments(
    db: Session,
    actor: Actor,
    revision: WorkdayRevision,
    *,
    can_view_all_rows: bool,
    can_view_private_notes: bool,
    can_self_decline: bool = False,
    workday: Workday | None = None,
) -> list[dict[str, object]]:
    statement = select(Assignment).where(Assignment.revision_id == revision.id)
    if not can_view_all_rows:
        if actor.person_id is None:
            return []
        statement = statement.where(Assignment.person_id == actor.person_id)
    rows = list(db.scalars(statement.order_by(Assignment.display_name_snapshot)))
    if workday and workday.generated_from_workday_id:
        rows = active_published_assignments(db, workday, revision, rows)
    rows.sort(key=lambda row: (position_order(row.display_name_snapshot), str(row.slot_key)))
    result = []
    empty_labels = {
        "OPEN": "Open position",
        "TBC": "Unassigned",
        "MANAGER_ACTION_REQUIRED": "Needs Manager action",
    }
    for row in rows:
        is_own = actor.person_id == row.person_id
        note = row.note if (not row.note_private or is_own or can_view_private_notes) else ""
        travel = effective_person_travel(revision, row)
        result.append(
            {
                "slot_key": str(row.slot_key),
                "person_id": str(row.person_id) if row.person_id else None,
                "role": row.display_name_snapshot,
                "person": row.person_name_snapshot or empty_labels.get(row.status, "Unassigned"),
                "status": row.status,
                "start": travel.start,
                "end": travel.finish,
                "note": note,
                "is_own": is_own,
                "can_decline": bool(is_own and row.status == "ASSIGNED" and can_self_decline),
                "transport": transport_display(
                    row.transport_mode, row.vehicle_name_snapshot, row.custom_transport_text
                ),
                "vehicle": row.vehicle_name_snapshot,
                "accommodation": travel.accommodation,
            }
        )
    return result
