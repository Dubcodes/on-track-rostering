from __future__ import annotations

import json
import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit.models import AuditEvent
from app.audit.service import record_audit
from app.auth.policy import (
    can_manage_region,
    external_calendar_region_ids,
    require_admin,
)
from app.auth.security import verify_csrf
from app.catalog.models import Track
from app.catalog.presentation import track_token
from app.core.database import get_db
from app.external_calendar.importer import apply_bundle, parse_bundle, preview_bundle
from app.external_calendar.models import (
    ExternalCalendarEvent,
    ExternalEventObservation,
)
from app.external_calendar.refresh import ensure_provider_states, refresh_provider
from app.external_calendar.service import (
    adopt_external_event,
    confirm_track_mapping,
    event_evidence,
    normalized_key,
    suggested_track,
)
from app.rostering.models import Workday
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
        raise HTTPException(400, str(exc)) from exc
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


@router.get("/admin/online-sources", response_class=HTMLResponse)
def sources_page(request: Request, db: Session = Depends(get_db)):
    require_admin(request.state.actor)
    return _sources_response(request, db)


def _sources_response(request: Request, db: Session, *, refresh_result=None):
    states = ensure_provider_states(db)
    unmatched = list(
        db.scalars(
            select(ExternalEventObservation)
            .where(ExternalEventObservation.mapping_state == "UNMATCHED")
            .order_by(ExternalEventObservation.retrieved_at.desc())
        )
    )
    conflict_observations = list(
        db.scalars(
            select(ExternalEventObservation)
            .where(ExternalEventObservation.reconciliation_state == "CONFLICT")
            .order_by(ExternalEventObservation.retrieved_at.desc())
            .limit(100)
        )
    )
    conflicts = [
        {"observation": row, "event": db.get(ExternalCalendarEvent, row.event_id)}
        for row in conflict_observations
    ]
    tracks = list(db.scalars(select(Track).where(Track.lifecycle == "ACTIVE").order_by(Track.name)))
    suggestions = {
        str(row.id): (
            None
            if (row.parsed_facts or {}).get("venue_confidence") == "CLUB_ONLY"
            else suggested_track(db, row.source_track_name)
        )
        for row in unmatched
    }
    recent_refreshes = list(
        db.scalars(
            select(AuditEvent)
            .where(AuditEvent.action.in_(("external_provider.refreshed", "external_provider.refresh_failed")))
            .order_by(AuditEvent.occurred_at.desc())
            .limit(5)
        )
    )
    return templates.TemplateResponse(
        "online_sources.html",
        context(
            request,
            providers=PROVIDERS,
            provider_states=states,
            unmatched=unmatched,
            conflicts=conflicts,
            tracks=tracks,
            suggestions=suggestions,
            recent_refreshes=recent_refreshes,
            refresh_result=refresh_result,
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
    confirm_track_mapping(db, provider, external_track_name, track.id, request.state.user.id)
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
    return templates.TemplateResponse(
        "external_event.html",
        context(
            request,
            event=event,
            track=track,
            workday=workday,
            evidence=event_evidence(db, event),
            can_build=bool(track and can_manage_region(request.state.actor, track.region_id)),
            presentation=track_token(track.palette_slot if track else None),
        ),
    )


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
