from __future__ import annotations

import calendar
import uuid
from dataclasses import replace
from datetime import date, timedelta

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit.models import HumanChange
from app.audit.service import record_audit
from app.auth.factors import remaining_recovery_codes
from app.auth.network import resolve_request
from app.auth.policy import (
    can_crew_view,
    can_manage_region,
    can_self_decline_assignment,
    can_view_management_detail,
    can_view_published,
)
from app.auth.security import (
    clear_failures,
    credential_error,
    hash_credential,
    is_safe_next,
    is_throttled,
    record_failure,
    throttle_keys,
    verify_credential,
    verify_csrf,
)
from app.catalog.models import BasePosition, Region, Track, TrackMap
from app.catalog.presentation import track_token
from app.core.database import get_db
from app.core.enums import CapabilitySignal, Role, WorkdayCategory
from app.core.holidays import holiday_info_for_date
from app.core.themes import THEME_VALUES, normalize_theme
from app.core.time import local_today, parse_time, utcnow, worked_minutes
from app.employee.read_models import adjacent_published_workdays, day_assignments, month_items
from app.external_calendar.models import CalendarDisplayPreference, ExternalCalendarEvent
from app.external_calendar.read_models import calendar_preference, external_calendar_items
from app.external_calendar.service import event_evidence
from app.help.content import help_topic
from app.hours.service import format_minutes, fortnight_bounds, published_hours
from app.identity.models import PasskeyCredential, Person, RoleGrant, TotpFactor, TrustedDevice, User
from app.notices.service import prominent_notice, recent_notices, relevant_notice_region_ids
from app.notifications.models import NotificationPreference, PushSubscription
from app.notifications.service import record_event
from app.positions.ordering import catalog_position_order
from app.positions.service import set_preference_signal
from app.rostering.models import (
    AllowanceIndicator,
    Assignment,
    PersonalWorkdayEntry,
    PositionCapability,
    Workday,
    WorkdayRevision,
)
from app.rostering.participation import active_published_assignments, person_day_participation
from app.rostering.service import decline_published_assignment, respond_to_reschedule
from app.track_maps.service import effective_map
from app.web import context, month_grid, templates

router = APIRouter()


def _fresh_auth_keys(request: Request) -> tuple[str, str]:
    return throttle_keys(
        f"fresh-auth:user:{request.state.user.id}",
        f"fresh-auth:{resolve_request(request).client_address}",
    )


def _verify_fresh_credential(db: Session, request: Request, user: User | None, credential: str) -> bool:
    keys = _fresh_auth_keys(request)
    if (
        any(is_throttled(db, key) for key in keys)
        or user is None
        or not verify_credential(credential, user.credential_hash)
    ):
        for key in keys:
            record_failure(db, key)
        return False
    for key in keys:
        clear_failures(db, key)
    return True


def _month_bounds(year: int, month: int) -> tuple[date, date]:
    if not 1 <= month <= 12 or not 2020 <= year <= 2100:
        raise HTTPException(400, "Invalid month")
    start = date(year, month, 1)
    return start, date(year + (month == 12), 1 if month == 12 else month + 1, 1)


@router.get("/", include_in_schema=False)
def root():
    from fastapi.responses import RedirectResponse

    return RedirectResponse("/month", status_code=303)


