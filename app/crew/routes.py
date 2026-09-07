from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.audit.service import record_audit
from app.auth.policy import can_manage_region, require_manage_region
from app.auth.security import verify_csrf
from app.auth.service import validated_email
from app.catalog.models import BasePosition, CrewGroup, PersonCrewGroup, Region
from app.core.database import get_db
from app.core.enums import CapabilitySignal, Lifecycle
from app.identity.models import Person, UserPersonLink
from app.positions.service import set_preference_signal
from app.rostering.models import PositionCapability
from app.web import context, templates

router = APIRouter(prefix="/manage/crew")


def _regions(db: Session, request: Request) -> list[Region]:
    rows = list(db.scalars(select(Region).where(Region.lifecycle == "ACTIVE").order_by(Region.name)))
    return [row for row in rows if can_manage_region(request.state.actor, row.id)]


def _person_in_scope(db: Session, request: Request, person_id: uuid.UUID) -> Person:
    person = db.get(Person, person_id)
    if not person or not person.home_region_id:
        raise HTTPException(404)
    require_manage_region(request.state.actor, person.home_region_id)
    return person


@router.get("", response_class=HTMLResponse)
def crew_management(
    request: Request,
    region_id: uuid.UUID | None = None,
    q: str = "",
    show_archived: bool = False,
    db: Session = Depends(get_db),
):
    regions = _regions(db, request)
    if not regions:
        raise HTTPException(403, "Regional roster authority required")
    selected = next((region for region in regions if region.id == region_id), regions[0])
    statement = select(Person).where(Person.home_region_id == selected.id)
    if not show_archived:
        statement = statement.where(Person.lifecycle == Lifecycle.ACTIVE.value)
    if q.strip():
        statement = statement.where(Person.display_name.ilike(f"%{q.strip()}%"))
    people = list(db.scalars(statement.order_by(Person.display_name)))
    person_ids = [person.id for person in people]
    memberships: dict[uuid.UUID, set[uuid.UUID]] = {person.id: set() for person in people}
    capabilities: dict[uuid.UUID, dict[uuid.UUID, set[str]]] = {person.id: {} for person in people}
    linked: set[uuid.UUID] = set()
    if person_ids:
        for person_id, group_id in db.execute(
            select(PersonCrewGroup.person_id, PersonCrewGroup.crew_group_id).where(
                PersonCrewGroup.person_id.in_(person_ids)
            )
        ):
            memberships[person_id].add(group_id)
        for row in db.scalars(
            select(PositionCapability).where(PositionCapability.person_id.in_(person_ids))
        ):
            capabilities[row.person_id].setdefault(row.base_position_id, set()).add(row.signal)
        linked = set(
            db.scalars(select(UserPersonLink.person_id).where(UserPersonLink.person_id.in_(person_ids)))
        )
    return templates.TemplateResponse(
        "crew_management.html",
        context(
            request,
            regions=regions,
            selected_region=selected,
            people=people,
            groups=list(
                db.scalars(
                    select(CrewGroup).where(CrewGroup.lifecycle == "ACTIVE").order_by(CrewGroup.name)
                )
            ),
            positions=list(
                db.scalars(
                    select(BasePosition)
                    .where(BasePosition.lifecycle == "ACTIVE")
                    .order_by(BasePosition.name)
                )
            ),
            memberships=memberships,
            capabilities=capabilities,
            linked=linked,
            query=q,
            show_archived=show_archived,
        ),
    )


@router.post("")
def create_crew_member(
    request: Request,
    display_name: str = Form(...),
    email: str = Form(""),
    region_id: uuid.UUID = Form(...),
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
):
    verify_csrf(request, csrf_token)
    require_manage_region(request.state.actor, region_id)
    clean_name = display_name.strip()
    if not 2 <= len(clean_name) <= 120:
        raise HTTPException(400, "Enter a crew name between 2 and 120 characters.")
    try:
        clean_email = validated_email(email) if email.strip() else None
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    person = Person(display_name=clean_name, email=clean_email, home_region_id=region_id)
    db.add(person)
    db.flush()
    record_audit(
        db,
        "person.created",
        "person",
        person.id,
        request.state.user.id,
        region_id=region_id,
        detail={"display_name": clean_name},
    )
    db.commit()
    return RedirectResponse(f"/manage/crew?region_id={region_id}", status_code=303)


@router.post("/{person_id}/groups")
def update_groups(
    person_id: uuid.UUID,
    request: Request,
    group_ids: list[uuid.UUID] = Form(default=[]),
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
):
    verify_csrf(request, csrf_token)
    person = _person_in_scope(db, request, person_id)
    allowed = set(
        db.scalars(
            select(CrewGroup.id).where(
                CrewGroup.id.in_(group_ids), CrewGroup.lifecycle == Lifecycle.ACTIVE.value
            )
        )
    ) if group_ids else set()
    if len(allowed) != len(set(group_ids)):
        raise HTTPException(400, "Select active crew groups only.")
    db.execute(delete(PersonCrewGroup).where(PersonCrewGroup.person_id == person.id))
    db.add_all(
        [PersonCrewGroup(person_id=person.id, crew_group_id=group_id) for group_id in allowed]
    )
    record_audit(
        db,
        "person.groups.updated",
        "person",
        person.id,
        request.state.user.id,
        region_id=person.home_region_id,
        detail={"group_count": len(allowed)},
    )
    db.commit()
    return RedirectResponse(f"/manage/crew?region_id={person.home_region_id}#{person.id}", status_code=303)


@router.post("/{person_id}/capabilities/{position_id}")
def update_manager_capability(
    person_id: uuid.UUID,
    position_id: uuid.UUID,
    request: Request,
    signal: str = Form(...),
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
):
    verify_csrf(request, csrf_token)
    person = _person_in_scope(db, request, person_id)
    if signal not in {
        CapabilitySignal.MANAGER_ALLOW.value,
        CapabilitySignal.MANAGER_BLOCK.value,
    }:
        raise HTTPException(400, "Invalid Manager capability decision.")
    position = db.get(BasePosition, position_id)
    if not position or position.lifecycle != Lifecycle.ACTIVE.value:
        raise HTTPException(404)
    set_preference_signal(db, person.id, position.id, signal, request.state.user.id)
    record_audit(
        db,
        "person.capability.updated",
        "person",
        person.id,
        request.state.user.id,
        region_id=person.home_region_id,
        detail={"position_id": str(position.id), "signal": signal},
    )
    db.commit()
    return RedirectResponse(f"/manage/crew?region_id={person.home_region_id}#{person.id}", status_code=303)


@router.post("/{person_id}/lifecycle")
def update_lifecycle(
    person_id: uuid.UUID,
    request: Request,
    lifecycle: str = Form(...),
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
):
    verify_csrf(request, csrf_token)
    person = _person_in_scope(db, request, person_id)
    if lifecycle not in {Lifecycle.ACTIVE.value, Lifecycle.ARCHIVED.value}:
        raise HTTPException(400, "Invalid lifecycle.")
    person.lifecycle = lifecycle
    record_audit(
        db,
        "person.lifecycle.updated",
        "person",
        person.id,
        request.state.user.id,
        region_id=person.home_region_id,
        detail={"lifecycle": lifecycle},
    )
    db.commit()
    return RedirectResponse(
        f"/manage/crew?region_id={person.home_region_id}&show_archived=true", status_code=303
    )
