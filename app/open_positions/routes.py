from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.policy import require_manage_region
from app.auth.security import verify_csrf
from app.core.database import get_db
from app.open_positions.service import apply_for_position, available_positions, select_application
from app.rostering.models import OpenPositionApplication, Workday
from app.rostering.service import ensure_draft
from app.web import context, templates

router = APIRouter()


@router.get("/open-positions", response_class=HTMLResponse)
def open_positions_page(request: Request, db: Session = Depends(get_db)):
    positions = available_positions(db, request.state.actor)
    applied = {
        (row.revision_id, row.slot_key): row.status
        for row in db.scalars(
            select(OpenPositionApplication).where(
                OpenPositionApplication.person_id == request.state.actor.person_id
            )
        )
    } if request.state.actor.person_id else {}
    return templates.TemplateResponse(
        "open_positions.html", context(request, positions=positions, applied=applied)
    )


@router.post("/open-positions/{workday_id}/{slot_key}/apply")
def apply(
    workday_id: uuid.UUID,
    slot_key: uuid.UUID,
    request: Request,
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
):
    verify_csrf(request, csrf_token)
    try:
        apply_for_position(db, actor=request.state.actor, workday_id=workday_id, slot_key=slot_key)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return RedirectResponse("/open-positions?applied=1", status_code=303)


@router.post("/manage/workdays/{workday_id}/applications/{application_id}/select")
def choose_applicant(
    workday_id: uuid.UUID,
    application_id: uuid.UUID,
    request: Request,
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
):
    verify_csrf(request, csrf_token)
    workday = db.get(Workday, workday_id)
    application = db.get(OpenPositionApplication, application_id)
    if not workday or not application:
        raise HTTPException(404)
    require_manage_region(request.state.actor, workday.region_id)
    if application.revision_id != workday.current_published_revision_id:
        raise HTTPException(409, "The application belongs to an older publication.")
    draft = ensure_draft(db, workday, request.state.user.id)
    try:
        select_application(db, workday=workday, draft=draft, application=application)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return RedirectResponse(f"/manage/workdays/{workday.id}#assignments", status_code=303)
