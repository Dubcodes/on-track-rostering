from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, time, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.audit.models import AuditEvent
from app.audit.read import (
    ROSTER_AUDIT_ACTIONS,
    managed_region_ids,
    permitted_audit_records,
)
from app.catalog.models import Region
from app.core.config import get_settings
from app.core.database import get_db
from app.core.time import local_today
from app.identity.models import User
from app.web import context, templates

router = APIRouter(prefix="/manage/audit")


@router.get("", response_class=HTMLResponse)
def audit_page(
    request: Request,
    region_id: uuid.UUID | None = None,
    person_id: uuid.UUID | None = None,
    actor_id: uuid.UUID | None = None,
    action: str = "",
    search: str = "",
    date_from: date | None = Query(None),
    date_to: date | None = Query(None),
    db: Session = Depends(get_db),
):
    actor = request.state.actor
    regions = list(db.scalars(select(Region).order_by(Region.name)))
    managed_ids = managed_region_ids(actor)
    if managed_ids == set():
        raise HTTPException(403, "Regional Manager or Admin authority required.")
    query_filters = []
    if actor_id:
        query_filters.append(AuditEvent.actor_user_id == actor_id)
    if action.strip():
        query_filters.append(AuditEvent.action.ilike(f"%{action.strip()}%"))
    timezone = get_settings().timezone
    if date_from:
        query_filters.append(
            AuditEvent.occurred_at
            >= datetime.combine(date_from, time.min, tzinfo=timezone).astimezone(UTC)
        )
    if date_to:
        query_filters.append(
            AuditEvent.occurred_at
            < datetime.combine(
                date_to + timedelta(days=1), time.min, tzinfo=timezone
            ).astimezone(UTC)
        )
    if managed_ids is None:
        statement = (
            select(AuditEvent)
            .where(*query_filters)
            .order_by(AuditEvent.occurred_at.desc())
            .limit(1500)
        )
        events = list(db.scalars(statement))
    else:
        regional = list(
            db.scalars(
                select(AuditEvent)
                .where(AuditEvent.region_id.in_(managed_ids), *query_filters)
                .order_by(AuditEvent.occurred_at.desc())
                .limit(500)
            )
        )
        cross_region = list(
            db.scalars(
                select(AuditEvent)
                .where(
                    AuditEvent.action.in_(ROSTER_AUDIT_ACTIONS),
                    or_(
                        AuditEvent.region_id.is_(None),
                        AuditEvent.region_id.not_in(managed_ids),
                    ),
                    *query_filters,
                )
                .order_by(AuditEvent.occurred_at.desc())
                .limit(1500)
            )
        )
        events = sorted(
            {event.id: event for event in regional + cross_region}.values(),
            key=lambda event: (event.occurred_at, str(event.id)),
            reverse=True,
        )
    records = permitted_audit_records(
        db, actor=actor, events=events
    )
    visible_region_ids = {
        record.region_id for record in records if record.region_id is not None
    }
    if managed_ids is None:
        visible_region_ids.update(region.id for region in regions)
    else:
        visible_region_ids.update(managed_ids)
    if region_id and region_id not in visible_region_ids:
        raise HTTPException(403, "Audit Region is outside your visible history.")
    visible_people: dict[uuid.UUID, str] = {}
    for record in records:
        for impact in record.impacts:
            visible_people.setdefault(impact.person_id, impact.person_name)
    if person_id and person_id not in visible_people:
        raise HTTPException(403, "Audit employee is outside your visible history.")
    visible_actor_ids = {
        record.event.actor_user_id
        for record in records
        if record.event.actor_user_id is not None
    }
    if actor_id and actor_id not in visible_actor_ids:
        raise HTTPException(403, "Audit actor is outside your visible history.")
    if region_id:
        records = [record for record in records if record.region_id == region_id]
    if person_id:
        records = [
            record
            for record in records
            if any(impact.person_id == person_id for impact in record.impacts)
        ]
    if actor_id:
        records = [
            record for record in records if record.event.actor_user_id == actor_id
        ]
    action_needle = action.strip().casefold()
    if action_needle:
        records = [
            record
            for record in records
            if action_needle in record.event.action.casefold()
        ]
    if date_from:
        records = [
            record
            for record in records
            if local_today(record.event.occurred_at) >= date_from
        ]
    if date_to:
        records = [
            record
            for record in records
            if local_today(record.event.occurred_at) <= date_to
        ]
    needle = search.strip().casefold()
    if needle:
        records = [
            record
            for record in records
            if needle
            in (
                f"{record.event.action} {record.event.target_type} "
                f"{record.event.target_id or ''} {record.visible_detail} "
                + " ".join(
                    f"{impact.person_name} {impact.summary}"
                    for impact in record.impacts
                )
                + (
                    f" {record.revision.track_name_snapshot} {record.revision.work_date}"
                    if record.revision
                    else ""
                )
            ).casefold()
        ]
    records = records[:500]
    user_ids = visible_actor_ids
    users = (
        {user.id: user for user in db.scalars(select(User).where(User.id.in_(user_ids)))} if user_ids else {}
    )
    return templates.TemplateResponse(
        "audit.html",
        context(
            request,
            records=records,
            users=users,
            people=sorted(visible_people.items(), key=lambda item: item[1].casefold()),
            regions=[region for region in regions if region.id in visible_region_ids],
            region_names={region.id: region.name for region in regions},
            selected_region_id=region_id,
            selected_person_id=person_id,
            selected_actor_id=actor_id,
            action_filter=action,
            search_filter=search,
            date_from=date_from,
            date_to=date_to,
        ),
    )
