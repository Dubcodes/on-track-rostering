from __future__ import annotations

import uuid
from datetime import date, datetime, time

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit.models import AuditEvent
from app.auth.policy import can_administer_region
from app.catalog.models import Region
from app.core.database import get_db
from app.identity.models import User
from app.web import context, templates

router = APIRouter(prefix="/manage/audit")


@router.get("", response_class=HTMLResponse)
def audit_page(
    request: Request,
    region_id: uuid.UUID | None = None,
    actor_id: uuid.UUID | None = None,
    action: str = "",
    search: str = "",
    date_from: date | None = Query(None),
    date_to: date | None = Query(None),
    db: Session = Depends(get_db),
):
    actor = request.state.actor
    regions = list(db.scalars(select(Region).order_by(Region.name)))
    allowed = (
        regions
        if actor.is_admin
        else [region for region in regions if can_administer_region(actor, region.id)]
    )
    if not allowed:
        raise HTTPException(403, "Regional Manager or Admin authority required.")
    allowed_ids = {region.id for region in allowed}
    if region_id and region_id not in allowed_ids:
        raise HTTPException(403, "Audit region is outside your authority.")
    statement = select(AuditEvent).order_by(AuditEvent.occurred_at.desc()).limit(500)
    if not actor.is_admin:
        statement = statement.where(AuditEvent.region_id.in_(allowed_ids))
    if region_id:
        statement = statement.where(AuditEvent.region_id == region_id)
    if actor_id:
        statement = statement.where(AuditEvent.actor_user_id == actor_id)
    if action.strip():
        statement = statement.where(AuditEvent.action.ilike(f"%{action.strip()}%"))
    if date_from:
        statement = statement.where(AuditEvent.occurred_at >= datetime.combine(date_from, time.min))
    if date_to:
        statement = statement.where(AuditEvent.occurred_at <= datetime.combine(date_to, time.max))
    events = list(db.scalars(statement))
    needle = search.strip().casefold()
    if needle:
        events = [
            event
            for event in events
            if needle
            in (f"{event.action} {event.target_type} {event.target_id or ''} {event.detail}").casefold()
        ]
    user_ids = {event.actor_user_id for event in events if event.actor_user_id}
    users = (
        {user.id: user for user in db.scalars(select(User).where(User.id.in_(user_ids)))} if user_ids else {}
    )
    return templates.TemplateResponse(
        "audit.html",
        context(
            request,
            events=events,
            users=users,
            regions=allowed,
            region_names={region.id: region.name for region in regions},
            selected_region_id=region_id,
            selected_actor_id=actor_id,
            action_filter=action,
            search_filter=search,
            date_from=date_from,
            date_to=date_to,
        ),
    )
