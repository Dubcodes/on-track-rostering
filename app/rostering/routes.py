from __future__ import annotations

import uuid
from datetime import date, time

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.policy import can_manage_region, require_manage_region
from app.auth.security import verify_csrf
from app.catalog.models import BasePosition, Region, Track
from app.core.database import get_db
from app.core.enums import AssignmentStatus, WorkdayCategory
from app.identity.models import Person
from app.rostering.models import Assignment, OpenPositionApplication, Workday, WorkdayRevision
from app.rostering.service import (
    AssignmentInput,
    PublishConflict,
    add_assignment,
    create_workday,
    ensure_draft,
    preview_diff,
    publish,
    update_assignment,
    update_draft_details,
)
from app.web import context, templates

router = APIRouter(prefix="/manage")


def _parse_time(value: str) -> time | None:
    return time.fromisoformat(value) if value.strip() else None


def _editable_regions(db: Session, request: Request) -> list[Region]:
    regions = list(db.scalars(select(Region).where(Region.lifecycle == "ACTIVE").order_by(Region.name)))
    return [region for region in regions if can_manage_region(request.state.actor, region.id)]


@router.get("/workdays/new", response_class=HTMLResponse)
def new_workday_page(request: Request, db: Session = Depends(get_db)):
    regions = _editable_regions(db, request)
    if not regions:
        raise HTTPException(403, "Regional roster authority required")
    tracks = list(db.scalars(select(Track).where(Track.lifecycle == "ACTIVE").order_by(Track.name)))
    return templates.TemplateResponse(
        "workday_new.html",
        context(request, regions=regions, tracks=tracks, categories=[item.value for item in WorkdayCategory]),
    )


