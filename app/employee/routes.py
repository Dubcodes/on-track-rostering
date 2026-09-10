from __future__ import annotations

import calendar
import uuid
from datetime import date, timedelta

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit.models import HumanChange
from app.audit.service import record_audit
from app.auth.policy import (
    can_crew_view,
    can_view_management_detail,
    can_view_published,
)
from app.auth.security import credential_error, hash_credential, verify_credential, verify_csrf
from app.catalog.models import BasePosition, Region
from app.core.database import get_db
from app.core.enums import CapabilitySignal, Role
from app.core.time import local_today, utcnow, worked_minutes
from app.employee.read_models import day_assignments, month_items
from app.identity.models import PasskeyCredential, RoleGrant, TotpFactor, TrustedDevice, User
from app.notifications.models import NotificationPreference, PushSubscription
from app.positions.service import set_preference_signal
from app.rostering.models import (
    AllowanceIndicator,
    Assignment,
    PositionCapability,
    Workday,
    WorkdayRevision,
)
from app.rostering.participation import person_day_participation
from app.rostering.service import decline_published_assignment
from app.web import context, month_grid, templates

router = APIRouter()


def _month_bounds(year: int, month: int) -> tuple[date, date]:
    start = date(year, month, 1)
    return start, date(year + (month == 12), 1 if month == 12 else month + 1, 1)


@router.get("/", include_in_schema=False)
def root():
    from fastapi.responses import RedirectResponse

    return RedirectResponse("/month", status_code=303)


@router.get("/month", response_class=HTMLResponse)
def month_view(
    request: Request, year: int | None = None, month: int | None = None, db: Session = Depends(get_db)
):
    today = local_today()
    year, month = year or today.year, month or today.month
    if not 1 <= month <= 12 or not 2020 <= year <= 2100:
        raise HTTPException(400, "Invalid month")
    start, end = _month_bounds(year, month)
    items = month_items(db, request.state.actor, start, end)
    by_date: dict[date, list[dict[str, object]]] = {}
    for item in items:
        by_date.setdefault(item["date"], []).append(item)  # type: ignore[arg-type]
    grid = month_grid(year, month)
    week_minutes = [
        sum(
            int(item["minutes"])
            for cell in week
            for item in by_date.get(cell["date"], [])  # type: ignore[arg-type]
            if item["own"]
        )
        for week in grid
    ]
    upcoming = [
        item
        for item in month_items(db, request.state.actor, today, today + timedelta(days=370))
        if item["own"]
    ][:3]
    previous = date(year - (month == 1), 12 if month == 1 else month - 1, 1)
    following = date(year + (month == 12), 1 if month == 12 else month + 1, 1)
    return templates.TemplateResponse(
        "month.html",
        context(
            request,
            grid=grid,
            items_by_date=by_date,
            week_minutes=week_minutes,
            next_up=upcoming,
            month_label=f"{calendar.month_name[month]} {year}",
            previous=previous,
            following=following,
        ),
    )


@router.get("/api/month")
def month_api(
    request: Request, year: int = Query(...), month: int = Query(...), db: Session = Depends(get_db)
):
    start, end = _month_bounds(year, month)
    rows = month_items(db, request.state.actor, start, end)
    saved_at = max((row["published_at"] for row in rows if row["published_at"]), default=None)
    return {"user_namespace": str(request.state.user.id), "saved_at": saved_at, "days": rows}