@router.get("/month", response_class=HTMLResponse)
def month_view(
    request: Request,
    year: int | None = None,
    month: int | None = None,
    view: str = Query("month", pattern="^(month|list)$"),
    db: Session = Depends(get_db),
):
    today = local_today()
    year, month = year or today.year, month or today.month
    start, end = _month_bounds(year, month)
    items = month_items(db, request.state.actor, start, end)
    external_items = external_calendar_items(db, request.state.actor, start, end)
    by_date: dict[date, list[dict[str, object]]] = {}
    for item in items:
        by_date.setdefault(item["date"], []).append(item)  # type: ignore[arg-type]
    external_by_date: dict[date, list[dict[str, object]]] = {}
    for item in external_items:
        external_by_date.setdefault(item["date"], []).append(item)  # type: ignore[arg-type]
    holiday_region = ""
    if request.state.actor.person_id:
        holiday_region = (
            db.scalar(
                select(Region.statutory_holiday_region)
                .join(Person, Person.home_region_id == Region.id)
                .where(Person.id == request.state.actor.person_id)
            )
            or ""
        )
    grid = month_grid(year, month, holiday_region)
    totals_items = month_items(
        db,
        request.state.actor,
        grid[0][0]["date"],
        grid[-1][-1]["date"] + timedelta(days=1),
    )
    totals_by_date: dict[date, list[dict[str, object]]] = {}
    for item in totals_items:
        totals_by_date.setdefault(item["date"], []).append(item)  # type: ignore[arg-type]
    week_minutes = [
        sum(
            int(item["minutes"])
            for cell in week
            for item in totals_by_date.get(cell["date"], [])  # type: ignore[arg-type]
            if item["own"]
        )
        for week in grid
    ]
    upcoming = [
        item
        for item in month_items(db, request.state.actor, today, today + timedelta(days=370))
        if item["own"] and item["workday_status"] == "SCHEDULED"
    ][:5]
    previous = date(year - (month == 1), 12 if month == 1 else month - 1, 1)
    following = date(year + (month == 12), 1 if month == 12 else month + 1, 1)
    notice_regions = relevant_notice_region_ids(db, request.state.actor)
    can_build = any(
        can_manage_region(request.state.actor, region_id)
        for region_id in db.scalars(select(Region.id).where(Region.lifecycle == "ACTIVE"))
    )
    fortnight_markers: dict[date, dict[str, str]] = {}
    if request.state.actor.person_id:
        current_start, _ = fortnight_bounds(
            today=today, anchor=request.state.system_settings.fortnight_anchor
        )
        first_grid_day = grid[0][0]["date"]
        last_grid_day = grid[-1][-1]["date"]
        first_offset = ((first_grid_day - current_start).days - 13) // 14
        for marker_offset in range(first_offset, first_offset + 5):
            marker_date = current_start + timedelta(days=marker_offset * 14 + 13)
            if first_grid_day <= marker_date <= last_grid_day:
                fortnight_start, fortnight_end = fortnight_bounds(
                    marker_offset,
                    today=today,
                    anchor=request.state.system_settings.fortnight_anchor,
                )
                hours_rows = published_hours(
                    db,
                    actor=request.state.actor,
                    start=fortnight_start,
                    end=fortnight_end,
                    management=False,
                )
                fortnight_markers[marker_date] = {
                    "url": f"/hours?offset={marker_offset}",
                    "total": format_minutes(sum(int(row["minutes"]) for row in hours_rows)),
                }
    return templates.TemplateResponse(
        "month.html",
        context(
            request,
            grid=grid,
            items_by_date=by_date,
            external_by_date=external_by_date,
            week_minutes=week_minutes,
            next_up=upcoming,
            month_label=f"{calendar.month_name[month]} {year}",
            previous=previous,
            following=following,
            today=today,
            weekdays=["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
            view=view,
            header_context=f"{calendar.month_name[month]} {year}",
            header_prev_url=f"/month?year={previous.year}&month={previous.month}&view={view}",
            header_next_url=f"/month?year={following.year}&month={following.month}&view={view}",
            month_view_url=f"/month?year={year}&month={month}&view=month",
            list_view_url=f"/month?year={year}&month={month}&view=list",
            fortnight_markers=fortnight_markers,
            can_build=can_build,
            prominent_notice=prominent_notice(db, notice_regions),
        ),
    )


@router.get("/api/month")
def month_api(
    request: Request, year: int = Query(...), month: int = Query(...), db: Session = Depends(get_db)
):
    start, end = _month_bounds(year, month)
    rows = month_items(db, request.state.actor, start, end)
    return {"user_namespace": str(request.state.user.id), "days": rows}


@router.get("/day/{workday_id}", response_class=HTMLResponse)
def day_view(workday_id: uuid.UUID, request: Request, db: Session = Depends(get_db)):
    workday = db.get(Workday, workday_id)
    revision = db.get(WorkdayRevision, workday.current_published_revision_id) if workday else None
    if not workday or not revision or not can_view_published(db, request.state.actor, workday, revision):
        raise HTTPException(404, "Workday not found")
    management = can_view_management_detail(request.state.actor, workday.region_id)
    region = db.get(Region, workday.region_id)
    crew_history = can_crew_view(request.state.actor, workday.region_id)
    own_rows = (
        list(
            db.scalars(
                select(Assignment).where(
                    Assignment.revision_id == revision.id,
                    Assignment.person_id == request.state.actor.person_id,
                )
            )
        )
        if request.state.actor.person_id
        else []
    )
    if workday.generated_from_workday_id:
        own_rows = active_published_assignments(db, workday, revision, own_rows)
        if request.state.actor.person_id and not management and not own_rows:
            raise HTTPException(404, "Workday not found")
    self_decline = can_self_decline_assignment(request.state.actor, workday, revision, own_rows)
    personal_entry = (
        db.scalar(
            select(PersonalWorkdayEntry).where(
                PersonalWorkdayEntry.workday_id == workday.id,
                PersonalWorkdayEntry.person_id == request.state.actor.person_id,
            )
        )
        if request.state.actor.person_id and own_rows
        else None
    )
    assignments = day_assignments(
        db,
        request.state.actor,
        revision,
        can_view_all_rows=crew_history,
        can_view_private_notes=management,
        can_self_decline=self_decline,
        workday=workday,
    )
    response_by_person = {
        str(entry.person_id): entry.replacement_response or "PENDING"
        for entry in db.scalars(
            select(PersonalWorkdayEntry).where(PersonalWorkdayEntry.workday_id == workday.id)
        )
    }
    if workday.rescheduled_from_workday_id and management:
        for row in assignments:
            row["replacement_response"] = response_by_person.get(
                str(row["person_id"]), "PENDING"
            ) if row["person_id"] and row["status"] == "ASSIGNED" else None
    if personal_entry:
        for row in assignments:
            if row["is_own"]:
                row["start"] = personal_entry.start_time or row["start"]
                row["end"] = personal_entry.end_time or row["end"]
                if personal_entry.standard_travel_opt_out:
                    row["transport"] = "Making own way"
    participation = person_day_participation(revision, own_rows) if own_rows else None
    can_opt_out_standard_travel = bool(
        workday.category in {WorkdayCategory.RACE_DAY.value, WorkdayCategory.TRIALS.value}
        and revision.standard_travel_enabled
        and any(row.status == "ASSIGNED" and row.uses_standard_travel for row in own_rows)
    )
    if participation and personal_entry and (
        personal_entry.start_time is not None or personal_entry.end_time is not None
    ):
        personal_start = personal_entry.start_time or participation.start
        personal_end = personal_entry.end_time or participation.end
        participation = replace(
            participation,
            start=personal_start,
            end=personal_end,
            minutes=(
                worked_minutes(revision.work_date, personal_start, personal_end)
                if personal_start is not None and personal_end is not None
                else participation.minutes
            ),
        )
    history = (
        list(
            db.scalars(
                select(HumanChange)
                .where(HumanChange.workday_id == workday.id)
                .order_by(HumanChange.occurred_at.desc())
            )
        )
        if crew_history
        else []
    )
    if crew_history and not management:
        history = [row for row in history if not row.summary.startswith("Publication reason:")]
    source_event = (
        db.get(ExternalCalendarEvent, workday.external_event_id) if workday.external_event_id else None
    )
    track_map = effective_map(db.get(TrackMap, revision.track_id)) if revision.track_id else None
    programme_changed = bool(
        source_event
        and (
            (source_event.meeting_name and source_event.meeting_name != revision.title)
            or any(
                getattr(source_event, field) is not None
                and getattr(source_event, field) != getattr(revision, field)
                for field in ("first_race_time", "last_race_time", "race_count")
            )
        )
    )
    previous_workday_id, next_workday_id = adjacent_published_workdays(
        db, request.state.actor, revision.work_date
    )
    predecessor = (
        db.get(Workday, workday.rescheduled_from_workday_id)
        if workday.rescheduled_from_workday_id
        else None
    )
    replacement = db.scalar(
        select(Workday).where(Workday.rescheduled_from_workday_id == workday.id)
    )
    predecessor_revision = (
        db.get(WorkdayRevision, predecessor.current_published_revision_id) if predecessor else None
    )
    replacement_revision = (
        db.get(WorkdayRevision, replacement.current_published_revision_id) if replacement else None
    )
    return templates.TemplateResponse(
        "day.html",
        context(
            request,
            workday=workday,
            revision=revision,
            presentation=track_token(
                db.scalar(select(Track.palette_slot).where(Track.id == revision.track_id)), workday.category
            ),
            assignments=assignments,
            management=management,
            can_edit=can_manage_region(request.state.actor, workday.region_id),
            history_available=crew_history,
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
            )
            if own_rows
            else [],
            history=history,
            holiday=holiday_info_for_date(
                revision.work_date, region.statutory_holiday_region or "" if region else ""
            ),
            source_evidence=event_evidence(db, source_event) if source_event else None,
            source_event=source_event,
            programme_changed=programme_changed,
            track_map=track_map,
            source_title=(
                f"Raw {'Race' if source_event.event_kind == 'RACE' else 'Trial'} Day Data"
                if source_event
                else None
            ),
            previous_workday_id=previous_workday_id,
            next_workday_id=next_workday_id,
            personal_entry=personal_entry,
            can_personalize=bool(own_rows and request.state.actor.person_id),
            can_opt_out_standard_travel=can_opt_out_standard_travel,
            predecessor=predecessor,
            predecessor_revision=predecessor_revision,
            replacement=replacement,
            replacement_revision=replacement_revision,
            replacement_response=(
                personal_entry.replacement_response
                if personal_entry and workday.rescheduled_from_workday_id
                else None
            ),
            can_respond_to_replacement=bool(
                workday.status == "SCHEDULED"
                and workday.rescheduled_from_workday_id
                and any(row.status == "ASSIGNED" for row in own_rows)
            ),
        ),
    )


