from __future__ import annotations

import re
import uuid

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit.service import record_audit
from app.auth.policy import can_administer_region, require_admin
from app.auth.security import verify_csrf
from app.catalog.models import BasePosition, CrewGroup, Region, Track
from app.core.database import get_db
from app.core.enums import DeclinePolicy, Lifecycle
from app.web import context, templates

router = APIRouter(prefix="/manage/catalog")


def _lifecycle(value: str) -> str:
    if value not in {Lifecycle.ACTIVE.value, Lifecycle.ARCHIVED.value}:
        raise HTTPException(400, "Invalid lifecycle.")
    return value


def _name(value: str, maximum: int) -> str:
    result = value.strip()
    if not 2 <= len(result) <= maximum:
        raise HTTPException(400, f"Name must be between 2 and {maximum} characters.")
    return result


@router.get("", response_class=HTMLResponse)
def catalog_page(request: Request, db: Session = Depends(get_db)):
    regions = [
        row
        for row in db.scalars(select(Region).order_by(Region.name))
        if can_administer_region(request.state.actor, row.id)
    ]
    if not regions:
        raise HTTPException(403)
    region_ids = [row.id for row in regions]
    return templates.TemplateResponse(
        "catalog.html",
        context(
            request,
            regions=regions,
            tracks=list(db.scalars(select(Track).where(Track.region_id.in_(region_ids)).order_by(Track.name))),
            groups=list(db.scalars(select(CrewGroup).order_by(CrewGroup.name))),
            positions=list(db.scalars(select(BasePosition).order_by(BasePosition.name))),
            decline_policies=[item.value for item in DeclinePolicy],
        ),
    )


def _colour(value: str) -> str:
    if not re.fullmatch(r"#[0-9A-Fa-f]{6}", value):
        raise HTTPException(400, "Track colour must be a six-digit hex colour.")
    return value.upper()


@router.post("/tracks")
def create_track(request: Request, region_id: uuid.UUID = Form(...), name: str = Form(...), display_colour: str = Form(...), map_reference: str = Form(""), csrf_token: str = Form(...), db: Session = Depends(get_db)):
    verify_csrf(request, csrf_token)
    if not can_administer_region(request.state.actor, region_id):
        raise HTTPException(403, "Regional administration authority required.")
    row = Track(region_id=region_id, name=_name(name, 120), display_colour=_colour(display_colour), map_reference=map_reference.strip()[:500] or None)
    db.add(row)
    db.flush()
    record_audit(db, "track.created", "track", row.id, request.state.user.id, region_id=region_id)
    db.commit()
    return RedirectResponse("/manage/catalog#tracks", status_code=303)


@router.post("/tracks/{track_id}")
def update_track(track_id: uuid.UUID, request: Request, name: str = Form(...), display_colour: str = Form(...), lifecycle: str = Form(...), map_reference: str = Form(""), csrf_token: str = Form(...), db: Session = Depends(get_db)):
    verify_csrf(request, csrf_token)
    row = db.get(Track, track_id)
    if not row:
        raise HTTPException(404)
    if not can_administer_region(request.state.actor, row.region_id):
        raise HTTPException(403, "Regional administration authority required.")
    row.name, row.display_colour = _name(name, 120), _colour(display_colour)
    row.map_reference, row.lifecycle = map_reference.strip()[:500] or None, _lifecycle(lifecycle)
    record_audit(db, "track.updated", "track", row.id, request.state.user.id, region_id=row.region_id)
    db.commit()
    return RedirectResponse("/manage/catalog#tracks", status_code=303)


@router.post("/regions/{region_id}")
def update_region(region_id: uuid.UUID, request: Request, name: str = Form(...), lifecycle: str = Form(...), decline_policy: str = Form(...), lead_minutes_race_day: int = Form(...), statutory_holiday_region: str = Form(""), csrf_token: str = Form(...), db: Session = Depends(get_db)):
    require_admin(request.state.actor)
    verify_csrf(request, csrf_token)
    row = db.get(Region, region_id)
    if not row:
        raise HTTPException(404)
    if decline_policy not in {item.value for item in DeclinePolicy} or not 0 <= lead_minutes_race_day <= 1440:
        raise HTTPException(400, "Invalid region policy.")
    row.name, row.lifecycle = _name(name, 100), _lifecycle(lifecycle)
    row.decline_policy, row.lead_minutes_race_day = decline_policy, lead_minutes_race_day
    row.statutory_holiday_region = statutory_holiday_region.strip()[:80] or None
    record_audit(db, "region.updated", "region", row.id, request.state.user.id, region_id=row.id)
    db.commit()
    return RedirectResponse("/manage/catalog#regions", status_code=303)


@router.post("/groups")
def create_group(request: Request, name: str = Form(...), csrf_token: str = Form(...), db: Session = Depends(get_db)):
    require_admin(request.state.actor)
    verify_csrf(request, csrf_token)
    row = CrewGroup(name=_name(name, 100))
    db.add(row)
    db.flush()
    record_audit(db, "crew_group.created", "crew_group", row.id, request.state.user.id)
    db.commit()
    return RedirectResponse("/manage/catalog#groups", status_code=303)


@router.post("/groups/{group_id}")
def update_group(group_id: uuid.UUID, request: Request, name: str = Form(...), lifecycle: str = Form(...), csrf_token: str = Form(...), db: Session = Depends(get_db)):
    require_admin(request.state.actor)
    verify_csrf(request, csrf_token)
    row = db.get(CrewGroup, group_id)
    if not row:
        raise HTTPException(404)
    row.name, row.lifecycle = _name(name, 100), _lifecycle(lifecycle)
    record_audit(db, "crew_group.updated", "crew_group", row.id, request.state.user.id)
    db.commit()
    return RedirectResponse("/manage/catalog#groups", status_code=303)


@router.post("/positions/{position_id}")
def update_position(position_id: uuid.UUID, request: Request, name: str = Form(...), crew_group_id: str = Form(""), lifecycle: str = Form(...), csrf_token: str = Form(...), db: Session = Depends(get_db)):
    require_admin(request.state.actor)
    verify_csrf(request, csrf_token)
    row = db.get(BasePosition, position_id)
    if not row:
        raise HTTPException(404)
    row.name, row.lifecycle = _name(name, 100), _lifecycle(lifecycle)
    row.crew_group_id = uuid.UUID(crew_group_id) if crew_group_id else None
    record_audit(db, "position.updated", "base_position", row.id, request.state.user.id)
    db.commit()
    return RedirectResponse("/manage/catalog#positions", status_code=303)