@router.get("/day/{workday_id}", response_class=HTMLResponse)
def day_view(workday_id: uuid.UUID, request: Request, db: Session = Depends(get_db)):
    workday = db.get(Workday, workday_id)
    revision = db.get(WorkdayRevision, workday.current_published_revision_id) if workday else None
    if not workday or not revision or not can_view_published(db, request.state.actor, workday, revision):
        raise HTTPException(404, "Workday not found")
    management = can_view_management_detail(request.state.actor, workday.region_id)
    assignments = day_assignments(
        db,
        request.state.actor,
        revision,
        can_view_all_rows=can_crew_view(request.state.actor, workday.region_id),
        can_view_private_notes=management,
    )
    own_rows = list(
        db.scalars(
            select(Assignment).where(
                Assignment.revision_id == revision.id,
                Assignment.person_id == request.state.actor.person_id,
            )
        )
    ) if request.state.actor.person_id else []
    participation = person_day_participation(revision, own_rows) if own_rows else None
    history = list(
        db.scalars(
            select(HumanChange)
            .where(HumanChange.workday_id == workday.id)
            .order_by(HumanChange.occurred_at.desc())
        )
    )
    if not management:
        history = [row for row in history if not row.summary.startswith("Publication reason:")]
    return templates.TemplateResponse(
        "day.html",
        context(
            request,
            workday=workday,
            revision=revision,
            assignments=assignments,
            management=management,
            minutes=(
                participation.minutes
                if participation
                else worked_minutes(revision.work_date, revision.start_time, revision.end_time)
            ),
            participation=participation,
            allowances=list(
                db.scalars(
                    select(AllowanceIndicator).where(
                        AllowanceIndicator.revision_id == revision.id,
                        AllowanceIndicator.person_id.in_(
                            [row.person_id for row in own_rows if row.person_id]
                        ),
                    )
                )
            ) if own_rows else [],
            history=history,
        ),
    )


@router.get("/day/{workday_id}/assignments/{slot_key}/decline", response_class=HTMLResponse)
def decline_confirmation(
    workday_id: uuid.UUID, slot_key: uuid.UUID, request: Request, db: Session = Depends(get_db)
):
    workday = db.get(Workday, workday_id)
    revision = db.get(WorkdayRevision, workday.current_published_revision_id) if workday else None
    assignment = db.scalar(
        select(Assignment).where(
            Assignment.revision_id == revision.id,
            Assignment.slot_key == slot_key,
            Assignment.person_id == request.state.actor.person_id,
            Assignment.status == "ASSIGNED",
        )
    ) if revision else None
    region = db.get(Region, workday.region_id) if workday else None
    if not workday or not revision or not assignment or not region:
        raise HTTPException(404, "Assignment not found")
    return templates.TemplateResponse(
        "decline_confirmation.html",
        context(request, workday=workday, revision=revision, assignment=assignment, region=region),
    )


@router.post("/day/{workday_id}/assignments/{slot_key}/decline")
def decline_assignment(
    workday_id: uuid.UUID,
    slot_key: uuid.UUID,
    request: Request,
    confirm: str = Form(...),
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
):
    verify_csrf(request, csrf_token)
    if confirm != "yes" or request.state.actor.person_id is None:
        raise HTTPException(400, "Explicit confirmation is required.")
    db.commit()
    try:
        decline_published_assignment(
            db,
            workday_id=workday_id,
            slot_key=slot_key,
            person_id=request.state.actor.person_id,
            actor_user_id=request.state.user.id,
        )
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return RedirectResponse(f"/day/{workday_id}?declined=1", status_code=303)


@router.get("/crew", response_class=HTMLResponse)
def crew_view(
    request: Request,
    region_id: uuid.UUID | None = None,
    year: int | None = None,
    month: int | None = None,
    db: Session = Depends(get_db),
):
    today = local_today()
    year, month = year or today.year, month or today.month
    start, end = _month_bounds(year, month)
    regions = list(db.scalars(select(Region).where(Region.lifecycle == "ACTIVE").order_by(Region.name)))
    regions = [region for region in regions if can_crew_view(request.state.actor, region.id)]
    if not regions:
        raise HTTPException(403, "Crew View is not available for this account.")
    selected_region = next((region for region in regions if region.id == region_id), regions[0])
    rows = db.execute(
        select(Workday, WorkdayRevision)
        .join(WorkdayRevision, Workday.current_published_revision_id == WorkdayRevision.id)
        .where(
            Workday.region_id == selected_region.id,
            WorkdayRevision.work_date >= start,
            WorkdayRevision.work_date < end,
        )
        .order_by(WorkdayRevision.work_date)
    ).all()
    days = [
        {
            "workday": workday,
            "revision": revision,
            "assignments": day_assignments(
                db,
                request.state.actor,
                revision,
                can_view_all_rows=True,
                can_view_private_notes=can_view_management_detail(
                    request.state.actor, workday.region_id
                ),
            ),
        }
        for workday, revision in rows
    ]
    return templates.TemplateResponse(
        "crew.html",
        context(
            request,
            regions=regions,
            selected_region=selected_region,
            days=days,
            month_label=f"{calendar.month_name[month]} {year}",
            year=year,
            month=month,
        ),
    )