@router.post("/day/{workday_id}/replacement-response")
def replacement_response(
    workday_id: uuid.UUID,
    request: Request,
    response: str = Form(...),
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
):
    verify_csrf(request, csrf_token)
    if request.state.actor.person_id is None:
        raise HTTPException(404, "Replacement assignment not found")
    try:
        respond_to_reschedule(
            db,
            workday_id=workday_id,
            person_id=request.state.actor.person_id,
            response=response,
            actor_user_id=request.state.user.id,
        )
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    return RedirectResponse(f"/day/{workday_id}?replacement=response-saved", status_code=303)


@router.post("/day/{workday_id}/personal")
async def save_personal_workday_entry(
    workday_id: uuid.UUID, request: Request, db: Session = Depends(get_db)
):
    form = await request.form()
    verify_csrf(request, str(form.get("csrf_token", "")))
    person_id = request.state.actor.person_id
    workday = db.get(Workday, workday_id)
    revision = db.get(WorkdayRevision, workday.current_published_revision_id) if workday else None
    assigned_rows = (
        list(
            db.scalars(
                select(Assignment).where(
                    Assignment.revision_id == revision.id,
                    Assignment.person_id == person_id,
                    Assignment.status == "ASSIGNED",
                )
            )
        )
        if revision and person_id
        else []
    )
    if not workday or not revision or not person_id or not assigned_rows:
        raise HTTPException(404, "Published assignment not found")
    can_opt_out_standard_travel = bool(
        workday.category in {WorkdayCategory.RACE_DAY.value, WorkdayCategory.TRIALS.value}
        and revision.standard_travel_enabled
        and any(row.uses_standard_travel for row in assigned_rows)
    )
    entry = db.scalar(
        select(PersonalWorkdayEntry).where(
            PersonalWorkdayEntry.workday_id == workday.id,
            PersonalWorkdayEntry.person_id == person_id,
        )
    )
    if entry is None:
        entry = PersonalWorkdayEntry(workday_id=workday.id, person_id=person_id)
        db.add(entry)
    previous_opt_out = entry.standard_travel_opt_out
    entry.note = str(form.get("note", "")).strip()
    entry.start_time = parse_time(str(form.get("personal_start_time", "")))
    entry.end_time = parse_time(str(form.get("personal_finish_time", "")))
    entry.last_race_time_changed = bool(form.get("last_race_time_changed"))
    entry.finished_back_at_office = bool(form.get("finished_back_at_office"))
    entry.standard_travel_opt_out = bool(
        can_opt_out_standard_travel and form.get("standard_travel_opt_out")
    )
    entry.updated_at = utcnow()
    if previous_opt_out != entry.standard_travel_opt_out:
        record_audit(
            db,
            "workday.personal_travel_changed",
            "workday",
            workday.id,
            request.state.user.id,
            region_id=workday.region_id,
            detail={"making_own_way": entry.standard_travel_opt_out},
        )
        person = db.get(Person, person_id)
        person_name = person.display_name if person else request.state.user.display_name
        record_event(
            db,
            event_key=f"travel-exception:{workday.id}:{person_id}:{entry.updated_at.isoformat()}",
            event_type="MANAGER_ACTION_REQUIRED",
            region_id=workday.region_id,
            workday_id=workday.id,
            payload={
                "kind": "TRAVEL_EXCEPTION",
                "making_own_way": entry.standard_travel_opt_out,
                "person": person_name,
                "date": revision.work_date.isoformat(),
                "context": revision.track_name_snapshot or revision.title,
                "message": (
                    f"{person_name} is now making their own way for "
                    f"{revision.track_name_snapshot or revision.title} on "
                    f"{revision.work_date:%d %b %Y}."
                    if entry.standard_travel_opt_out
                    else f"{person_name} has rejoined standard travel for "
                    f"{revision.track_name_snapshot or revision.title} on "
                    f"{revision.work_date:%d %b %Y}."
                ),
                "url": f"/day/{workday.id}",
            },
        )
    db.commit()
    return RedirectResponse(f"/day/{workday.id}?personal=saved#notes-and-timing", status_code=303)


