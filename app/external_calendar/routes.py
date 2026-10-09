from __future__ import annotations

import json
import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import exists, select
from sqlalchemy.orm import Session

from app.audit.models import AuditEvent
from app.audit.service import record_audit
from app.auth.policy import (
    can_administer_region,
    can_manage_region,
    external_calendar_region_ids,
    require_admin,
)
from app.auth.security import verify_csrf
from app.catalog.models import Region, Track
from app.catalog.presentation import track_token
from app.catalog.service import allocate_palette_slot, normalized_track_match
from app.core.database import get_db
from app.core.enums import Role
from app.core.time import local_today
from app.external_calendar.detail_refresh import refresh_love_racing_programme
from app.external_calendar.importer import apply_bundle, parse_bundle, preview_bundle
from app.external_calendar.inventory import (
    confirmed_mapping_rows,
    inventory_template,
    source_inventory,
    structural_master_data_bundle,
)
from app.external_calendar.models import (
    ExternalCalendarEvent,
    ExternalEventObservation,
    ExternalTrackMapping,
)
from app.external_calendar.refresh import ensure_provider_states, refresh_provider
from app.external_calendar.service import (
    adopt_external_event,
    confirm_track_mapping,
    event_evidence,
    normalized_key,
)
from app.rostering.models import Workday
from app.transition_import.service import (
    apply_capture,
    parse_capture,
    preview_capture,
    validate_payload,
)
from app.web import context, templates

router = APIRouter()
PROVIDERS = (("LOVE_RACING", "Love Racing"), ("HRNZ", "Harness Racing NZ"), ("API", "Future/API"))


def _bundle_text(pasted_json: str, upload: UploadFile | None) -> str:
    if pasted_json.strip():
        return pasted_json
    if upload and upload.filename:
        if not upload.filename.lower().endswith(".json"):
            raise HTTPException(400, "Upload a .json file.")
        data = upload.file.read(2_000_001)
        if len(data) > 2_000_000:
            raise HTTPException(413, "Import bundle is too large.")
        return data.decode("utf-8")
    raise HTTPException(400, "Paste JSON or upload a JSON file.")


def _transition_text(pasted_capture: str, upload: UploadFile | None) -> str:
    if pasted_capture.strip():
        raw = pasted_capture
    elif upload and upload.filename:
        if not upload.filename.lower().endswith(".txt"):
            raise ValueError("Upload a .txt Deputy transition capture.")
        data = upload.file.read(5_000_001)
        if len(data) > 5_000_000:
            raise ValueError("Deputy transition capture is larger than 5 MB.")
        try:
            raw = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("Deputy transition capture must be UTF-8 text.") from exc
    else:
        raise ValueError("Paste or upload a Deputy transition capture.")
    if len(raw.encode("utf-8")) > 5_000_000:
        raise ValueError("Deputy transition capture is larger than 5 MB.")
    return raw


@router.get("/admin/data-import", response_class=HTMLResponse)
def import_page(request: Request):
    require_admin(request.state.actor)
    return templates.TemplateResponse("data_import.html", context(request))


@router.post("/admin/data-import/preview", response_class=HTMLResponse)
def import_preview(
    request: Request,
    pasted_json: str = Form(""),
    upload: UploadFile | None = File(None),
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
):
    require_admin(request.state.actor)
    verify_csrf(request, csrf_token)
    raw = _bundle_text(pasted_json, upload)
    try:
        bundle = parse_bundle(raw)
    except ValueError as exc:
        return templates.TemplateResponse(
            "data_import.html", context(request, import_error=str(exc)), status_code=400
        )
    plan = preview_bundle(db, bundle)
    return templates.TemplateResponse(
        "data_import.html",
        context(request, import_plan=plan, import_json=json.dumps(bundle, separators=(",", ":"))),
    )


