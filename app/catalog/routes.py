from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.audit.models import AuditEvent
from app.audit.service import record_audit
from app.auth.policy import can_administer_region, require_admin
from app.auth.security import verify_csrf
from app.catalog.models import BasePosition, CrewGroup, Region, Track, Vehicle
from app.catalog.service import (
    allocate_palette_slot,
    business_reference_tables,
    normalized_track_match,
)
from app.core.database import get_db
from app.core.enums import DeclinePolicy, Lifecycle
from app.core.forms import controlled_integrity, optional_uuid
from app.positions.ordering import catalog_position_order
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
    visible_regions = [
        row
        for row in db.scalars(select(Region).order_by(Region.name))
        if can_administer_region(request.state.actor, row.id)
    ]
    if not visible_regions:
        raise HTTPException(403)
    active_regions = [row for row in visible_regions if row.lifecycle == Lifecycle.ACTIVE.value]
    archived_regions = [row for row in visible_regions if row.lifecycle == Lifecycle.ARCHIVED.value]
    region_ids = [row.id for row in visible_regions]
    visible_tracks = list(
        db.scalars(select(Track).where(Track.region_id.in_(region_ids)).order_by(Track.name))
    )
    active_region_ids = {row.id for row in active_regions}
    vehicle_statement = select(Vehicle).order_by(Vehicle.name)
    if not request.state.actor.is_admin:
        vehicle_statement = vehicle_statement.where(Vehicle.home_region_id.in_(region_ids))
    vehicles = list(db.scalars(vehicle_statement))
    return templates.TemplateResponse(
        "catalog.html",
        context(
            request,
            regions=active_regions,
            archived_regions=archived_regions,
            tracks=[
                row
                for row in visible_tracks
                if row.lifecycle == Lifecycle.ACTIVE.value and row.region_id in active_region_ids
            ],
            archived_tracks=[
                row
                for row in visible_tracks
                if row.lifecycle == Lifecycle.ARCHIVED.value or row.region_id not in active_region_ids
            ],
            groups=list(db.scalars(select(CrewGroup).order_by(CrewGroup.name))),
            positions=sorted(db.scalars(select(BasePosition)), key=catalog_position_order),
            vehicles=vehicles,
            decline_policies=[item.value for item in DeclinePolicy],
        ),
    )


@router.post("/regions")
def create_region(
    request: Request,
    name: str = Form(...),
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
):
    require_admin(request.state.actor)
    verify_csrf(request, csrf_token)
    row = Region(name=_name(name, 100))
    with controlled_integrity(db, "A region already uses that name."):
        db.add(row)
        db.flush()
        record_audit(
            db,
            "region.created",
            "region",
            row.id,
            request.state.user.id,
            detail={"name": row.name},
        )
        db.commit()
    return RedirectResponse("/manage/catalog#regions", status_code=303)


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
    clean_name = _name(name, 120)
    if normalized_track_match(db, region_id, clean_name):
        raise HTTPException(409, "A track in that region already uses that normalized name.")
    try:
        slot = allocate_palette_slot(db, region_id)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    row = Track(region_id=region_id, name=clean_name, palette_slot=slot, map_reference=map_reference.strip()[:500] or None)
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
    clean_name = _name(name, 120)
    if normalized_track_match(db, region_id, clean_name, exclude_track_id=row.id):
        raise HTTPException(409, "A track in that region already uses that normalized name.")
    if region_id != row.region_id or (clean_lifecycle == "ACTIVE" and row.lifecycle != "ACTIVE"):
        try:
            row.palette_slot = allocate_palette_slot(db, region_id, preferred=row.palette_slot, exclude_track_id=row.id)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
    row.name, row.region_id = clean_name, region_id
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


@router.post("/regions/{region_id}/lifecycle")
def set_region_lifecycle(
    region_id: uuid.UUID,
    request: Request,
    lifecycle: str = Form(...),
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
):
    require_admin(request.state.actor)
    verify_csrf(request, csrf_token)
    row = db.get(Region, region_id)
    if not row:
        raise HTTPException(404)
    previous = row.lifecycle
    row.lifecycle = _lifecycle(lifecycle)
    record_audit(
        db,
        "region.lifecycle_updated",
        "region",
        row.id,
        request.state.user.id,
        region_id=row.id,
        detail={"previous": previous, "lifecycle": row.lifecycle},
    )
    db.commit()
    return RedirectResponse("/manage/catalog#regions", status_code=303)


