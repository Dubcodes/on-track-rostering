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
from app.catalog.models import (
    BasePosition,
    CrewGroup,
    EquipmentSetup,
    EquipmentSetupItem,
    OperationalPlace,
    PositionPreset,
    Region,
    Track,
    TrackMap,
    Vehicle,
)
from app.catalog.presets import (
    PRESET_KEYS,
    effective_position_presets,
    reset_position_preset,
    save_position_preset,
)
from app.catalog.service import (
    allocate_palette_slot,
    business_reference_tables,
    normalized_track_match,
)
from app.core.database import get_db
from app.core.enums import DeclinePolicy, Lifecycle
from app.core.forms import controlled_integrity, optional_uuid
from app.positions.canonical import validate_position_identity
from app.positions.ordering import catalog_position_order
from app.track_maps.service import effective_map, map_status
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
    places = list(
        db.scalars(
            select(OperationalPlace)
            .where(OperationalPlace.region_id.in_(region_ids))
            .order_by(OperationalPlace.name)
        )
    )
    equipment_setups = list(
        db.scalars(
            select(EquipmentSetup)
            .where(EquipmentSetup.region_id.in_(region_ids))
            .order_by(EquipmentSetup.name)
        )
    )
    equipment_items_by_setup: dict[uuid.UUID, list[EquipmentSetupItem]] = {}
    if equipment_setups:
        for item in db.scalars(
            select(EquipmentSetupItem)
            .where(EquipmentSetupItem.setup_id.in_([row.id for row in equipment_setups]))
            .order_by(EquipmentSetupItem.display_order, EquipmentSetupItem.name)
        ):
            equipment_items_by_setup.setdefault(item.setup_id, []).append(item)
    map_by_track = {
        row.track_id: row
        for row in db.scalars(select(TrackMap).where(TrackMap.track_id.in_([t.id for t in visible_tracks])))
    }
    effective_map_by_track = {
        track_id: effective_map(row) for track_id, row in map_by_track.items()
    }
    map_status_by_track = {
        track.id: map_status(map_by_track.get(track.id)) for track in visible_tracks
    }
    active_region_ids = {row.id for row in active_regions}
    vehicle_statement = select(Vehicle).order_by(Vehicle.name)
    if not request.state.actor.is_admin:
        vehicle_statement = vehicle_statement.where(Vehicle.home_region_id.in_(region_ids))
    vehicles = list(db.scalars(vehicle_statement))
    positions = sorted(db.scalars(select(BasePosition)), key=catalog_position_order)
    effective_presets = effective_position_presets(db, region_ids)
    overridden_presets = {
        (str(row.region_id), row.preset_key)
        for row in db.scalars(
            select(PositionPreset).where(PositionPreset.region_id.in_(region_ids))
        )
    }
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
            map_by_track=map_by_track,
            effective_map_by_track=effective_map_by_track,
            map_status_by_track=map_status_by_track,
            groups=list(db.scalars(select(CrewGroup).order_by(CrewGroup.name))),
            positions=positions,
            active_positions=[row for row in positions if row.lifecycle == Lifecycle.ACTIVE.value],
            vehicles=vehicles,
            operational_places=places,
            equipment_setups=equipment_setups,
            equipment_items_by_setup=equipment_items_by_setup,
            preset_keys=PRESET_KEYS,
            preset_labels={
                "THOROUGHBRED": "Thoroughbred standard",
                "HARNESS": "Harness standard",
                "TRIALS": "Trials",
            },
            effective_presets=effective_presets,
            overridden_presets=overridden_presets,
            decline_policies=[item.value for item in DeclinePolicy],
        ),
    )


def _preset_region(db: Session, request: Request, region_id: uuid.UUID) -> Region:
    region = db.get(Region, region_id)
    if not region or region.lifecycle != Lifecycle.ACTIVE.value:
        raise HTTPException(400, "Select an active region.")
    if not can_administer_region(request.state.actor, region.id):
        raise HTTPException(403, "Regional administration authority required.")
    return region