@router.post("/admin/data-import/apply")
def import_apply(
    request: Request, import_json: str = Form(...), csrf_token: str = Form(...), db: Session = Depends(get_db)
):
    require_admin(request.state.actor)
    verify_csrf(request, csrf_token)
    try:
        bundle = parse_bundle(import_json)
        plan = apply_bundle(db, bundle, request.state.user.id)
        record_audit(
            db,
            "data_import.applied",
            "import_bundle",
            None,
            request.state.user.id,
            detail={"version": bundle.get("version"), "counts": plan.counts},
        )
        db.commit()
    except ValueError as exc:
        db.rollback()
        raise HTTPException(409, str(exc)) from exc
    return RedirectResponse("/admin/data-import?imported=1", status_code=303)


@router.post("/admin/data-import/deputy-preview", response_class=HTMLResponse)
def deputy_import_preview(
    request: Request,
    pasted_capture: str = Form(""),
    transition_upload: UploadFile | None = File(None),
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
):
    require_admin(request.state.actor)
    verify_csrf(request, csrf_token)
    try:
        payload = parse_capture(_transition_text(pasted_capture, transition_upload))
        plan = preview_capture(db, payload)
    except ValueError as exc:
        return templates.TemplateResponse(
            "data_import.html", context(request, transition_error=str(exc)), status_code=400
        )
    return templates.TemplateResponse(
        "data_import.html",
        context(
            request,
            transition_plan=plan,
            transition_json=json.dumps(payload, separators=(",", ":")),
        ),
    )


@router.post("/admin/data-import/deputy-apply")
def deputy_import_apply(
    request: Request,
    transition_json: str = Form(...),
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
):
    require_admin(request.state.actor)
    verify_csrf(request, csrf_token)
    try:
        payload = validate_payload(json.loads(transition_json))
        counts = apply_capture(db, payload, request.state.user.id)
        record_audit(
            db,
            "transition_import.applied",
            "transition_capture",
            None,
            request.state.user.id,
            detail={"source": payload["source"], "counts": counts},
        )
        db.commit()
    except (json.JSONDecodeError, ValueError) as exc:
        db.rollback()
        return templates.TemplateResponse(
            "data_import.html", context(request, transition_error=str(exc)), status_code=409
        )
    return RedirectResponse("/admin/data-import?transition_imported=1", status_code=303)


@router.get("/admin/online-sources", response_class=HTMLResponse)
def sources_page(request: Request, db: Session = Depends(get_db)):
    actor = request.state.actor
    if not actor.is_admin and not any(
        Role.MANAGER.value in roles for roles in actor.regional_roles.values()
    ):
        raise HTTPException(403, "Manager access required")
    return _sources_response(request, db)