@router.get("/day/{workday_id}/assignments/{slot_key}/decline", response_class=HTMLResponse)
def decline_confirmation(
    workday_id: uuid.UUID, slot_key: uuid.UUID, request: Request, db: Session = Depends(get_db)
):
    workday = db.get(Workday, workday_id)
    revision = db.get(WorkdayRevision, workday.current_published_revision_id) if workday else None
    assignment = (
        db.scalar(
            select(Assignment).where(
                Assignment.revision_id == revision.id,
                Assignment.slot_key == slot_key,
                Assignment.person_id == request.state.actor.person_id,
                Assignment.status == "ASSIGNED",
            )
        )
        if revision
        else None
    )
    region = db.get(Region, workday.region_id) if workday else None
    if not workday or not revision or not assignment or not region:
        raise HTTPException(404, "Assignment not found")
    own_rows = list(
        db.scalars(
            select(Assignment).where(
                Assignment.revision_id == revision.id,
                Assignment.person_id == request.state.actor.person_id,
            )
        )
    )
    if not can_self_decline_assignment(request.state.actor, workday, revision, own_rows):
        raise HTTPException(403, "Self-decline is no longer available for this assignment.")
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
    workday = db.get(Workday, workday_id)
    revision = db.get(WorkdayRevision, workday.current_published_revision_id) if workday else None
    own_rows = (
        list(
            db.scalars(
                select(Assignment).where(
                    Assignment.revision_id == revision.id,
                    Assignment.person_id == request.state.actor.person_id,
                )
            )
        )
        if revision
        else []
    )
    if (
        not workday
        or not revision
        or not can_self_decline_assignment(request.state.actor, workday, revision, own_rows)
    ):
        raise HTTPException(403, "Self-decline is no longer available for this assignment.")
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
    view: str = Query("month", pattern="^(month|list)$"),
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
        select(Workday, WorkdayRevision, Track.palette_slot)
        .join(WorkdayRevision, Workday.current_published_revision_id == WorkdayRevision.id)
        .outerjoin(Track, Track.id == WorkdayRevision.track_id)
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
            "presentation": track_token(slot, workday.category),
            "assignments": day_assignments(
                db,
                request.state.actor,
                revision,
                can_view_all_rows=True,
                can_view_private_notes=can_view_management_detail(request.state.actor, workday.region_id),
                workday=workday,
            ),
        }
        for workday, revision, slot in rows
    ]
    grid = month_grid(year, month, selected_region.statutory_holiday_region or "")
    by_date: dict[date, list[dict[str, object]]] = {}
    for item in days:
        revision = item["revision"]
        assignments = item["assignments"]
        statuses = {row["status"] for row in assignments}
        active = item["workday"].status == "SCHEDULED"
        by_date.setdefault(revision.work_date, []).append(
            {
                "id": str(item["workday"].id),
                "category": item["workday"].category,
                "date": revision.work_date,
                "track": revision.track_name_snapshot,
                "title": revision.title,
                "presentation": item["presentation"],
                "start": revision.start_time,
                "role": f"{len(assignments)} crew",
                "has_open": active and "OPEN" in statuses,
                "status": (
                    "TBC" if active and "TBC" in statuses
                    else "PUBLISHED" if active
                    else item["workday"].status
                ),
                "workday_status": item["workday"].status,
            }
        )
    previous = date(year - (month == 1), 12 if month == 1 else month - 1, 1)
    following = date(year + (month == 12), 1 if month == 12 else month + 1, 1)
    base_query = f"region_id={selected_region.id}"
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
            grid=grid,
            items_by_date=by_date,
            weekdays=["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
            week_counts=[sum(len(by_date.get(cell["date"], [])) for cell in week) for week in grid],
            view=view,
            crew_mode=True,
            header_context=f"{calendar.month_name[month]} {year} · Crew",
            header_prev_url=f"/crew?{base_query}&year={previous.year}&month={previous.month}&view={view}",
            header_next_url=f"/crew?{base_query}&year={following.year}&month={following.month}&view={view}",
            month_view_url=f"/crew?{base_query}&year={year}&month={month}&view=month",
            list_view_url=f"/crew?{base_query}&year={year}&month={month}&view=list",
            can_build=can_manage_region(request.state.actor, selected_region.id),
            prominent_notice=prominent_notice(db, {selected_region.id}),
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
        workday=workday,
    )
    management = can_view_management_detail(request.state.actor, workday.region_id)
    if (
        workday.generated_from_workday_id
        and request.state.actor.person_id
        and not management
        and not personal_assignments
    ):
        raise HTTPException(404, "Workday not found")
    personal_entry = (
        db.scalar(
            select(PersonalWorkdayEntry).where(
                PersonalWorkdayEntry.workday_id == workday.id,
                PersonalWorkdayEntry.person_id == request.state.actor.person_id,
            )
        )
        if request.state.actor.person_id and personal_assignments
        else None
    )
    if personal_entry:
        for row in personal_assignments:
            if row["is_own"]:
                row["start"] = personal_entry.start_time or row["start"]
                row["end"] = personal_entry.end_time or row["end"]
                if personal_entry.standard_travel_opt_out:
                    row["transport"] = "Making own way"
    own_rows = (
        list(
            db.scalars(
                select(Assignment).where(
                    Assignment.revision_id == revision.id,
                    Assignment.person_id == request.state.actor.person_id,
                    Assignment.status == "ASSIGNED",
                )
            )
        )
        if request.state.actor.person_id
        else []
    )
    own_rows = active_published_assignments(db, workday, revision, own_rows)
    participation = person_day_participation(revision, own_rows) if own_rows else None
    personal_start = participation.start if participation else revision.start_time
    personal_end = participation.end if participation else revision.end_time
    if personal_entry:
        personal_start = personal_entry.start_time or personal_start
        personal_end = personal_entry.end_time or personal_end
    return {
        "product_name": request.state.branding.product_name,
        "user_namespace": str(request.state.user.id),
        "offline_cacheable": bool(personal_assignments),
        "workday": {
            "id": str(workday.id),
            "revision_id": str(revision.id),
            "revision_number": revision.revision_number,
            "published_at": revision.published_at,
            "date": revision.work_date,
            "category": workday.category,
            "title": revision.title,
            "track": revision.track_name_snapshot,
            "start": personal_start,
            "on_track": revision.on_track_time,
            "first_trial": revision.first_trial_time,
            "last_trial": revision.last_trial_time,
            "first_race": revision.first_race_time,
            "last_race": revision.last_race_time,
            "race_count": revision.race_count,
            "end": personal_end,
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
            "days": [],
        }
    today = local_today()
    rows = month_items(db, request.state.actor, today, today + timedelta(days=370))
    own_rows = [
        row for row in rows if row["own"] and row["workday_status"] == "SCHEDULED"
    ]
    today_rows = [row for row in own_rows if row["date"] == today]
    future_rows = [row for row in own_rows if row["date"] > today]
    selected_rows = today_rows[:1] + future_rows[:3]
    return {
        "product_name": request.state.branding.product_name,
        "user_namespace": str(request.state.user.id),
        "days": selected_rows,
    }


@router.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request, db: Session = Depends(get_db)):
    positions = sorted(
        db.scalars(select(BasePosition).where(BasePosition.lifecycle == "ACTIVE")),
        key=catalog_position_order,
    )
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
    manageable_regions = list(
        db.scalars(select(Region).where(Region.lifecycle == "ACTIVE").order_by(Region.name))
    )
    manageable_regions = [
        region for region in manageable_regions if can_manage_region(request.state.actor, region.id)
    ]
    history_regions = relevant_notice_region_ids(db, request.state.actor)
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
            recovery_code_count=remaining_recovery_codes(db, request.state.user.id),
            notification_preference=db.get(NotificationPreference, request.state.user.id),
            calendar_preference=calendar_preference(db, request.state.user.id),
            push_subscriptions=list(
                db.scalars(
                    select(PushSubscription).where(
                        PushSubscription.user_id == request.state.user.id,
                        PushSubscription.active.is_(True),
                    )
                )
            ),
            manageable_notice_regions=manageable_regions,
            notice_history=recent_notices(db, history_regions, request.state.actor.is_admin),
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
    normalized_theme = normalize_theme(theme)
    if theme not in THEME_VALUES and theme != "trackside":
        raise HTTPException(400, "Invalid theme.")
    user = db.get(User, request.state.user.id)
    if not user:
        raise HTTPException(404)
    user.theme = normalized_theme
    db.commit()
    return RedirectResponse("/settings?theme=saved", status_code=303)