@router.post("/regions/{region_id}/presets/{preset_key}")
def update_position_preset(
    region_id: uuid.UUID,
    preset_key: str,
    request: Request,
    position_ids: list[uuid.UUID] = Form(default=[]),
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
):
    verify_csrf(request, csrf_token)
    region = _preset_region(db, request, region_id)
    try:
        row = save_position_preset(
            db,
            region_id=region.id,
            preset_key=preset_key,
            position_ids=position_ids,
            actor_user_id=request.state.user.id,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    record_audit(
        db,
        "position_preset.updated",
        "position_preset",
        row.id,
        request.state.user.id,
        region_id=region.id,
        detail={"preset_key": preset_key, "position_ids": [str(item) for item in position_ids]},
    )
    db.commit()
    return RedirectResponse(f"/manage/catalog#presets-{region.id}", status_code=303)


@router.post("/regions/{region_id}/presets/{preset_key}/reset")
def reset_region_position_preset(
    region_id: uuid.UUID,
    preset_key: str,
    request: Request,
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
):
    verify_csrf(request, csrf_token)
    region = _preset_region(db, request, region_id)
    try:
        row = reset_position_preset(db, region_id=region.id, preset_key=preset_key)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    record_audit(
        db,
        "position_preset.reset",
        "position_preset",
        row.id if row else region.id,
        request.state.user.id,
        region_id=region.id,
        detail={"preset_key": preset_key},
    )
    db.commit()
    return RedirectResponse(f"/manage/catalog#presets-{region.id}", status_code=303)


@router.post("/regions")
def create_region(
    request: Request,
    name: str = Form(...),
    base_name: str = Form(""),
    base_address: str = Form(""),
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
):
    require_admin(request.state.actor)
    verify_csrf(request, csrf_token)
    row = Region(
        name=_name(name, 100), base_name=base_name.strip()[:160] or None,
        base_address=base_address.strip()[:500] or None,
    )
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
def create_track(request: Request, region_id: uuid.UUID = Form(...), name: str = Form(...), address: str = Form(""), map_reference: str = Form(""), default_travel_minutes: int | None = Form(None), csrf_token: str = Form(...), db: Session = Depends(get_db)):
    verify_csrf(request, csrf_token)
    region = db.get(Region, region_id)
    if not region or region.lifecycle != Lifecycle.ACTIVE.value:
        raise HTTPException(400, "Select an active region.")
    if not can_administer_region(request.state.actor, region_id):
        raise HTTPException(403, "Regional administration authority required.")
    clean_name = _name(name, 120)
    if default_travel_minutes is not None and not 0 <= default_travel_minutes <= 1440:
        raise HTTPException(400, "Default travel must be between 0 and 1440 minutes.")
    if normalized_track_match(db, region_id, clean_name):
        raise HTTPException(409, "A track in that region already uses that normalized name.")
    try:
        slot = allocate_palette_slot(db, region_id)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    row = Track(region_id=region_id, name=clean_name, palette_slot=slot, address=address.strip()[:500] or None, map_reference=map_reference.strip()[:500] or None, default_travel_minutes=default_travel_minutes)
    with controlled_integrity(db, "A track in that region already uses that name."):
        db.add(row)
        db.flush()
        record_audit(db, "track.created", "track", row.id, request.state.user.id, region_id=region_id,
                     detail={"default_travel_minutes": row.default_travel_minutes})
        db.commit()
    return RedirectResponse(f"/manage/catalog?created=track#track-{row.id}", status_code=303)


@router.post("/tracks/{track_id}")
def update_track(track_id: uuid.UUID, request: Request, name: str = Form(...), region_id: uuid.UUID = Form(...), lifecycle: str = Form(...), address: str = Form(""), map_reference: str = Form(""), default_travel_minutes: int | None = Form(None), csrf_token: str = Form(...), db: Session = Depends(get_db)):
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
    if default_travel_minutes is not None and not 0 <= default_travel_minutes <= 1440:
        raise HTTPException(400, "Default travel must be between 0 and 1440 minutes.")
    if normalized_track_match(db, region_id, clean_name, exclude_track_id=row.id):
        raise HTTPException(409, "A track in that region already uses that normalized name.")
    if region_id != row.region_id or (clean_lifecycle == "ACTIVE" and row.lifecycle != "ACTIVE"):
        try:
            row.palette_slot = allocate_palette_slot(db, region_id, preferred=row.palette_slot, exclude_track_id=row.id)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
    previous_default_travel_minutes = row.default_travel_minutes
    row.name, row.region_id = clean_name, region_id
    row.address = address.strip()[:500] or None
    row.map_reference, row.lifecycle = map_reference.strip()[:500] or None, clean_lifecycle
    row.default_travel_minutes = default_travel_minutes
    record_audit(db, "track.updated", "track", row.id, request.state.user.id, region_id=row.region_id,
                 detail={"previous_region_id": str(previous_region), "region_id": str(row.region_id),
                         "previous_palette_slot": previous_slot, "palette_slot": row.palette_slot,
                         "previous_default_travel_minutes": previous_default_travel_minutes,
                         "default_travel_minutes": row.default_travel_minutes})
    with controlled_integrity(db, "A track in that region already uses that name."):
        db.commit()
    return RedirectResponse(f"/manage/catalog?saved=track#track-{row.id}", status_code=303)


@router.post("/regions/{region_id}")
def update_region(region_id: uuid.UUID, request: Request, name: str = Form(...), lifecycle: str = Form(...), decline_policy: str = Form(...), lead_minutes_race_day: int = Form(...), statutory_holiday_region: str = Form(""), base_name: str = Form(""), base_address: str = Form(""), csrf_token: str = Form(...), db: Session = Depends(get_db)):
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
    row.base_name = base_name.strip()[:160] or None
    row.base_address = base_address.strip()[:500] or None
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
    if clean_lifecycle == Lifecycle.ACTIVE.value:
        try:
            identity = validate_position_identity(db, clean_name, exclude_id=row.id)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        clean_name = identity.canonical_name
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
    clean_name = _name(name, 100)
    try:
        identity = validate_position_identity(db, clean_name)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    row = BasePosition(
        name=identity.canonical_name,
        crew_group_id=group.id if group else None,
        display_order=display_order,
    )
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


def _catalog_region(db: Session, actor, region_id: uuid.UUID) -> Region:
    region = db.get(Region, region_id)
    if not region or region.lifecycle != Lifecycle.ACTIVE.value:
        raise HTTPException(400, "Select an active region.")
    if not can_administer_region(actor, region.id):
        raise HTTPException(403, "Regional administration authority required.")
    return region


@router.post("/places")
def create_operational_place(
    request: Request,
    region_id: uuid.UUID = Form(...),
    name: str = Form(...),
    kind: str = Form(...),
    address: str = Form(""),
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
):
    verify_csrf(request, csrf_token)
    _catalog_region(db, request.state.actor, region_id)
    if kind not in {"ACCOMMODATION", "DESTINATION"}:
        raise HTTPException(400, "Select a valid place type.")
    row = OperationalPlace(
        region_id=region_id,
        name=_name(name, 160),
        kind=kind,
        address=address.strip()[:500] or None,
    )
    with controlled_integrity(db, "A place in that region already uses that name."):
        db.add(row)
        db.flush()
        record_audit(
            db, "operational_place.created", "operational_place", row.id,
            request.state.user.id, region_id=region_id,
            detail={"name": row.name, "kind": row.kind},
        )
        db.commit()
    return RedirectResponse("/manage/catalog#places", status_code=303)


@router.post("/places/{place_id}")
def update_operational_place(
    place_id: uuid.UUID,
    request: Request,
    name: str = Form(...),
    kind: str = Form(...),
    address: str = Form(""),
    lifecycle: str = Form(...),
    expected_version: int = Form(...),
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
):
    verify_csrf(request, csrf_token)
    row = db.scalar(select(OperationalPlace).where(OperationalPlace.id == place_id).with_for_update())
    if not row:
        raise HTTPException(404)
    _catalog_region(db, request.state.actor, row.region_id)
    if row.lock_version != expected_version:
        raise HTTPException(409, "This place changed. Reload it before saving.")
    if kind not in {"ACCOMMODATION", "DESTINATION"}:
        raise HTTPException(400, "Select a valid place type.")
    previous = {"name": row.name, "kind": row.kind, "address": row.address, "lifecycle": row.lifecycle}
    row.name = _name(name, 160)
    row.kind = kind
    row.address = address.strip()[:500] or None
    row.lifecycle = _lifecycle(lifecycle)
    row.lock_version += 1
    record_audit(
        db, "operational_place.updated", "operational_place", row.id,
        request.state.user.id, region_id=row.region_id,
        detail={"previous": previous, "name": row.name, "kind": row.kind, "lifecycle": row.lifecycle},
    )
    with controlled_integrity(db, "A place in that region already uses that name."):
        db.commit()
    return RedirectResponse("/manage/catalog#places", status_code=303)


def _equipment_rows(names: list[str], quantities: list[int], notes: list[str]) -> list[tuple[str, int, str]]:
    if not (len(names) == len(quantities) == len(notes)):
        raise HTTPException(400, "The equipment rows were incomplete.")
    result = []
    for name, quantity, note in zip(names, quantities, notes, strict=True):
        clean_name = name.strip()
        if not clean_name and not note.strip():
            continue
        if not clean_name:
            raise HTTPException(400, "Enter a name for each equipment item.")
        if not 1 <= quantity <= 999:
            raise HTTPException(400, "Equipment quantity must be between 1 and 999.")
        result.append((_name(clean_name, 160), quantity, note.strip()[:500]))
    return result


@router.post("/equipment-setups")
def create_equipment_setup(
    request: Request,
    region_id: uuid.UUID = Form(...),
    name: str = Form(...),
    notes: str = Form(""),
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
):
    verify_csrf(request, csrf_token)
    _catalog_region(db, request.state.actor, region_id)
    row = EquipmentSetup(region_id=region_id, name=_name(name, 160), notes=notes.strip()[:500])
    with controlled_integrity(db, "An equipment setup in that region already uses that name."):
        db.add(row)
        db.flush()
        record_audit(db, "equipment_setup.created", "equipment_setup", row.id,
                     request.state.user.id, region_id=region_id, detail={"name": row.name})
        db.commit()
    return RedirectResponse(f"/manage/catalog#equipment-{row.id}", status_code=303)


@router.post("/equipment-setups/{setup_id}")
def update_equipment_setup(
    setup_id: uuid.UUID,
    request: Request,
    name: str = Form(...),
    notes: str = Form(""),
    lifecycle: str = Form(...),
    expected_version: int = Form(...),
    item_name: list[str] = Form(default=[]),
    item_quantity: list[int] = Form(default=[]),
    item_notes: list[str] = Form(default=[]),
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
):
    verify_csrf(request, csrf_token)
    row = db.scalar(select(EquipmentSetup).where(EquipmentSetup.id == setup_id).with_for_update())
    if not row:
        raise HTTPException(404)
    _catalog_region(db, request.state.actor, row.region_id)
    if row.lock_version != expected_version:
        raise HTTPException(409, "This equipment setup changed. Reload it before saving.")
    items = _equipment_rows(item_name, item_quantity, item_notes)
    row.name = _name(name, 160)
    row.notes = notes.strip()[:500]
    row.lifecycle = _lifecycle(lifecycle)
    row.lock_version += 1
    for old in db.scalars(select(EquipmentSetupItem).where(EquipmentSetupItem.setup_id == row.id)):
        db.delete(old)
    for order, (item_name_value, quantity, item_note) in enumerate(items):
        db.add(EquipmentSetupItem(setup_id=row.id, name=item_name_value, quantity=quantity,
                                  notes=item_note, display_order=order))
    record_audit(db, "equipment_setup.updated", "equipment_setup", row.id,
                 request.state.user.id, region_id=row.region_id,
                 detail={"name": row.name, "lifecycle": row.lifecycle, "item_count": len(items)})
    with controlled_integrity(db, "An equipment setup in that region already uses that name."):
        db.commit()
    return RedirectResponse(f"/manage/catalog#equipment-{row.id}", status_code=303)


@router.post("/equipment-setups/{setup_id}/duplicate")
def duplicate_equipment_setup(
    setup_id: uuid.UUID,
    request: Request,
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
):
    verify_csrf(request, csrf_token)
    source = db.get(EquipmentSetup, setup_id)
    if not source:
        raise HTTPException(404)
    _catalog_region(db, request.state.actor, source.region_id)
    duplicate = EquipmentSetup(region_id=source.region_id, name=f"{source.name} copy"[:160], notes=source.notes)
    with controlled_integrity(db, "Rename the existing copy before duplicating again."):
        db.add(duplicate)
        db.flush()
        for item in db.scalars(select(EquipmentSetupItem).where(EquipmentSetupItem.setup_id == source.id)):
            db.add(EquipmentSetupItem(setup_id=duplicate.id, item_key=item.item_key,
                                      name=item.name, quantity=item.quantity, notes=item.notes,
                                      display_order=item.display_order))
        record_audit(db, "equipment_setup.duplicated", "equipment_setup", duplicate.id,
                     request.state.user.id, region_id=source.region_id,
                     detail={"source_id": str(source.id), "name": duplicate.name})
        db.commit()
    return RedirectResponse(f"/manage/catalog#equipment-{duplicate.id}", status_code=303)


@router.post("/vehicles")
def create_vehicle(
    request: Request,
    name: str = Form(...),
    home_region_id: uuid.UUID = Form(...),
    category: str = Form("Operational"),
    model: str = Form(""),
    fuel_type: str = Form(""),
    is_truck: bool = Form(False),
    seat_capacity: int | None = Form(4),
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
):
    verify_csrf(request, csrf_token)
    _vehicle_region(db, request.state.actor, home_region_id)
    if seat_capacity is not None and seat_capacity < 1:
        raise HTTPException(400, "Vehicle capacity must be at least one person.")
    row = Vehicle(name=_name(name, 100), home_region_id=home_region_id,
                  category=_name(category, 80), model=model.strip()[:100] or None,
                  fuel_type=fuel_type.strip()[:40] or None, is_truck=is_truck,
                  seat_capacity=seat_capacity)
    with controlled_integrity(db, "A vehicle already uses that name."):
        db.add(row)
        db.flush()
        record_audit(db, "vehicle.created", "vehicle", row.id, request.state.user.id,
                     region_id=row.home_region_id,
                     detail={"name": row.name, "category": row.category, "model": row.model,
                             "is_truck": row.is_truck, "seat_capacity": row.seat_capacity})
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
    is_truck: bool = Form(False),
    seat_capacity: int | None = Form(None),
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
    if seat_capacity is not None and seat_capacity < 1:
        raise HTTPException(400, "Vehicle capacity must be at least one person.")
    previous = {"name": row.name, "home_region_id": str(row.home_region_id),
                "category": row.category, "model": row.model, "fuel_type": row.fuel_type,
                "is_truck": row.is_truck, "seat_capacity": row.seat_capacity,
                "lifecycle": row.lifecycle}
    row.name, row.home_region_id = _name(name, 100), home_region_id
    row.category, row.model = _name(category, 80), model.strip()[:100] or None
    row.fuel_type, row.lifecycle = fuel_type.strip()[:40] or None, _lifecycle(lifecycle)
    row.is_truck = is_truck
    row.seat_capacity = seat_capacity
    record_audit(db, "vehicle.updated", "vehicle", row.id, request.state.user.id,
                 region_id=row.home_region_id,
                 detail={"previous": previous, "name": row.name, "home_region_id": str(row.home_region_id),
                         "category": row.category, "model": row.model, "fuel_type": row.fuel_type,
                         "is_truck": row.is_truck, "seat_capacity": row.seat_capacity,
                         "lifecycle": row.lifecycle})
    with controlled_integrity(db, "A vehicle already uses that name."):
        db.commit()
    return RedirectResponse("/manage/catalog#vehicles", status_code=303)