def _sources_response(request: Request, db: Session, *, refresh_result=None):
    actor = request.state.actor
    region_ids = None if actor.is_admin else {
        region_id
        for region_id, roles in actor.regional_roles.items()
        if Role.MANAGER.value in roles
    }
    states = ensure_provider_states(db)
    unmatched = source_inventory(db, unmatched_only=True) if actor.is_admin else []
    observation_query = select(ExternalEventObservation)
    mapping_query = select(ExternalTrackMapping)
    if region_ids is not None:
        observation_query = (
            observation_query
            .join(ExternalCalendarEvent, ExternalEventObservation.event_id == ExternalCalendarEvent.id)
            .join(Track, ExternalCalendarEvent.track_id == Track.id)
            .where(Track.region_id.in_(region_ids))
        )
        mapping_query = mapping_query.join(Track).where(Track.region_id.in_(region_ids))
    observations = list(db.scalars(observation_query))
    mappings = list(db.scalars(mapping_query))
    provider_metrics: dict[str, dict[str, int]] = {}
    for provider, _label in PROVIDERS:
        provider_observations = [row for row in observations if row.provider == provider]
        unmatched_observations = [
            row for row in provider_observations if row.mapping_state == "UNMATCHED"
        ]
        provider_metrics[provider] = {
            "mapped_identities": sum(1 for row in mappings if row.provider == provider),
            "unmapped_identities": len(
                {normalized_key(row.source_track_name) for row in unmatched_observations}
            ),
            "unmapped_observations": len(unmatched_observations),
            "conflicts": sum(
                1
                for row in provider_observations
                if row.reconciliation_state == "CONFLICT"
            ),
        }
    today = local_today()
    programme_identity_exists = exists(
        select(1).where(
            ExternalEventObservation.event_id == ExternalCalendarEvent.id,
            ExternalEventObservation.provider == "LOVE_RACING",
            ExternalEventObservation.provider_event_id.is_not(None),
            ExternalEventObservation.mapping_state == "MAPPED",
        )
    )
    programme_query = (
        select(ExternalCalendarEvent)
        .where(
            programme_identity_exists,
            ExternalCalendarEvent.event_kind == "RACE",
            ExternalCalendarEvent.event_date >= today,
        )
    )
    if region_ids is not None:
        programme_query = programme_query.join(Track).where(Track.region_id.in_(region_ids))
    programme_events = list(db.scalars(programme_query))
    provider_metrics["LOVE_RACING"].update(
        {
            "programme_complete": sum(event.programme_status == "COMPLETE" for event in programme_events),
            "programme_partial": sum(
                event.programme_status in {"PARTIAL", "AWAITING_SCHEDULE", "DISCOVERED"}
                for event in programme_events
            ),
            "programme_failed": sum(event.detail_failure_count > 0 for event in programme_events),
            "programme_never_checked": sum(event.detail_checked_at is None for event in programme_events),
            "programme_near_term_failed": sum(
                event.detail_failure_count > 0 and (event.event_date - today).days <= 3
                for event in programme_events
            ),
        }
    )
    conflict_observations = [
        row for row in observations if row.reconciliation_state == "CONFLICT"
    ][:100]
    conflicts = [
        {"observation": row, "event": db.get(ExternalCalendarEvent, row.event_id)}
        for row in conflict_observations
    ]
    track_query = (
        select(Track)
            .join(Region, Region.id == Track.region_id)
            .where(Track.lifecycle == "ACTIVE", Region.lifecycle == "ACTIVE")
            .order_by(Track.name)
    )
    if region_ids is not None:
        track_query = track_query.where(Track.region_id.in_(region_ids))
    tracks = list(db.scalars(track_query))
    recent_refreshes = list(
        db.scalars(
            select(AuditEvent)
            .where(AuditEvent.action.in_(("external_provider.refreshed", "external_provider.refresh_failed")))
            .order_by(AuditEvent.occurred_at.desc())
            .limit(5)
        )
    ) if actor.is_admin else []
    confirmed_mappings = confirmed_mapping_rows(db)
    if region_ids is not None:
        confirmed_mappings = [
            row for row in confirmed_mappings if row["region"].id in region_ids
        ]
    region_query = select(Region).where(Region.lifecycle == "ACTIVE").order_by(Region.name)
    if region_ids is not None:
        region_query = region_query.where(Region.id.in_(region_ids))
    return templates.TemplateResponse(
        "online_sources.html",
        context(
            request,
            providers=PROVIDERS,
            provider_states=states,
            provider_metrics=provider_metrics,
            unmatched=unmatched,
            conflicts=conflicts,
            tracks=tracks,
            regions=list(db.scalars(region_query)),
            confirmed_mappings=confirmed_mappings,
            recent_refreshes=recent_refreshes,
            refresh_result=refresh_result,
            sources_admin=actor.is_admin,
        ),
    )


@router.post("/admin/online-sources/{provider}/toggle", response_class=HTMLResponse)
def toggle_provider(
    provider: str,
    request: Request,
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
):
    require_admin(request.state.actor)
    verify_csrf(request, csrf_token)
    provider = provider.upper()
    if provider not in {"LOVE_RACING", "HRNZ"}:
        raise HTTPException(404, "Provider is not configurable.")
    state = ensure_provider_states(db)[provider]
    state.enabled = not state.enabled
    state.explicitly_configured = True
    state.status = "READY" if state.enabled else "DISABLED"
    record_audit(
        db,
        "external_provider.toggled",
        "external_provider",
        provider,
        request.state.user.id,
        detail={"enabled": state.enabled},
    )
    db.commit()
    return RedirectResponse("/admin/online-sources", status_code=303)


