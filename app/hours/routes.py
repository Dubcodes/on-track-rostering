from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.catalog.models import CrewGroup, PersonCrewGroup, Region
from app.core.database import get_db
from app.core.holidays import holiday_info_for_date
from app.hours.service import (
    format_minutes,
    fortnight_bounds,
    group_people,
    published_hours,
)
from app.identity.models import Person
from app.unavailability.models import PersonUnavailability
from app.web import context, templates

router = APIRouter()


def _offset(value: int) -> int:
    if not -130 <= value <= 130:
        raise HTTPException(400, "Fortnight offset is outside the supported range.")
    return value


@router.get("/hours", response_class=HTMLResponse)
def employee_hours(request: Request, offset: int = Query(0), db: Session = Depends(get_db)):
    offset = _offset(offset)
    start, end = fortnight_bounds(
        offset, anchor=request.state.system_settings.fortnight_anchor
    )
    rows = published_hours(db, actor=request.state.actor, start=start, end=end, management=False)
    rows_by_date = {row["date"]: row for row in rows}
    holiday_region = (
        db.scalar(
            select(Region.statutory_holiday_region)
            .join(Person, Person.home_region_id == Region.id)
            .where(Person.id == request.state.actor.person_id)
        )
        if request.state.actor.person_id
        else ""
    ) or ""
    days = [
        rows_by_date.get(start + timedelta(days=index))
        or {
            "date": start + timedelta(days=index),
            "minutes": 0,
            "duration": "0h",
            "holiday_info": holiday_info_for_date(
                start + timedelta(days=index), holiday_region
            ),
        }
        for index in range(14)
    ]
    return templates.TemplateResponse(
        "hours.html",
        context(
            request,
            management=False,
            rows=days,
            people=[],
            total=format_minutes(sum(int(row["minutes"]) for row in rows)),
            start=start,
            end=end,
            offset=offset,
            matrix_dates=[],
        ),
    )


@router.get("/manage/hours", response_class=HTMLResponse)
def management_hours(
    request: Request,
    offset: int = Query(0),
    q: str = Query(""),
    group_id: str = Query(""),
    region_id: str = Query(""),
    db: Session = Depends(get_db),
):
    if request.state.actor.is_contractor or (
        not request.state.actor.is_admin
        and not any(
            {"MANAGER", "SUB_MANAGER"} & set(roles)
            for roles in request.state.actor.regional_roles.values()
        )
    ):
        raise HTTPException(403, "Regional hours visibility required.")
    offset = _offset(offset)
    start, end = fortnight_bounds(
        offset, anchor=request.state.system_settings.fortnight_anchor
    )
    rows = published_hours(db, actor=request.state.actor, start=start, end=end, management=True)
    leave_statement = (
        select(Person, PersonUnavailability)
        .join(PersonUnavailability, PersonUnavailability.person_id == Person.id)
        .where(
            Person.lifecycle == "ACTIVE",
            PersonUnavailability.cancelled_at.is_(None),
            PersonUnavailability.start_date <= end,
            PersonUnavailability.end_date >= start,
        )
    )
    if not request.state.actor.is_admin:
        visible_regions = {
            region_id for region_id, roles in request.state.actor.regional_roles.items()
            if {"MANAGER", "SUB_MANAGER"} & set(roles)
        }
        leave_statement = leave_statement.where(Person.home_region_id.in_(visible_regions))
    leave_rows = list(db.execute(leave_statement))
    all_people = group_people(rows, start=start, end=end, leave_rows=leave_rows)
    person_ids = {person["person_id"] for person in all_people}
    people_records = {
        person.id: person
        for person in db.scalars(select(Person).where(Person.id.in_(person_ids)))
    }
    memberships: dict[object, set[object]] = {person_id: set() for person_id in person_ids}
    if person_ids:
        for person_id, crew_group_id in db.execute(
            select(PersonCrewGroup.person_id, PersonCrewGroup.crew_group_id).where(
                PersonCrewGroup.person_id.in_(person_ids)
            )
        ):
            memberships[person_id].add(crew_group_id)
    visible_region_ids = (
        None
        if request.state.actor.is_admin
        else {
            region_key
            for region_key, roles in request.state.actor.regional_roles.items()
            if {"MANAGER", "SUB_MANAGER"} & set(roles)
        }
    )
    region_statement = select(Region).where(Region.lifecycle == "ACTIVE")
    if visible_region_ids is not None:
        region_statement = region_statement.where(Region.id.in_(visible_region_ids))
    region_options = list(db.scalars(region_statement.order_by(Region.name)))
    visible_group_ids = {
        crew_group_id for person_groups in memberships.values() for crew_group_id in person_groups
    }
    group_options = list(
        db.scalars(
            select(CrewGroup)
            .where(
                CrewGroup.lifecycle == "ACTIVE",
                CrewGroup.id.in_(visible_group_ids),
            )
            .order_by(CrewGroup.name)
        )
    )
    query = q.strip().casefold()
    people = [
        person
        for person in all_people
        if (not query or query in str(person["person"]).casefold())
        and (
            not group_id
            or any(
                str(crew_group_id) == group_id
                for crew_group_id in memberships.get(person["person_id"], set())
            )
        )
        and (
            not region_id
            or str(people_records[person["person_id"]].home_region_id) == region_id
        )
    ]
    return templates.TemplateResponse(
        "hours.html",
        context(
            request,
            management=True,
            rows=[],
            people=people,
            group_options=group_options,
            region_options=region_options,
            selected_group_id=group_id,
            selected_region_id=region_id,
            query=q,
            total="",
            start=start,
            end=end,
            offset=offset,
            matrix_dates=[start + timedelta(days=index) for index in range(14)],
        ),
    )
