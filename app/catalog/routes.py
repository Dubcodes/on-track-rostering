from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit.service import record_audit
from app.auth.policy import can_administer_region, require_admin
from app.auth.security import verify_csrf
from app.catalog.models import BasePosition, CrewGroup, Region, Track
from app.catalog.service import allocate_palette_slot
from app.core.database import get_db
from app.core.enums import DeclinePolicy, Lifecycle
from app.core.forms import controlled_integrity, optional_uuid
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


def _active_group(db: Session, raw_id: str) -> CrewGroup | None:
    group_id = optional_uuid(raw_id, "crew group")
    if group_id is None:
        return None
    group = db.get(CrewGroup, group_id)
    if not group or group.lifecycle != Lifecycle.ACTIVE.value:
        raise HTTPException(400, "Select an active crew group.")
    return group


@router.post("/tracks")
def create_track(request: Request, region_id: uuid.UUID = Form(...), name: str = Form(...), map_reference: str = Form(""), csrf_token: str = Form(...), db: Session = Depends(get_db)):
    verify_csrf(request, csrf_token)
    region = db.get(Region, region_id)
    if not region or region.lifecycle != Lifecycle.ACTIVE.value:
        raise HTTPException(400, "Select an active region.")
    if not can_administer_region(request.state.actor, region_id):
        raise HTTPException(403, "Regional administration authority required.")
    try:
        slot = allocate_palette_slot(db, region_id)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    row = Track(region_id=region_id, name=_name(name, 120), palette_slot=slot, map_reference=map_reference.strip()[:500] or None)
    with controlled_integrity(db, "A track in that region already uses that name."):
        db.add(row)
        db.flush()
        record_audit(db, "track.created", "track", row.id, request.state.user.id, region_id=region_id)
        db.commit()
    return RedirectResponse("/manage/catalog#tracks", status_code=303)


@router.post("/tracks/{track_id}")
def update_track(track_id: uuid.UUID, request: Request, name: str = Form(...), region_id: uuid.UUID = Form(...), lifecycle: str = Form(...), map_reference: str = Form(""), csrf_token: str = Form(...), db: Session = Depends(get_db)):
    verify_csrf(request, csrf_token)
    row = db.scalar(select(Track).where(Track.id == track_id).with_for_update())
    if not row:
        raise HTTPException(404)
    if not can_administer_region(request.state.actor, row.region_id) or not can_administer_region(request.state.actor, region_id):
        raise HTTPException(403, "Regional administration authority required.")
    destination = db.get(Region, region_id)
    if not destination or (region_id != row.region_id and destination.lifecycle != "ACTIVE"):
        raise HTTPException(400, "Select an active destination region.")
    previous_region, previous_slot = row.region_id, row.palette_slot
    clean_lifecycle = _lifecycle(lifecycle)
    if region_id != row.region_id or (clean_lifecycle == "ACTIVE" and row.lifecycle != "ACTIVE"):
        try:
            row.palette_slot = allocate_palette_slot(db, region_id, preferred=row.palette_slot, exclude_track_id=row.id)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
    row.name, row.region_id = _name(name, 120), region_id
    row.map_reference, row.lifecycle = map_reference.strip()[:500] or None, clean_lifecycle
    record_audit(db, "track.updated", "track", row.id, request.state.user.id, region_id=row.region_id,
                 detail={"previous_region_id": str(previous_region), "region_id": str(row.region_id),
                         "previous_palette_slot": previous_slot, "palette_slot": row.palette_slot})
    with controlled_integrity(db, "A track in that region already uses that name."):
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
    with controlled_integrity(db, "A region already uses that name."):
        db.commit()
    return RedirectResponse("/manage/catalog#regions", status_code=303)


@router.post("/groups")
def create_group(request: Request, name: str = Form(...), csrf_token: str = Form(...), db: Session = Depends(get_db)):
    require_admin(request.state.actor)
    verify_csrf(request, csrf_token)
    row = CrewGroup(name=_name(name, 100))
    with controlled_integrity(db, "A crew group already uses that name."):
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
    with controlled_integrity(db, "A crew group already uses that name."):
        db.commit()
    return RedirectResponse("/manage/catalog#groups", status_code=303)


@router.post("/positions/{position_id}")
def update_position(position_id: uuid.UUID, request: Request, name: str = Form(...), crew_group_id: str = Form(""), lifecycle: str = Form(...), csrf_token: str = Form(...), db: Session = Depends(get_db)):
    require_admin(request.state.actor)
    verify_csrf(request, csrf_token)
    row = db.get(BasePosition, position_id)
    if not row:
        raise HTTPException(404)
    group = _active_group(db, crew_group_id)
    clean_name = _name(name, 100)
    clean_lifecycle = _lifecycle(lifecycle)
    group_id = group.id if group else None
    duplicate = db.scalar(
        select(BasePosition.id).where(
            BasePosition.id != row.id,
            BasePosition.name == clean_name,
            BasePosition.crew_group_id == group_id,
        )
    )
    if duplicate:
        raise HTTPException(409, "A base position in that crew group already uses that name.")
    row.name, row.lifecycle, row.crew_group_id = clean_name, clean_lifecycle, group_id
    record_audit(db, "position.updated", "base_position", row.id, request.state.user.id)
    with controlled_integrity(db, "A base position in that crew group already uses that name."):
        db.commit()
    return RedirectResponse("/manage/catalog#positions", status_code=303)
