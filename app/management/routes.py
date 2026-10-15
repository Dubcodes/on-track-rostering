from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.policy import can_administer_region, can_manage_region
from app.catalog.models import Region
from app.core.database import get_db
from app.core.enums import Role
from app.identity.models import Person
from app.notices.service import (
    permitted_notice_region_ids,
    recent_notices,
    relevant_notice_region_ids,
)
from app.web import context, templates

router = APIRouter(prefix="/manage")


@router.get("", response_class=HTMLResponse)
def management_hub(request: Request, db: Session = Depends(get_db)):
    actor = request.state.actor
    if actor.is_contractor:
        raise HTTPException(403, "Management access is not available.")
    regions = list(
        db.scalars(select(Region).where(Region.lifecycle == "ACTIVE").order_by(Region.name))
    )
    can_build = any(can_manage_region(actor, region.id) for region in regions)
    can_manage_people = any(can_administer_region(actor, region.id) for region in regions)
    can_team_hours = actor.is_admin or any(
        roles & {Role.MANAGER.value, Role.SUB_MANAGER.value, Role.VIEWER.value}
        for roles in actor.regional_roles.values()
    )
    notice_region_ids = permitted_notice_region_ids(db, actor)
    if not (can_build or can_manage_people or can_team_hours or notice_region_ids or actor.is_admin):
        raise HTTPException(403, "Management access is not available.")
    notice_regions = [region for region in regions if region.id in notice_region_ids]
    primary_region_id = (
        db.scalar(select(Person.home_region_id).where(Person.id == actor.person_id))
        if actor.person_id
        else None
    )
    notice_default_region_id = (
        primary_region_id
        if primary_region_id in notice_region_ids
        else (notice_regions[0].id if notice_regions else None)
    )
    notice_default_region = next(
        (region for region in notice_regions if region.id == notice_default_region_id), None
    )
    return templates.TemplateResponse(
        "management.html",
        context(
            request,
            can_build=can_build,
            can_manage_people=can_manage_people,
            can_team_hours=can_team_hours,
            notice_regions=notice_regions,
            notice_default_region_id=notice_default_region_id,
            notice_default_region=notice_default_region,
            notice_history=recent_notices(
                db, relevant_notice_region_ids(db, actor), actor.is_admin
            ),
        ),
    )
