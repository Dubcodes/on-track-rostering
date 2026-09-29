from __future__ import annotations

import uuid
from datetime import date
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.policy import can_administer_person, can_administer_region
from app.auth.security import verify_csrf
from app.catalog.models import Region
from app.core.database import get_db
from app.core.time import local_today
from app.identity.models import Person
from app.unavailability.models import PersonUnavailability
from app.unavailability.service import (
    cancel_unavailability,
    create_unavailability,
    roster_conflicts,
)
from app.web import context, templates

router = APIRouter(prefix="/manage/leave")


def _authorized_regions(db: Session, request: Request) -> list[Region]:
    regions = list(
        db.scalars(
            select(Region).where(Region.lifecycle == "ACTIVE").order_by(Region.name)
        )
    )
    return [
        region
        for region in regions
        if can_administer_region(request.state.actor, region.id)
    ]


def _require_person_authority(request: Request, person: Person) -> None:
    if person.lifecycle != "ACTIVE":
        raise HTTPException(409, "Leave can only be managed for active people.")
    if request.state.actor.is_admin:
        return
    if person.home_region_id is None or not can_administer_person(
        request.state.actor, person, person.home_region_id
    ):
        raise HTTPException(403, "You cannot manage leave for this person.")


@router.get("", response_class=HTMLResponse)
def leave_page(
    request: Request,
    region_id: uuid.UUID | None = Query(None),
    db: Session = Depends(get_db),
):
    regions = _authorized_regions(db, request)
    if not regions:
        raise HTTPException(403, "Regional administration authority required.")
    selected = next((region for region in regions if region.id == region_id), None)
    if region_id and selected is None:
        raise HTTPException(403, "You cannot manage leave for that Region.")
    selected = selected or regions[0]
    people = list(
        db.scalars(
            select(Person)
            .where(
                Person.home_region_id == selected.id,
                Person.lifecycle == "ACTIVE",
            )
            .order_by(Person.display_name, Person.id)
        )
    )
    person_ids = [person.id for person in people]
    records = list(
        db.scalars(
            select(PersonUnavailability)
            .where(PersonUnavailability.person_id.in_(person_ids))
            .order_by(PersonUnavailability.start_date, PersonUnavailability.created_at)
        )
    ) if person_ids else []
    people_by_id = {person.id: person for person in people}
    today = local_today()
    current = [
        row for row in records if row.cancelled_at is None and row.end_date >= today
    ]
    history = [row for row in records if row not in current]
    conflicts = {
        row.id: roster_conflicts(
            db,
            person_id=row.person_id,
            start_date=row.start_date,
            end_date=row.end_date,
        )
        for row in records
    }
    return templates.TemplateResponse(
        "leave.html",
        context(
            request,
            regions=regions,
            selected_region=selected,
            people=people,
            people_by_id=people_by_id,
            current_records=current,
            history_records=history,
            conflicts_by_record=conflicts,
            leave_error=request.query_params.get("error"),
        ),
    )


@router.post("")
def add_leave(
    request: Request,
    person_id: uuid.UUID = Form(...),
    start_date: date = Form(...),
    end_date: date = Form(...),
    note: str = Form(""),
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
):
    verify_csrf(request, csrf_token)
    person = db.get(Person, person_id)
    if not person:
        raise HTTPException(404)
    _require_person_authority(request, person)
    try:
        create_unavailability(
            db,
            person=person,
            start_date=start_date,
            end_date=end_date,
            note=note,
            actor_user_id=request.state.user.id,
        )
    except ValueError as exc:
        db.rollback()
        region = person.home_region_id or ""
        return RedirectResponse(
            f"/manage/leave?region_id={region}&error={quote(str(exc))}", status_code=303
        )
    return RedirectResponse(
        f"/manage/leave?region_id={person.home_region_id}&created=1", status_code=303
    )


@router.post("/{record_id}/cancel")
def cancel_leave(
    record_id: uuid.UUID,
    request: Request,
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
):
    verify_csrf(request, csrf_token)
    row = db.get(PersonUnavailability, record_id)
    person = db.get(Person, row.person_id) if row else None
    if not row or not person:
        raise HTTPException(404)
    _require_person_authority(request, person)
    try:
        cancel_unavailability(
            db, row=row, person=person, actor_user_id=request.state.user.id
        )
    except ValueError as exc:
        db.rollback()
        raise HTTPException(409, str(exc)) from exc
    return RedirectResponse(
        f"/manage/leave?region_id={person.home_region_id}&cancelled=1", status_code=303
    )
