from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.hours.service import (
    format_minutes,
    fortnight_bounds,
    group_people,
    published_hours,
)
from app.web import context, templates

router = APIRouter()


def _offset(value: int) -> int:
    if not -130 <= value <= 130:
        raise HTTPException(400, "Fortnight offset is outside the supported range.")
    return value


@router.get("/hours", response_class=HTMLResponse)
def employee_hours(
    request: Request, offset: int = Query(0), db: Session = Depends(get_db)
):
    offset = _offset(offset)
    start, end = fortnight_bounds(offset)
    rows = published_hours(db, actor=request.state.actor, start=start, end=end, management=False)
    return templates.TemplateResponse(
        "hours.html",
        context(
            request,
            management=False,
            rows=rows,
            people=[],
            total=format_minutes(sum(int(row["minutes"]) for row in rows)),
            start=start,
            end=end,
            offset=offset,
        ),
    )


@router.get("/manage/hours", response_class=HTMLResponse)
def management_hours(
    request: Request, offset: int = Query(0), db: Session = Depends(get_db)
):
    if not request.state.actor.is_admin and not any(
        {"MANAGER", "SUB_MANAGER", "VIEWER"} & set(roles)
        for roles in request.state.actor.regional_roles.values()
    ):
        raise HTTPException(403, "Regional hours visibility required.")
    offset = _offset(offset)
    start, end = fortnight_bounds(offset)
    rows = published_hours(db, actor=request.state.actor, start=start, end=end, management=True)
    return templates.TemplateResponse(
        "hours.html",
        context(
            request,
            management=True,
            rows=[],
            people=group_people(rows),
            total="",
            start=start,
            end=end,
            offset=offset,
        ),
    )