@router.post("/settings/calendar")
def update_calendar_preferences(
    request: Request,
    show_thoroughbred: bool = Form(False),
    show_harness: bool = Form(False),
    show_trials: bool = Form(False),
    minimal_external_detail: bool = Form(False),
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
):
    verify_csrf(request, csrf_token)
    preference = db.get(CalendarDisplayPreference, request.state.user.id)
    if not preference:
        preference = CalendarDisplayPreference(user_id=request.state.user.id)
        db.add(preference)
    preference.show_thoroughbred = show_thoroughbred
    preference.show_harness = show_harness
    preference.show_trials = show_trials
    preference.minimal_external_detail = minimal_external_detail
    db.commit()
    return RedirectResponse("/settings?calendar=saved#calendar", status_code=303)


@router.post("/settings/reauthenticate")
def reauthenticate(
    request: Request,
    credential: str = Form(...),
    csrf_token: str = Form(...),
    next: str = Form("/settings"),
    db: Session = Depends(get_db),
):
    verify_csrf(request, csrf_token)
    user = db.get(User, request.state.user.id)
    device = db.get(TrustedDevice, request.state.device.id)
    if not device or not _verify_fresh_credential(db, request, user, credential):
        raise HTTPException(400, "Credential could not be verified.")
    from app.core.time import utcnow

    device.primary_authenticated_at = utcnow()
    db.commit()
    destination = next if is_safe_next(next) else "/settings"
    return RedirectResponse(destination, status_code=303)


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
    if not _verify_fresh_credential(db, request, user, current_credential):
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
    user.credential_admin_eligible = not bool(credential_error(new_credential, Role.ADMIN.value))
    user.auth_epoch += 1
    for device in db.scalars(
        select(TrustedDevice).where(TrustedDevice.user_id == user.id, TrustedDevice.revoked_at.is_(None))
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
    resolved_key, topic, links = help_topic(context_key, request.state.actor)
    return templates.TemplateResponse(
        "help.html",
        context(request, context_key=resolved_key, help_topic=topic, help_links=links),
    )