@router.post("/tracks/{track_id}/lifecycle")
def set_track_lifecycle(
    track_id: uuid.UUID,
    request: Request,
    lifecycle: str = Form(...),
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
):
    verify_csrf(request, csrf_token)
    row = db.scalar(select(Track).where(Track.id == track_id).with_for_update())
    if not row:
        raise HTTPException(404)
    if not can_administer_region(request.state.actor, row.region_id):
        raise HTTPException(403, "Regional administration authority required.")
    desired = _lifecycle(lifecycle)
    if desired == Lifecycle.ACTIVE.value and row.lifecycle != Lifecycle.ACTIVE.value:
        try:
            row.palette_slot = allocate_palette_slot(
                db, row.region_id, preferred=row.palette_slot, exclude_track_id=row.id
            )
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
    previous = row.lifecycle
    row.lifecycle = desired
    record_audit(
        db,
        "track.lifecycle_updated",
        "track",
        row.id,
        request.state.user.id,
        region_id=row.region_id,
        detail={"previous": previous, "lifecycle": row.lifecycle},
    )
    db.commit()
    return RedirectResponse("/manage/catalog#tracks", status_code=303)


def _confirmed_remove(value: str) -> None:
    if value != "yes":
        raise HTTPException(400, "Confirm Remove unused before continuing.")


@router.post("/tracks/{track_id}/remove-unused")
def remove_unused_track(
    track_id: uuid.UUID,
    request: Request,
    confirm_remove: str = Form(""),
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
):
    verify_csrf(request, csrf_token)
    _confirmed_remove(confirm_remove)
    row = db.scalar(select(Track).where(Track.id == track_id).with_for_update())
    if not row:
        raise HTTPException(404)
    if not can_administer_region(request.state.actor, row.region_id):
        raise HTTPException(403, "Regional administration authority required.")
    blockers = business_reference_tables(db, row)
    if blockers:
        raise HTTPException(
            409, "This record has history and cannot be deleted. Archive it instead."
        )
    record_audit(
        db,
        "track.removed_unused",
        "track",
        row.id,
        request.state.user.id,
        region_id=row.region_id,
        detail={"name": row.name},
    )
    db.delete(row)
    db.commit()
    return RedirectResponse("/manage/catalog#tracks", status_code=303)


@router.post("/regions/{region_id}/remove-unused")
def remove_unused_region(
    region_id: uuid.UUID,
    request: Request,
    confirm_remove: str = Form(""),
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
):
    require_admin(request.state.actor)
    verify_csrf(request, csrf_token)
    _confirmed_remove(confirm_remove)
    row = db.scalar(select(Region).where(Region.id == region_id).with_for_update())
    if not row:
        raise HTTPException(404)
    blockers = business_reference_tables(db, row)
    if blockers:
        raise HTTPException(
            409, "This record has history and cannot be deleted. Archive it instead."
        )
    db.execute(
        update(AuditEvent).where(AuditEvent.region_id == row.id).values(region_id=None)
    )
    record_audit(
        db,
        "region.removed_unused",
        "region",
        row.id,
        request.state.user.id,
        detail={"name": row.name},
    )
    db.delete(row)
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
def update_position(position_id: uuid.UUID, request: Request, name: str = Form(...), crew_group_id: str = Form(""), display_order: int | None = Form(None), lifecycle: str = Form(...), csrf_token: str = Form(...), db: Session = Depends(get_db)):
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
    if display_order is not None and display_order < 0:
        raise HTTPException(400, "Position order must be zero or greater.")
    previous = {"name": row.name, "display_order": row.display_order, "lifecycle": row.lifecycle}
    row.name, row.lifecycle, row.crew_group_id = clean_name, clean_lifecycle, group_id
    row.display_order = display_order
    record_audit(db, "position.updated", "base_position", row.id, request.state.user.id,
                 detail={"previous": previous, "name": row.name, "display_order": row.display_order,
                         "lifecycle": row.lifecycle})
    with controlled_integrity(db, "A base position in that crew group already uses that name."):
        db.commit()
    return RedirectResponse("/manage/catalog#positions", status_code=303)