@router.post("/admin/online-sources/{provider}/refresh", response_class=HTMLResponse)
def refresh_source(
    provider: str,
    request: Request,
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
):
    require_admin(request.state.actor)
    verify_csrf(request, csrf_token)
    provider = provider.upper()
    if provider not in {"LOVE_RACING", "HRNZ"}:
        raise HTTPException(404, "Provider is not configured.")
    try:
        result = refresh_provider(db, provider, actor_user_id=request.state.user.id)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return _sources_response(request, db, refresh_result=result)


@router.post("/admin/online-sources/map")
def map_track(
    request: Request,
    provider: str = Form(...),
    external_track_name: str = Form(...),
    track_id: uuid.UUID = Form(...),
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
):
    require_admin(request.state.actor)
    verify_csrf(request, csrf_token)
    track = db.get(Track, track_id)
    if not track or track.lifecycle != "ACTIVE":
        raise HTTPException(400, "Select an active track.")
    try:
        confirm_track_mapping(db, provider, external_track_name, track.id, request.state.user.id)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    record_audit(
        db,
        "external_track.mapped",
        "track",
        track.id,
        request.state.user.id,
        detail={"provider": provider, "external_track_key": normalized_key(external_track_name)},
    )
    db.commit()
    return RedirectResponse("/admin/online-sources?mapped=1", status_code=303)


@router.post("/admin/online-sources/create-track-map")
def create_track_and_map(
    request: Request,
    provider: str = Form(...),
    external_track_name: str = Form(...),
    region_id: uuid.UUID = Form(...),
    track_name: str = Form(...),
    map_reference: str = Form(""),
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
):
    require_admin(request.state.actor)
    verify_csrf(request, csrf_token)
    provider = provider.strip().upper()
    inventory_item = next(
        (
            item
            for item in source_inventory(db, provider=provider, unmatched_only=True)
            if item.source_key == normalized_key(external_track_name)
        ),
        None,
    )
    if inventory_item is None:
        raise HTTPException(404, "Unmatched source identity not found.")
    if inventory_item.club_only:
        raise HTTPException(409, "Create Track & Map is unavailable for a club-only source identity.")
    region = db.get(Region, region_id)
    if not region or region.lifecycle != "ACTIVE":
        raise HTTPException(400, "Select an active Region.")
    clean_name = " ".join(track_name.strip().split())
    if not 2 <= len(clean_name) <= 120:
        raise HTTPException(400, "Track name must be between 2 and 120 characters.")
    duplicate = normalized_track_match(db, region.id, clean_name)
    if duplicate:
        raise HTTPException(
            409,
            f"{duplicate.name} already exists in {region.name}; map to the existing Track instead.",
        )
    try:
        track = Track(
            region_id=region.id,
            name=clean_name,
            palette_slot=allocate_palette_slot(db, region.id),
            map_reference=map_reference.strip()[:500] or None,
        )
        db.add(track)
        db.flush()
        confirm_track_mapping(db, provider, external_track_name, track.id, request.state.user.id)
        record_audit(
            db,
            "track.created",
            "track",
            track.id,
            request.state.user.id,
            region_id=region.id,
            detail={"source_setup": True},
        )
        record_audit(
            db,
            "external_track.mapped",
            "track",
            track.id,
            request.state.user.id,
            detail={"provider": provider, "external_track_key": inventory_item.source_key},
        )
        db.commit()
    except ValueError as exc:
        db.rollback()
        raise HTTPException(409, str(exc)) from exc
    return RedirectResponse("/admin/online-sources?created_mapped=1", status_code=303)