@router.get("/api/day/{workday_id}")
def day_api(workday_id: uuid.UUID, request: Request, db: Session = Depends(get_db)):
    workday = db.get(Workday, workday_id)
    revision = db.get(WorkdayRevision, workday.current_published_revision_id) if workday else None
    if not workday or not revision or not can_view_published(db, request.state.actor, workday, revision):
        raise HTTPException(404, "Workday not found")
    personal_assignments = day_assignments(
        db,
        request.state.actor,
        revision,
        can_view_all_rows=False,
        can_view_private_notes=False,
    )
    return {
        "product_name": request.state.branding.product_name,
        "user_namespace": str(request.state.user.id),
        "saved_at": revision.published_at,
        "offline_cacheable": bool(personal_assignments),
        "workday": {
            "id": str(workday.id),
            "revision_id": str(revision.id),
            "revision_number": revision.revision_number,
            "published_at": revision.published_at,
            "date": revision.work_date,
            "title": revision.title,
            "track": revision.track_name_snapshot,
            "start": revision.start_time,
            "end": revision.end_time,
            "note": revision.day_note,
            "assignments": personal_assignments,
        },
    }


@router.get("/api/upcoming-work")
def upcoming_work_api(request: Request, db: Session = Depends(get_db)):
    """Return own work across month boundaries for sequential offline prefetch."""
    if request.state.actor.person_id is None:
        return {
            "product_name": request.state.branding.product_name,
            "user_namespace": str(request.state.user.id),
            "saved_at": None,
            "days": [],
        }
    today = local_today()
    rows = month_items(db, request.state.actor, today, today + timedelta(days=370))
    own_rows = [row for row in rows if row["own"]]
    today_rows = [row for row in own_rows if row["date"] == today]
    future_rows = [row for row in own_rows if row["date"] > today]
    selected_rows = today_rows[:1] + future_rows[:3]
    saved_at = max(
        (row["published_at"] for row in selected_rows if row["published_at"]), default=None
    )
    return {
        "product_name": request.state.branding.product_name,
        "user_namespace": str(request.state.user.id),
        "saved_at": saved_at,
        "days": selected_rows,
    }


@router.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request, db: Session = Depends(get_db)):
    positions = list(db.scalars(select(BasePosition).where(BasePosition.lifecycle == "ACTIVE").order_by(BasePosition.name)))
    signals = {}
    if request.state.actor.person_id:
        signals = {
            row.base_position_id: row.signal
            for row in db.scalars(
                select(PositionCapability).where(
                    PositionCapability.person_id == request.state.actor.person_id,
                    PositionCapability.signal.in_(
                        [
                            CapabilitySignal.EMPLOYEE_ALLOW.value,
                            CapabilitySignal.EMPLOYEE_OPT_OUT.value,
                        ]
                    ),
                )
            )
        }
    return templates.TemplateResponse(
        "settings.html",
        context(
            request,
            positions=positions,
            capability_signals=signals,
            passkeys=list(
                db.scalars(
                    select(PasskeyCredential)
                    .where(PasskeyCredential.user_id == request.state.user.id)
                    .order_by(PasskeyCredential.created_at)
                )
            ),
            totp_factor=db.get(TotpFactor, request.state.user.id),
            notification_preference=db.get(NotificationPreference, request.state.user.id),
            push_subscriptions=list(
                db.scalars(
                    select(PushSubscription).where(
                        PushSubscription.user_id == request.state.user.id,
                        PushSubscription.active.is_(True),
                    )
                )
            ),
        ),
    )


