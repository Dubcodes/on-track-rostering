from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.audit.service import record_audit
from app.auth.policy import can_administer_region
from app.auth.security import verify_csrf
from app.catalog.models import Track, TrackMap
from app.core.config import get_settings
from app.core.database import get_db
from app.external_calendar.http import SourceHTTPClient
from app.track_maps.service import (
    effective_map,
    map_path,
    refresh_automatic_map,
    reset_manual_map,
    save_manual_map,
)

router = APIRouter()


def _administered_track(db: Session, request: Request, track_id: uuid.UUID) -> Track:
    track = db.get(Track, track_id)
    if not track:
        raise HTTPException(404)
    if not can_administer_region(request.state.actor, track.region_id):
        raise HTTPException(403, "Regional administration authority required.")
    return track


@router.get("/track-maps/{track_id}")
def track_map_file(track_id: uuid.UUID, db: Session = Depends(get_db)):
    effective = effective_map(db.get(TrackMap, track_id))
    path = map_path(str(effective["file_name"])) if effective else None
    if not path:
        raise HTTPException(404)
    return FileResponse(
        path,
        media_type=str(effective["content_type"]),
        headers={"Cache-Control": "private, max-age=3600"},
    )


@router.post("/manage/catalog/tracks/{track_id}/map/upload")
def upload_track_map(
    track_id: uuid.UUID,
    request: Request,
    image: UploadFile = File(...),
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
):
    track = _administered_track(db, request, track_id)
    verify_csrf(request, csrf_token)
    content = image.file.read(15 * 1024 * 1024 + 1)
    try:
        row = save_manual_map(db, track, content)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    record_audit(
        db,
        "track_map.manual_uploaded",
        "track",
        track.id,
        request.state.user.id,
        region_id=track.region_id,
        detail={"width": row.manual_width, "height": row.manual_height, "bytes": row.manual_bytes},
    )
    db.commit()
    return RedirectResponse("/manage/catalog#tracks", status_code=303)


@router.post("/manage/catalog/tracks/{track_id}/map/reset")
def reset_track_map(
    track_id: uuid.UUID,
    request: Request,
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
):
    track = _administered_track(db, request, track_id)
    verify_csrf(request, csrf_token)
    row = db.get(TrackMap, track.id)
    if row and row.has_manual_override:
        previous = reset_manual_map(db, row)
        record_audit(
            db, "track_map.manual_reset", "track", track.id, request.state.user.id,
            region_id=track.region_id,
        )
        db.commit()
        if previous:
            previous.unlink(missing_ok=True)
    return RedirectResponse("/manage/catalog#tracks", status_code=303)


@router.post("/manage/catalog/tracks/{track_id}/map/refresh")
def refresh_track_map(
    track_id: uuid.UUID,
    request: Request,
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
):
    track = _administered_track(db, request, track_id)
    verify_csrf(request, csrf_token)
    settings = get_settings()
    row = refresh_automatic_map(
        db,
        track,
        SourceHTTPClient(
            timeout=settings.racing_source_timeout_seconds,
            max_bytes=settings.racing_source_max_bytes,
        ),
    )
    record_audit(
        db,
        "track_map.automatic_refreshed",
        "track",
        track.id,
        request.state.user.id,
        region_id=track.region_id,
        detail={
            "status": row.automatic_status,
            "width": row.automatic_width,
            "height": row.automatic_height,
        },
    )
    db.commit()
    return RedirectResponse("/manage/catalog#tracks", status_code=303)