@router.get("/admin/online-sources/source-inventory.json")
def download_source_inventory(request: Request, db: Session = Depends(get_db)):
    require_admin(request.state.actor)
    return JSONResponse(
        inventory_template(db),
        headers={"Content-Disposition": 'attachment; filename="ontrack-source-mapping-template.json"'},
    )


@router.get("/admin/structural-master-data.json")
def download_structural_master_data(request: Request, db: Session = Depends(get_db)):
    require_admin(request.state.actor)
    return JSONResponse(
        structural_master_data_bundle(db),
        headers={"Content-Disposition": 'attachment; filename="ontrack-structural-master-data.json"'},
    )


@router.get("/external-events/{event_id}", response_class=HTMLResponse)
def event_detail(event_id: uuid.UUID, request: Request, db: Session = Depends(get_db)):
    event = db.get(ExternalCalendarEvent, event_id)
    if not event:
        raise HTTPException(404)
    track = db.get(Track, event.track_id) if event.track_id else None
    visible_regions = external_calendar_region_ids(db, request.state.actor)
    if track is None or (visible_regions is not None and track.region_id not in visible_regions):
        raise HTTPException(404, "External event not found")
    workday = db.scalar(select(Workday).where(Workday.external_event_id == event.id))
    has_programme_identity = bool(
        db.scalar(
            select(ExternalEventObservation.id).where(
                ExternalEventObservation.event_id == event.id,
                ExternalEventObservation.provider == "LOVE_RACING",
                ExternalEventObservation.provider_event_id.is_not(None),
                ExternalEventObservation.mapping_state == "MAPPED",
            ).limit(1)
        )
    )
    return templates.TemplateResponse(
        "external_event.html",
        context(
            request,
            event=event,
            track=track,
            workday=workday,
            evidence=event_evidence(db, event),
            can_build=bool(track and can_manage_region(request.state.actor, track.region_id)),
            can_refresh_programme=bool(
                track
                and event.event_kind == "RACE"
                and has_programme_identity
                and can_administer_region(request.state.actor, track.region_id)
            ),
            programme_refresh=request.query_params.get("programme_refresh"),
            presentation=track_token(track.palette_slot if track else None),
        ),
    )


@router.post("/external-events/{event_id}/refresh-programme")
def refresh_event_programme(
    event_id: uuid.UUID, request: Request, csrf_token: str = Form(...), db: Session = Depends(get_db)
):
    verify_csrf(request, csrf_token)
    event = db.get(ExternalCalendarEvent, event_id)
    track = db.get(Track, event.track_id) if event and event.track_id else None
    if not event or not track:
        raise HTTPException(404, "External event not found")
    if event.event_kind != "RACE":
        raise HTTPException(409, "Programme refresh is available only for race events.")
    if not can_administer_region(request.state.actor, track.region_id):
        raise HTTPException(403, "Manager access for this Track's Region is required")
    try:
        outcome = refresh_love_racing_programme(db, event)
        record_audit(
            db,
            "external_programme.refreshed",
            "external_calendar_event",
            event.id,
            request.state.user.id,
            region_id=track.region_id,
            detail={"provider": "LOVE_RACING", "outcome": outcome},
        )
        db.commit()
    except ValueError as exc:
        db.rollback()
        raise HTTPException(409, str(exc)) from exc
    state = "failed" if outcome == "ERROR" else "refreshed"
    return RedirectResponse(f"/external-events/{event.id}?programme_refresh={state}", status_code=303)


@router.post("/external-events/{event_id}/build")
def build_event(
    event_id: uuid.UUID, request: Request, csrf_token: str = Form(...), db: Session = Depends(get_db)
):
    verify_csrf(request, csrf_token)
    try:
        workday, _created = adopt_external_event(db, event_id, request.state.actor)
        db.commit()
    except ValueError as exc:
        db.rollback()
        raise HTTPException(409, str(exc)) from exc
    return RedirectResponse(f"/manage/workdays/{workday.id}", status_code=303)