@router.post("/settings/theme")
def update_theme(
    request: Request,
    theme: str = Form(...),
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
):
    verify_csrf(request, csrf_token)
    if theme not in {"trackside", "steel", "moss", "daylight", "high-contrast"}:
        raise HTTPException(400, "Invalid theme.")
    user = db.get(User, request.state.user.id)
    if not user:
        raise HTTPException(404)
    user.theme = theme
    db.commit()
    return RedirectResponse("/settings?theme=saved", status_code=303)


@router.post("/settings/reauthenticate")
def reauthenticate(
    request: Request,
    credential: str = Form(...),
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
):
    verify_csrf(request, csrf_token)
    user = db.get(User, request.state.user.id)
    device = db.get(TrustedDevice, request.state.device.id)
    if not user or not device or not verify_credential(credential, user.credential_hash):
        raise HTTPException(400, "Credential could not be verified.")
    from app.core.time import utcnow

    device.primary_authenticated_at = utcnow()
    db.commit()
    return RedirectResponse("/settings?reauthenticated=1", status_code=303)


@router.post("/settings/credential")
def update_credential(
    request: Request,
    current_credential: str = Form(...),
    new_credential: str = Form(...),
    confirmation: str = Form(...),
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
):
    verify_csrf(request, csrf_token)
    user = db.get(User, request.state.user.id)
    if not user or not verify_credential(current_credential, user.credential_hash):
        raise HTTPException(400, "Current credential could not be verified.")
    if new_credential != confirmation:
        raise HTTPException(400, "New credentials did not match.")
    has_admin_grant = bool(
        db.scalar(
            select(RoleGrant.user_id).where(
                RoleGrant.user_id == user.id,
                RoleGrant.role == Role.ADMIN.value,
                RoleGrant.status.in_(["ACTIVE", "PENDING"]),
            )
        )
    )
    policy_role = Role.ADMIN.value if has_admin_grant else Role.EMPLOYEE.value
    if error := credential_error(new_credential, policy_role):
        raise HTTPException(400, error)
    user.credential_hash = hash_credential(new_credential)
    user.credential_kind = "pin" if new_credential.isdigit() else "password"
    user.credential_admin_eligible = not bool(
        credential_error(new_credential, Role.ADMIN.value)
    )
    user.auth_epoch += 1
    for device in db.scalars(
        select(TrustedDevice).where(
            TrustedDevice.user_id == user.id, TrustedDevice.revoked_at.is_(None)
        )
    ):
        device.revoked_at = utcnow()
    record_audit(db, "user.credential.updated", "user", user.id, user.id)
    db.commit()
    return RedirectResponse("/login?credential=updated", status_code=303)


@router.post("/settings/capabilities/{position_id}")
def update_capability_preference(
    position_id: uuid.UUID,
    request: Request,
    signal: str = Form(...),
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
):
    verify_csrf(request, csrf_token)
    if request.state.actor.person_id is None or not any(
        Role.EMPLOYEE.value in roles for roles in request.state.actor.regional_roles.values()
    ):
        raise HTTPException(403, "A linked crew identity is required.")
    if signal not in {
        CapabilitySignal.EMPLOYEE_ALLOW.value,
        CapabilitySignal.EMPLOYEE_OPT_OUT.value,
        "CLEAR",
    }:
        raise HTTPException(400, "Invalid capability preference.")
    position = db.get(BasePosition, position_id)
    if not position or position.lifecycle != "ACTIVE":
        raise HTTPException(404)
    set_preference_signal(
        db,
        request.state.actor.person_id,
        position.id,
        signal,
        request.state.user.id,
        family="employee",
    )
    db.commit()
    return RedirectResponse("/settings#capabilities", status_code=303)


@router.get("/help", response_class=HTMLResponse)
def help_page(request: Request, context_key: str = "general"):
    return templates.TemplateResponse("help.html", context(request, context_key=context_key))