@router.post("/positions")
def create_position(
    request: Request,
    name: str = Form(...),
    crew_group_id: str = Form(""),
    display_order: int | None = Form(None),
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
):
    require_admin(request.state.actor)
    verify_csrf(request, csrf_token)
    if display_order is not None and display_order < 0:
        raise HTTPException(400, "Position order must be zero or greater.")
    group = _active_group(db, crew_group_id)
    row = BasePosition(
        name=_name(name, 100),
        crew_group_id=group.id if group else None,
        display_order=display_order,
    )
    duplicate = db.scalar(
        select(BasePosition.id).where(
            BasePosition.name == row.name,
            BasePosition.crew_group_id == row.crew_group_id,
        )
    )
    if duplicate:
        raise HTTPException(409, "A base position in that crew group already uses that name.")
    with controlled_integrity(db, "A base position in that crew group already uses that name."):
        db.add(row)
        db.flush()
        record_audit(
            db,
            "position.created",
            "base_position",
            row.id,
            request.state.user.id,
            detail={"name": row.name},
        )
        db.commit()
    return RedirectResponse("/manage/catalog#positions", status_code=303)


def _vehicle_region(db: Session, actor, region_id: uuid.UUID) -> Region:
    region = db.get(Region, region_id)
    if not region or region.lifecycle != Lifecycle.ACTIVE.value:
        raise HTTPException(400, "Select an active home region.")
    if not can_administer_region(actor, region.id):
        raise HTTPException(403, "Regional administration authority required.")
    return region


@router.post("/vehicles")
def create_vehicle(
    request: Request,
    name: str = Form(...),
    home_region_id: uuid.UUID = Form(...),
    category: str = Form("Operational"),
    model: str = Form(""),
    fuel_type: str = Form(""),
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
):
    verify_csrf(request, csrf_token)
    _vehicle_region(db, request.state.actor, home_region_id)
    row = Vehicle(name=_name(name, 100), home_region_id=home_region_id,
                  category=_name(category, 80), model=model.strip()[:100] or None,
                  fuel_type=fuel_type.strip()[:40] or None)
    with controlled_integrity(db, "A vehicle already uses that name."):
        db.add(row)
        db.flush()
        record_audit(db, "vehicle.created", "vehicle", row.id, request.state.user.id,
                     region_id=row.home_region_id,
                     detail={"name": row.name, "category": row.category, "model": row.model})
        db.commit()
    return RedirectResponse("/manage/catalog#vehicles", status_code=303)


@router.post("/vehicles/{vehicle_id}")
def update_vehicle(
    vehicle_id: uuid.UUID,
    request: Request,
    name: str = Form(...),
    home_region_id: uuid.UUID = Form(...),
    category: str = Form("Operational"),
    model: str = Form(""),
    fuel_type: str = Form(""),
    lifecycle: str = Form(...),
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
):
    verify_csrf(request, csrf_token)
    row = db.get(Vehicle, vehicle_id)
    if not row:
        raise HTTPException(404)
    if not request.state.actor.is_admin and (
        row.home_region_id is None
        or not can_administer_region(request.state.actor, row.home_region_id)
    ):
        raise HTTPException(403, "Regional administration authority required.")
    _vehicle_region(db, request.state.actor, home_region_id)
    previous = {"name": row.name, "home_region_id": str(row.home_region_id),
                "category": row.category, "model": row.model, "fuel_type": row.fuel_type,
                "lifecycle": row.lifecycle}
    row.name, row.home_region_id = _name(name, 100), home_region_id
    row.category, row.model = _name(category, 80), model.strip()[:100] or None
    row.fuel_type, row.lifecycle = fuel_type.strip()[:40] or None, _lifecycle(lifecycle)
    record_audit(db, "vehicle.updated", "vehicle", row.id, request.state.user.id,
                 region_id=row.home_region_id,
                 detail={"previous": previous, "name": row.name, "home_region_id": str(row.home_region_id),
                         "category": row.category, "model": row.model, "fuel_type": row.fuel_type,
                         "lifecycle": row.lifecycle})
    with controlled_integrity(db, "A vehicle already uses that name."):
        db.commit()
    return RedirectResponse("/manage/catalog#vehicles", status_code=303)