@router.post("/workdays")
def new_workday(
    request: Request,
    region_id: uuid.UUID = Form(...),
    category: str = Form(...),
    work_date: date = Form(...),
    track_id: str = Form(""),
    title: str = Form(""),
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
):
    verify_csrf(request, csrf_token)
    require_manage_region(request.state.actor, region_id)
    if category not in {item.value for item in WorkdayCategory}:
        raise HTTPException(400, "Invalid workday category")
    try:
        workday = create_workday(
            db,
            region_id=region_id,
            category=category,
            work_date=work_date,
            track_id=uuid.UUID(track_id) if track_id else None,
            title=title,
            actor_user_id=request.state.user.id,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return RedirectResponse(f"/manage/workdays/{workday.id}", status_code=303)


def _builder_context(
    db: Session, request: Request, workday: Workday, draft: WorkdayRevision, **extra: object
):
    require_manage_region(request.state.actor, workday.region_id)
    application_rows = db.execute(
        select(OpenPositionApplication, Person)
        .join(Person, Person.id == OpenPositionApplication.person_id)
        .where(
            OpenPositionApplication.revision_id == workday.current_published_revision_id,
            OpenPositionApplication.status.in_(["APPLIED", "SELECTED"]),
        )
        .order_by(OpenPositionApplication.created_at)
    ).all() if workday.current_published_revision_id else []
    applications_by_slot: dict[uuid.UUID, list[tuple[OpenPositionApplication, Person]]] = {}
    for application, person in application_rows:
        applications_by_slot.setdefault(application.slot_key, []).append((application, person))
    return context(
        request,
        workday=workday,
        draft=draft,
        tracks=list(
            db.scalars(
                select(Track)
                .where(Track.region_id == workday.region_id, Track.lifecycle == "ACTIVE")
                .order_by(Track.name)
            )
        ),
        positions=list(
            db.scalars(
                select(BasePosition).where(BasePosition.lifecycle == "ACTIVE").order_by(BasePosition.name)
            )
        ),
        people=list(
            db.scalars(select(Person).where(Person.lifecycle == "ACTIVE").order_by(Person.display_name))
        ),
        assignments=list(
            db.scalars(
                select(Assignment)
                .where(Assignment.revision_id == draft.id)
                .order_by(Assignment.display_name_snapshot)
            )
        ),
        statuses=[item.value for item in AssignmentStatus],
        applications_by_slot=applications_by_slot,
        **extra,
    )


@router.get("/workdays/{workday_id}", response_class=HTMLResponse)
def edit_workday(workday_id: uuid.UUID, request: Request, db: Session = Depends(get_db)):
    workday = db.get(Workday, workday_id)
    if not workday:
        raise HTTPException(404)
    require_manage_region(request.state.actor, workday.region_id)
    draft = ensure_draft(db, workday, request.state.user.id)
    return templates.TemplateResponse("workday_builder.html", _builder_context(db, request, workday, draft))


@router.post("/workdays/{workday_id}/details")
def save_details(
    workday_id: uuid.UUID,
    request: Request,
    work_date: date = Form(...),
    track_id: str = Form(""),
    title: str = Form(...),
    start_time: str = Form(""),
    end_time: str = Form(""),
    on_track_time: str = Form(""),
    first_race_time: str = Form(""),
    last_race_time: str = Form(""),
    race_count: str = Form(""),
    day_note: str = Form(""),
    change_reason: str = Form(""),
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
):
    verify_csrf(request, csrf_token)
    workday = db.get(Workday, workday_id)
    if not workday:
        raise HTTPException(404)
    require_manage_region(request.state.actor, workday.region_id)
    draft = db.get(WorkdayRevision, workday.current_draft_revision_id)
    if not draft:
        raise HTTPException(409, "Open the editor again to create a draft")
    count = int(race_count) if race_count else None
    if count is not None and not 0 <= count <= 99:
        raise HTTPException(400, "Race count must be between 0 and 99")
    try:
        update_draft_details(
            db,
            draft,
            work_date=work_date,
            track_id=uuid.UUID(track_id) if track_id else None,
            title=title,
            start_time=_parse_time(start_time),
            end_time=_parse_time(end_time),
            on_track_time=_parse_time(on_track_time),
            first_race_time=_parse_time(first_race_time),
            last_race_time=_parse_time(last_race_time),
            race_count=count,
            day_note=day_note,
            change_reason=change_reason,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return RedirectResponse(f"/manage/workdays/{workday_id}", status_code=303)


@router.post("/workdays/{workday_id}/assignments")
def create_assignment(
    workday_id: uuid.UUID,
    request: Request,
    base_position_id: str = Form(""),
    slot_index: str = Form(""),
    person_id: str = Form(""),
    status: str = Form(AssignmentStatus.TBC.value),
    note: str = Form(""),
    note_private: bool = Form(False),
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
):
    verify_csrf(request, csrf_token)
    workday = db.get(Workday, workday_id)
    if not workday:
        raise HTTPException(404)
    require_manage_region(request.state.actor, workday.region_id)
    draft = db.get(WorkdayRevision, workday.current_draft_revision_id)
    if not draft:
        raise HTTPException(409)
    try:
        add_assignment(
            db,
            draft,
            AssignmentInput(
                base_position_id=uuid.UUID(base_position_id) if base_position_id else None,
                slot_index=int(slot_index) if slot_index else None,
                person_id=uuid.UUID(person_id) if person_id else None,
                status=status,
                note=note,
                note_private=note_private,
            ),
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return RedirectResponse(f"/manage/workdays/{workday_id}#assignments", status_code=303)


@router.post("/workdays/{workday_id}/assignments/{assignment_id}")
def change_assignment(
    workday_id: uuid.UUID,
    assignment_id: uuid.UUID,
    request: Request,
    person_id: str = Form(""),
    status: str = Form(AssignmentStatus.TBC.value),
    note: str = Form(""),
    note_private: bool = Form(False),
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
):
    verify_csrf(request, csrf_token)
    workday = db.get(Workday, workday_id)
    if not workday:
        raise HTTPException(404)
    require_manage_region(request.state.actor, workday.region_id)
    draft = db.get(WorkdayRevision, workday.current_draft_revision_id)
    if not draft:
        raise HTTPException(409)
    try:
        update_assignment(
            db,
            draft,
            assignment_id,
            person_id=uuid.UUID(person_id) if person_id else None,
            status=status,
            note=note,
            note_private=note_private,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return RedirectResponse(f"/manage/workdays/{workday_id}#assignments", status_code=303)


@router.get("/workdays/{workday_id}/preview", response_class=HTMLResponse)
def preview(workday_id: uuid.UUID, request: Request, db: Session = Depends(get_db)):
    workday = db.get(Workday, workday_id)
    if not workday:
        raise HTTPException(404)
    require_manage_region(request.state.actor, workday.region_id)
    draft = db.get(WorkdayRevision, workday.current_draft_revision_id)
    if not draft:
        raise HTTPException(409)
    return templates.TemplateResponse(
        "workday_preview.html",
        _builder_context(db, request, workday, draft, changes=preview_diff(db, workday, draft)),
    )


@router.post("/workdays/{workday_id}/publish")
def publish_workday(
    workday_id: uuid.UUID,
    request: Request,
    draft_id: uuid.UUID = Form(...),
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
):
    verify_csrf(request, csrf_token)
    workday = db.get(Workday, workday_id)
    if not workday:
        raise HTTPException(404)
    require_manage_region(request.state.actor, workday.region_id)
    db.commit()
    try:
        publish(db, workday_id, draft_id, request.state.user.id)
    except PublishConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    return RedirectResponse(f"/day/{workday_id}", status_code=303)
