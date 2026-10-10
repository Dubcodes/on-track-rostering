from __future__ import annotations

import uuid
from datetime import date, time
from types import SimpleNamespace
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.audit.service import record_audit
from app.auth.policy import can_manage_region, require_manage_region
from app.auth.security import verify_csrf
from app.catalog.models import BasePosition, Region, Track, Vehicle
from app.core.database import get_db
from app.core.enums import AssignmentStatus, RacingDiscipline, WorkdayCategory, WorkdayStatus
from app.core.time import parse_time
from app.external_calendar.models import ExternalCalendarEvent
from app.external_calendar.service import apply_latest_programme_to_draft
from app.identity.models import Person, UserPersonLink
from app.notifications.service import record_event
from app.positions.ordering import catalog_position_order, position_order
from app.positions.service import bulk_eligibility
from app.rostering.builder_read import crew_picker_views
from app.rostering.conflicts import publication_conflicts
from app.rostering.models import Assignment, OpenPositionApplication, Workday, WorkdayRevision
from app.rostering.service import (
    AssignmentInput,
    DraftAssignmentInput,
    DraftConflict,
    DraftDetailsInput,
    PublishConflict,
    add_assignment,
    create_workday,
    delete_never_published_workday,
    ensure_draft,
    preview_diff,
    publish,
    remove_assignment,
    reschedule_published_workday,
    save_draft,
    update_assignment,
    update_draft_details,
)
from app.rostering.travel import TRANSPORT_LABELS, TRANSPORT_UNASSIGNED
from app.unavailability.service import active_for_people_on_date, unavailability_label
from app.web import context, templates

router = APIRouter(prefix="/manage")


def _editable_regions(db: Session, request: Request) -> list[Region]:
    regions = list(db.scalars(select(Region).where(Region.lifecycle == "ACTIVE").order_by(Region.name)))
    return [region for region in regions if can_manage_region(request.state.actor, region.id)]


def _builder_vehicles(
    db: Session, request: Request, regions: list[Region], workday_region_id: uuid.UUID
) -> list[Vehicle]:
    statement = select(Vehicle).where(Vehicle.lifecycle == "ACTIVE")
    if not request.state.actor.is_admin:
        statement = statement.where(
            or_(Vehicle.home_region_id.in_([region.id for region in regions]),
                Vehicle.home_region_id.is_(None))
        )
    return sorted(
        db.scalars(statement),
        key=lambda vehicle: (vehicle.home_region_id != workday_region_id, vehicle.name.casefold()),
    )


def _day_type_options() -> tuple[tuple[str, str], ...]:
    return (
        ("RACE_DAY:THOROUGHBRED", "Thoroughbred Race Day"),
        ("RACE_DAY:HARNESS", "Harness Race Day"),
        ("TRIALS:THOROUGHBRED", "Thoroughbred Trials"),
        ("TRIALS:HARNESS", "Harness Trials"),
        ("TRAVEL_DAY:", "Travel Day"),
        ("RIG_DAY:", "Rig Day"),
        ("OFFICE_DAY:", "Office Day"),
        ("TRAINING_DAY:", "Training Day"),
        ("OTHER:", "Other"),
    )


def _parse_day_type(value: str) -> tuple[str, str | None]:
    category, separator, discipline = value.partition(":")
    if not separator or category not in {item.value for item in WorkdayCategory}:
        raise ValueError("Select a valid day type.")
    discipline_value = discipline or None
    if category in {WorkdayCategory.RACE_DAY.value, WorkdayCategory.TRIALS.value}:
        if discipline_value not in {item.value for item in RacingDiscipline}:
            raise ValueError("Select a racing discipline.")
    elif discipline_value is not None:
        raise ValueError("Non-racing days cannot have a racing discipline.")
    return category, discipline_value


def _optional_uuid(value: object) -> uuid.UUID | None:
    text = str(value or "").strip()
    return uuid.UUID(text) if text else None


def _optional_int(value: object) -> int | None:
    text = str(value or "").strip()
    return int(text) if text else None


def _draft_payload(  # type: ignore[no-untyped-def]
    form, *, category: str, discipline: str | None, draft: WorkdayRevision | None = None
):
    assignment_ids = form.getlist("assignment_id")
    row_count = len(assignment_ids)
    field_names = (
        "base_position_id",
        "slot_index",
        "person_id",
        "status",
        "note",
        "note_private",
        "assignment_start_time",
        "assignment_end_time",
        "transport_mode",
        "vehicle_id",
        "custom_transport_text",
        "accommodation_name",
        "uses_standard_travel",
        "hotel_to_track_minutes_override",
        "finish_destination_override",
        "return_travel_minutes_override",
    )
    values = {name: form.getlist(name) for name in field_names}
    for name in field_names:
        if not values[name] and row_count:
            values[name] = [""] * row_count
    for name in ("transport_mode", "note_private", "uses_standard_travel"):
        if all(not value for value in values[name]) and row_count:
            default = "UNASSIGNED" if name == "transport_mode" else "1"
            values[name] = [default] * row_count
    if any(len(items) != row_count for items in values.values()):
        raise ValueError("The assignment rows were incomplete. Refresh and try again.")
    assignments = [
        DraftAssignmentInput(
            assignment_id=_optional_uuid(assignment_ids[index]),
            base_position_id=_optional_uuid(values["base_position_id"][index]),
            slot_index=_optional_int(values["slot_index"][index]),
            person_id=_optional_uuid(values["person_id"][index]),
            status=str(values["status"][index]),
            note=str(values["note"][index]),
            note_private=str(values["note_private"][index]) == "1",
            start_time=parse_time(str(values["assignment_start_time"][index])),
            end_time=parse_time(str(values["assignment_end_time"][index])),
            transport_mode=str(values["transport_mode"][index] or TRANSPORT_UNASSIGNED),
            vehicle_id=_optional_uuid(values["vehicle_id"][index]),
            custom_transport_text=str(values["custom_transport_text"][index]),
            accommodation_name=str(values["accommodation_name"][index]),
            uses_standard_travel=str(values["uses_standard_travel"][index]) == "1",
            hotel_to_track_minutes_override=_optional_int(
                values["hotel_to_track_minutes_override"][index]
            ),
            finish_destination_override=str(values["finish_destination_override"][index]),
            return_travel_minutes_override=_optional_int(
                values["return_travel_minutes_override"][index]
            ),
        )
        for index in range(row_count)
    ]
    racing = category in {
        WorkdayCategory.RACE_DAY.value,
        WorkdayCategory.TRIALS.value,
    }
    race_day = category == WorkdayCategory.RACE_DAY.value
    trials = category == WorkdayCategory.TRIALS.value
    details = DraftDetailsInput(
        work_date=date.fromisoformat(str(form["work_date"])),
        track_id=_optional_uuid(form.get("track_id")),
        title=str(form.get("title", "")),
        start_time=parse_time(str(form.get("start_time", ""))),
        start_time_is_override=(
            str(form.get("start_time_is_override", "")) == "1"
            if "start_time_is_override" in form
            else bool(str(form.get("start_time", "")).strip())
        ),
        end_time=parse_time(str(form.get("end_time", ""))),
        end_time_is_override=(
            str(form.get("end_time_is_override", "")) == "1"
            if "end_time_is_override" in form
            else bool(str(form.get("end_time", "")).strip())
        ),
        on_track_time=parse_time(str(form.get("on_track_time", ""))) if racing else None,
        on_track_time_is_override=(
            race_day
            and (
                str(form.get("on_track_time_is_override", "")) == "1"
                if "on_track_time_is_override" in form
                else bool(str(form.get("on_track_time", "")).strip())
            )
        ),
        track_travel_minutes=(
            _optional_int(form.get("track_travel_minutes")) if race_day else None
        ),
        track_travel_minutes_is_override=(
            race_day and str(form.get("track_travel_minutes_is_override", "")) == "1"
        ),
        first_trial_time=parse_time(str(form.get("first_trial_time", ""))) if trials else None,
        last_trial_time=parse_time(str(form.get("last_trial_time", ""))) if trials else None,
        first_race_time=parse_time(str(form.get("first_race_time", ""))) if race_day else None,
        last_race_time=parse_time(str(form.get("last_race_time", ""))) if race_day else None,
        race_count=_optional_int(form.get("race_count")) if race_day else None,
        day_note=str(form.get("day_note", "")),
        change_reason=str(form.get("change_reason", "")),
        start_origin=str(form.get("start_origin", "")),
        finish_destination=str(form.get("finish_destination", "")),
        category=category,
        racing_discipline=discipline,
        standard_travel_enabled=(
            racing and str(form.get("standard_travel_enabled", "")) == "1"
        ),
        travel_departure_time=parse_time(str(form.get("travel_departure_time", ""))),
        travel_to_hotel_minutes=_optional_int(form.get("travel_to_hotel_minutes")),
        default_hotel=str(form.get("default_hotel", "")),
        hotel_to_track_minutes=_optional_int(form.get("hotel_to_track_minutes")),
        return_travel_minutes=_optional_int(form.get("return_travel_minutes")),
        pack_up_minutes=(
            60
            if _optional_int(form.get("pack_up_minutes")) is None
            else _optional_int(form.get("pack_up_minutes"))
        ),
    )
    return details, assignments


@router.get("/workdays/new", response_class=HTMLResponse)
def new_workday_page(
    request: Request,
    work_date: date | None = Query(None, alias="date"),
    region_id: uuid.UUID | None = None,
    db: Session = Depends(get_db),
):
    regions = _editable_regions(db, request)
    if not regions:
        raise HTTPException(403, "Regional roster authority required")
    tracks = list(
        db.scalars(
            select(Track)
            .where(Track.lifecycle == "ACTIVE", Track.region_id.in_([region.id for region in regions]))
            .order_by(Track.name)
        )
    )
    positions = sorted(
        db.scalars(select(BasePosition).where(BasePosition.lifecycle == "ACTIVE")),
        key=catalog_position_order,
    )
    selected_region = next(
        (region for region in regions if region.id == region_id),
        regions[0],
    )
    workday = SimpleNamespace(
        id=None,
        region_id=selected_region.id,
        category=WorkdayCategory.RACE_DAY.value,
        racing_discipline=RacingDiscipline.THOROUGHBRED.value,
        current_published_revision_id=None,
        lock_version=0,
    )
    draft = SimpleNamespace(
        revision_number=1,
        work_date=work_date,
        track_id=None,
        track_name_snapshot="Choose a Track",
        title="Race Day",
        start_time=None,
        start_time_is_override=False,
        end_time=None,
        end_time_is_override=False,
        on_track_time=None,
        on_track_time_is_override=False,
        track_travel_minutes=None,
        track_travel_minutes_is_override=False,
        first_trial_time=None,
        first_race_time=None,
        last_race_time=None,
        race_count=None,
        start_origin="",
        finish_destination="",
        standard_travel_enabled=False,
        travel_departure_time=time(12),
        travel_to_hotel_minutes=None,
        default_hotel="",
        hotel_to_track_minutes=None,
        return_travel_minutes=None,
        pack_up_minutes=60,
        day_note="",
        change_reason="",
    )
    return templates.TemplateResponse(
        "workday_builder.html",
        context(
            request,
            new_mode=True,
            workday=workday,
            draft=draft,
            regions=regions,
            tracks=tracks,
            positions=positions,
            assignments=[],
            people_groups_by_assignment={},
            assignment_leave_by_id={},
            applications_by_slot={},
            vehicles=_builder_vehicles(db, request, regions, selected_region.id),
            vehicle_region_names={region.id: region.name for region in regions},
            transport_labels=TRANSPORT_LABELS,
            setup_lead_minutes=selected_region.lead_minutes_race_day,
            day_type_options=_day_type_options(),
            builder_error=request.query_params.get("error"),
        ),
    )


@router.post("/workdays/{workday_id}/delete")
def delete_draft_workday(
    workday_id: uuid.UUID,
    request: Request,
    confirm_delete: str = Form(""),
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
):
    verify_csrf(request, csrf_token)
    workday = db.get(Workday, workday_id)
    if not workday:
        raise HTTPException(404)
    require_manage_region(request.state.actor, workday.region_id)
    if confirm_delete != "yes":
        raise HTTPException(400, "Confirm permanent draft deletion.")
    try:
        work_date = delete_never_published_workday(
            db,
            workday_id=workday.id,
            actor_user_id=request.state.user.id,
        )
    except ValueError as exc:
        db.rollback()
        raise HTTPException(409, str(exc)) from exc
    return RedirectResponse(
        f"/month?year={work_date.year}&month={work_date.month}", status_code=303
    )


@router.post("/workdays")
async def new_workday(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    verify_csrf(request, str(form.get("csrf_token", "")))
    try:
        region_id = uuid.UUID(str(form["region_id"]))
        require_manage_region(request.state.actor, region_id)
        if "day_type" not in form:
            category = str(form.get("category", ""))
            if category not in {item.value for item in WorkdayCategory}:
                raise ValueError("Select a valid day type.")
            workday = create_workday(
                db,
                region_id=region_id,
                category=category,
                work_date=date.fromisoformat(str(form["work_date"])),
                track_id=_optional_uuid(form.get("track_id")),
                title=str(form.get("title", "")),
                actor_user_id=request.state.user.id,
            )
            return RedirectResponse(f"/manage/workdays/{workday.id}", status_code=303)
        category, discipline = _parse_day_type(str(form.get("day_type", "")))
        details, assignments = _draft_payload(form, category=category, discipline=discipline)
        workday = create_workday(
            db,
            region_id=region_id,
            category=category,
            racing_discipline=discipline,
            work_date=details.work_date,
            track_id=details.track_id,
            title=details.title,
            actor_user_id=request.state.user.id,
            commit=False,
        )
        draft = db.get(WorkdayRevision, workday.current_draft_revision_id)
        save_draft(
            db,
            workday_id=workday.id,
            draft_id=draft.id,
            expected_version=workday.lock_version,
            details=details,
            assignments=assignments,
            commit=False,
        )
        db.commit()
    except (KeyError, TypeError, ValueError) as exc:
        db.rollback()
        raw_date = str(form.get("work_date", ""))
        raw_region = str(form.get("region_id", ""))
        return RedirectResponse(
            f"/manage/workdays/new?date={quote(raw_date)}&region_id={quote(raw_region)}"
            f"&error={quote(str(exc))}",
            status_code=303,
        )
    return RedirectResponse(f"/manage/workdays/{workday.id}/preview", status_code=303)


def _builder_context(
    db: Session, request: Request, workday: Workday, draft: WorkdayRevision, **extra: object
):
    require_manage_region(request.state.actor, workday.region_id)
    assignments = list(
        db.scalars(
            select(Assignment)
            .where(Assignment.revision_id == draft.id)
            .order_by(Assignment.display_name_snapshot)
        )
    )
    assignment_position_ids = {
        assignment.base_position_id for assignment in assignments if assignment.base_position_id
    }
    assignment_positions = {
        position.id: position
        for position in db.scalars(
            select(BasePosition).where(BasePosition.id.in_(assignment_position_ids))
        )
    } if assignment_position_ids else {}
    assignments.sort(
        key=lambda row: (
            catalog_position_order(assignment_positions[row.base_position_id])
            if row.base_position_id in assignment_positions
            else (1, *position_order(row.display_name_snapshot)),
            str(row.slot_key),
        )
    )
    position_ids = {assignment.base_position_id for assignment in assignments}
    picker_views = crew_picker_views(
        db,
        region_id=workday.region_id,
        work_date=draft.work_date,
        exclude_workday_id=workday.id,
        position_ids=position_ids,
    )
    people_groups_by_assignment = {
        assignment.id: picker_views[assignment.base_position_id].groups
        for assignment in assignments
    }
    leave_by_person = active_for_people_on_date(
        db, {row.person_id for row in assignments if row.person_id}, draft.work_date
    )
    assignment_leave_by_id = {
        row.id: unavailability_label(leave.start_date, leave.end_date)
        for row in assignments
        if (leave := leave_by_person.get(row.person_id)) is not None
    }
    application_rows = db.execute(
        select(OpenPositionApplication, Person)
        .join(Person, Person.id == OpenPositionApplication.person_id)
        .where(
            OpenPositionApplication.revision_id == workday.current_published_revision_id,
            OpenPositionApplication.status.in_(["APPLIED", "SELECTED"]),
        )
        .order_by(OpenPositionApplication.created_at)
    ).all() if workday.current_published_revision_id else []
    picker_people_by_position = {
        position_id: {
            person.id: person
            for group in view.groups
            for person in group[1]
        }
        for position_id, view in picker_views.items()
    }
    assignments_by_slot = {assignment.slot_key: assignment for assignment in assignments}
    applications_by_slot: dict[uuid.UUID, list[dict[str, object]]] = {}
    for application, person in application_rows:
        assignment = assignments_by_slot.get(application.slot_key)
        picker_person = (
            picker_people_by_position.get(assignment.base_position_id, {}).get(person.id)
            if assignment
            else None
        )
        applications_by_slot.setdefault(application.slot_key, []).append(
            {
                "application": application,
                "person": person,
                "advisory": picker_person.hint if picker_person else "Eligibility reviewed on selection",
                "same_date": picker_person.same_date if picker_person else False,
                "leave": (
                    picker_person.leave_label
                    if picker_person and picker_person.on_leave
                    else ""
                ),
            }
        )
    generated_travel = None
    if workday.operation_id:
        generated_workday = db.scalar(
            select(Workday).where(
                Workday.operation_id == workday.operation_id,
                Workday.category == WorkdayCategory.TRAVEL_DAY.value,
                Workday.id != workday.id,
            )
        )
        if generated_workday:
            generated_travel = db.get(
                WorkdayRevision,
                generated_workday.current_draft_revision_id
                or generated_workday.current_published_revision_id,
            )
    builder_error = extra.pop("builder_error", request.query_params.get("error"))
    editable_regions = _editable_regions(db, request)
    region = db.get(Region, workday.region_id)
    source_event = (
        db.get(ExternalCalendarEvent, workday.external_event_id)
        if workday.external_event_id
        else None
    )
    programme_changed = bool(
        source_event
        and (
            (source_event.meeting_name and source_event.meeting_name != draft.title)
            or any(
                getattr(source_event, field) is not None
                and getattr(source_event, field) != getattr(draft, field)
                for field in ("first_race_time", "last_race_time", "race_count")
            )
        )
    )
    return context(
        request,
        new_mode=False,
        workday=workday,
        draft=draft,
        regions=editable_regions,
        day_type_options=_day_type_options(),
        tracks=list(
            db.scalars(
                select(Track)
                .where(Track.region_id == workday.region_id, Track.lifecycle == "ACTIVE")
                .order_by(Track.name)
            )
        ),
        positions=sorted(
            db.scalars(
                select(BasePosition).where(BasePosition.lifecycle == "ACTIVE").order_by(BasePosition.name)
            ), key=catalog_position_order
        ),
        assignments=assignments,
        people_groups_by_assignment=people_groups_by_assignment,
        assignment_leave_by_id=assignment_leave_by_id,
        statuses=[item.value for item in AssignmentStatus],
        applications_by_slot=applications_by_slot,
        vehicles=_builder_vehicles(db, request, editable_regions, workday.region_id),
        vehicle_region_names={region.id: region.name for region in editable_regions},
        transport_labels=TRANSPORT_LABELS,
        setup_lead_minutes=region.lead_minutes_race_day if region else 120,
        generated_travel=generated_travel,
        source_event=source_event,
        programme_changed=programme_changed,
        builder_error=builder_error,
        **extra,
    )


@router.get("/workdays/{workday_id}/crew-picker", response_class=JSONResponse)
def workday_crew_picker(
    workday_id: uuid.UUID,
    position_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db),
):
    workday = db.get(Workday, workday_id)
    if not workday:
        raise HTTPException(404)
    require_manage_region(request.state.actor, workday.region_id)
    draft = db.get(WorkdayRevision, workday.current_draft_revision_id)
    if not draft:
        raise HTTPException(409, "Open the editor again to create a draft")
    try:
        view = crew_picker_views(
            db,
            region_id=workday.region_id,
            work_date=draft.work_date,
            exclude_workday_id=workday.id,
            position_ids={position_id},
        )[position_id]
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return JSONResponse(
        {
            "groups": [
                {
                    "label": label,
                    "people": [
                        {
                            "id": str(person.id),
                            "label": person.display_name,
                            "hint": person.hint,
                            "context": person.context_label,
                            "same_date": person.same_date,
                            "on_leave": person.on_leave,
                            "leave_start": person.leave_start.isoformat() if person.leave_start else None,
                            "leave_end": person.leave_end.isoformat() if person.leave_end else None,
                            "leave_label": person.leave_label,
                        }
                        for person in people
                    ],
                }
                for label, people in view.groups
            ]
        }
    )


@router.get("/new-workday/crew-picker", response_class=JSONResponse)
def new_workday_crew_picker(
    region_id: uuid.UUID,
    work_date: date,
    position_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db),
):
    require_manage_region(request.state.actor, region_id)
    try:
        view = crew_picker_views(
            db,
            region_id=region_id,
            work_date=work_date,
            position_ids={position_id},
        )[position_id]
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return JSONResponse(
        {
            "groups": [
                {
                    "label": label,
                    "people": [
                        {
                            "id": str(person.id),
                            "label": person.display_name,
                            "hint": person.hint,
                            "context": person.context_label,
                            "same_date": person.same_date,
                            "on_leave": person.on_leave,
                            "leave_start": person.leave_start.isoformat() if person.leave_start else None,
                            "leave_end": person.leave_end.isoformat() if person.leave_end else None,
                            "leave_label": person.leave_label,
                        }
                        for person in people
                    ],
                }
                for label, people in view.groups
            ]
        }
    )


def _publication_warnings(
    db: Session, workday: Workday, draft: WorkdayRevision
) -> list[str]:
    assignments = list(
        db.scalars(select(Assignment).where(Assignment.revision_id == draft.id))
    )
    warnings: list[str] = []
    if draft.track_id is None:
        warnings.append("Track is still to be confirmed.")
    if draft.start_time is None:
        warnings.append("Workday start is not set.")
    if any(row.status == "TBC" for row in assignments):
        warnings.append("One or more positions are Unassigned.")
    if any(row.status == "OPEN" for row in assignments):
        warnings.append("One or more positions are open for applications.")
    if any(row.status == "MANAGER_ACTION_REQUIRED" for row in assignments):
        warnings.append("One or more positions require Manager action.")
    person_ids = {row.person_id for row in assignments if row.person_id}
    linked = set(
        db.scalars(
            select(UserPersonLink.person_id).where(UserPersonLink.person_id.in_(person_ids))
        )
    ) if person_ids else set()
    if person_ids - linked:
        warnings.append("One or more assigned people do not have a linked app account.")
    leave_by_person = active_for_people_on_date(db, person_ids, draft.work_date)
    if leave_by_person:
        names = list(
            db.scalars(
                select(Person.display_name)
                .where(Person.id.in_(leave_by_person))
                .order_by(Person.display_name)
            )
        )
        warnings.append(f"{', '.join(names)} {'is' if len(names) == 1 else 'are'} on leave on this Workday.")
    eligibility_by_pair = bulk_eligibility(
        db,
        {row.person_id for row in assignments if row.person_id is not None},
        {
            row.base_position_id
            for row in assignments
            if row.base_position_id is not None
        },
    )
    if any(
        row.person_id
        and row.base_position_id
        and not eligibility_by_pair[(row.person_id, row.base_position_id)][0]
        for row in assignments
    ):
        warnings.append("One or more assignments have a capability conflict.")
    elsewhere = bool(
        person_ids
        and db.scalar(
            select(Assignment.id)
            .join(WorkdayRevision, WorkdayRevision.id == Assignment.revision_id)
            .join(Workday, Workday.current_published_revision_id == WorkdayRevision.id)
            .where(
                Workday.id != workday.id,
                WorkdayRevision.work_date == draft.work_date,
                Assignment.person_id.in_(person_ids),
            )
            .limit(1)
        )
    )
    if elsewhere:
        warnings.append("A person is also rostered on another workday on this date.")
    if workday.category == WorkdayCategory.RACE_DAY.value and any(
        value is None
        for value in (
            draft.on_track_time,
            draft.first_race_time,
            draft.last_race_time,
            draft.race_count,
            draft.end_time,
        )
    ):
        warnings.append("Race Day timing is incomplete; publication is still allowed.")
    return warnings


def _managed_region_ids(request: Request) -> set[uuid.UUID] | None:
    actor = request.state.actor
    return None if actor.is_admin else {
        region_id for region_id in actor.regional_roles if can_manage_region(actor, region_id)
    }


@router.get("/workdays/{workday_id}", response_class=HTMLResponse)
def edit_workday(workday_id: uuid.UUID, request: Request, db: Session = Depends(get_db)):
    workday = db.get(Workday, workday_id)
    if not workday:
        raise HTTPException(404)
    require_manage_region(request.state.actor, workday.region_id)
    if workday.generated_from_workday_id:
        return RedirectResponse(f"/manage/workdays/{workday.generated_from_workday_id}", status_code=303)
    draft = ensure_draft(db, workday, request.state.user.id)
    return templates.TemplateResponse("workday_builder.html", _builder_context(db, request, workday, draft))


@router.post("/workdays/{workday_id}/apply-latest-programme")
def apply_latest_programme(
    workday_id: uuid.UUID,
    request: Request,
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
):
    verify_csrf(request, csrf_token)
    workday = db.get(Workday, workday_id)
    if not workday:
        raise HTTPException(404)
    try:
        apply_latest_programme_to_draft(db, workday, request.state.actor)
        record_audit(
            db,
            "workday.programme_applied",
            "workday",
            workday.id,
            request.state.user.id,
            region_id=workday.region_id,
        )
        db.commit()
    except ValueError as exc:
        db.rollback()
        raise HTTPException(409, str(exc)) from exc
    return RedirectResponse(f"/manage/workdays/{workday.id}?programme=applied", status_code=303)


@router.post("/workdays/{workday_id}/status")
def update_workday_status(
    workday_id: uuid.UUID,
    request: Request,
    status: str = Form(...),
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
):
    verify_csrf(request, csrf_token)
    workday = db.get(Workday, workday_id)
    if not workday:
        raise HTTPException(404)
    require_manage_region(request.state.actor, workday.region_id)
    if status not in {item.value for item in WorkdayStatus}:
        raise HTTPException(400, "Invalid Workday status")
    if status == WorkdayStatus.SCHEDULED.value and db.scalar(
        select(Workday.id).where(Workday.rescheduled_from_workday_id == workday.id)
    ):
        raise HTTPException(409, "A Workday with a replacement cannot be reinstated.")
    before = workday.status
    workday.status = status
    published = db.get(WorkdayRevision, workday.current_published_revision_id)
    generated = db.scalar(
        select(Workday).where(Workday.generated_from_workday_id == workday.id)
    )
    if generated:
        generated_status = (
            status
            if status in {WorkdayStatus.CANCELLED.value, WorkdayStatus.ABANDONED.value}
            else (
                WorkdayStatus.SCHEDULED.value
                if published and published.standard_travel_enabled
                else WorkdayStatus.CANCELLED.value
            )
        )
        if generated.status != generated_status:
            generated_before = generated.status
            generated.status = generated_status
            generated.lock_version += 1
            record_audit(
                db,
                "workday.status_changed",
                "workday",
                generated.id,
                request.state.user.id,
                region_id=generated.region_id,
                detail={"from": generated_before, "to": generated_status, "generated": True},
            )
    record_audit(
        db,
        "workday.status_changed",
        "workday",
        workday.id,
        request.state.user.id,
        region_id=workday.region_id,
        detail={"from": before, "to": status},
    )
    record_event(
        db,
        event_key=f"workday-status:{workday.id}:{before}:{status}:{workday.lock_version}",
        event_type="ROSTER_PUBLISHED",
        region_id=workday.region_id,
        workday_id=workday.id,
        payload={
            "revision_id": str(workday.current_published_revision_id or ""),
            "previous_revision_id": None,
            "status": status,
            "summary": (
                f"{published.track_name_snapshot or published.title} roster for "
                f"{published.work_date:%d %b %Y} was "
                f"{'reinstated' if status == WorkdayStatus.SCHEDULED.value else status.lower()}."
                if published
                else f"Roster status changed to {status.lower()}."
            ),
        },
    )
    workday.lock_version += 1
    db.commit()
    return RedirectResponse(f"/day/{workday.id}", status_code=303)


@router.post("/workdays/{workday_id}/reschedule")
def reschedule_workday(
    workday_id: uuid.UUID,
    request: Request,
    new_date: date = Form(...),
    confirm_move: bool = Form(False),
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
):
    verify_csrf(request, csrf_token)
    workday = db.get(Workday, workday_id)
    if not workday:
        raise HTTPException(404)
    require_manage_region(request.state.actor, workday.region_id)
    if workday.generated_from_workday_id:
        return RedirectResponse(
            f"/day/{workday.generated_from_workday_id}?move=parent-required", status_code=303
        )
    if not confirm_move:
        raise HTTPException(400, "Confirm that the current published roster will be moved.")
    db.commit()
    try:
        replacement = reschedule_published_workday(
            db,
            workday_id=workday.id,
            new_date=new_date,
            actor_user_id=request.state.user.id,
        )
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return RedirectResponse(f"/day/{replacement.id}?moved=1", status_code=303)


@router.post("/workdays/{workday_id}/draft")
async def save_workday_draft(
    workday_id: uuid.UUID, request: Request, db: Session = Depends(get_db)
):
    form = await request.form()
    verify_csrf(request, str(form.get("csrf_token", "")))
    workday = db.get(Workday, workday_id)
    if not workday:
        raise HTTPException(404)
    require_manage_region(request.state.actor, workday.region_id)
    draft = db.get(WorkdayRevision, workday.current_draft_revision_id)
    if not draft:
        raise HTTPException(409, "Open the editor again to create a draft")

    try:
        category, discipline = _parse_day_type(
            str(form.get("day_type", f"{workday.category}:{workday.racing_discipline or ''}"))
        )
        details, items = _draft_payload(
            form, category=category, discipline=discipline, draft=draft
        )
        save_draft(
            db,
            workday_id=workday.id,
            draft_id=draft.id,
            expected_version=int(str(form["expected_version"])),
            details=details,
            assignments=items,
        )
    except DraftConflict as exc:
        db.rollback()
        return templates.TemplateResponse(
            "workday_builder.html",
            _builder_context(db, request, workday, draft, builder_error=str(exc)),
            status_code=409,
        )
    except (KeyError, TypeError, ValueError) as exc:
        db.rollback()
        return templates.TemplateResponse(
            "workday_builder.html",
            _builder_context(db, request, workday, draft, builder_error=str(exc)),
            status_code=400,
        )
    return RedirectResponse(f"/manage/workdays/{workday_id}/preview", status_code=303)


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
    first_trial_time: str = Form(""),
    first_race_time: str = Form(""),
    last_race_time: str = Form(""),
    race_count: str = Form(""),
    day_note: str = Form(""),
    change_reason: str = Form(""),
    expected_version: int = Form(...),
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
    try:
        count = int(race_count) if race_count else None
        if count is not None and not 0 <= count <= 99:
            raise ValueError("Race count must be between 0 and 99")
        update_draft_details(
            db,
            workday_id=workday.id,
            draft_id=draft.id,
            expected_version=expected_version,
            work_date=work_date,
            track_id=uuid.UUID(track_id) if track_id else None,
            title=title,
            start_time=parse_time(start_time),
            end_time=parse_time(end_time),
            on_track_time=parse_time(on_track_time),
            first_trial_time=parse_time(first_trial_time),
            first_race_time=parse_time(first_race_time),
            last_race_time=parse_time(last_race_time),
            race_count=count,
            day_note=day_note,
            change_reason=change_reason,
        )
    except DraftConflict as exc:
        raise HTTPException(409, str(exc)) from exc
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
    assignment_start_time: str = Form(""),
    assignment_end_time: str = Form(""),
    expected_version: int = Form(...),
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
            workday_id=workday.id,
            draft_id=draft.id,
            expected_version=expected_version,
            item=AssignmentInput(
                base_position_id=uuid.UUID(base_position_id) if base_position_id else None,
                slot_index=int(slot_index) if slot_index else None,
                person_id=uuid.UUID(person_id) if person_id else None,
                status=status,
                note=note,
                note_private=note_private,
                start_time=parse_time(assignment_start_time),
                end_time=parse_time(assignment_end_time),
            ),
        )
    except DraftConflict as exc:
        raise HTTPException(409, str(exc)) from exc
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
    assignment_start_time: str = Form(""),
    assignment_end_time: str = Form(""),
    expected_version: int = Form(...),
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
            workday_id=workday.id,
            draft_id=draft.id,
            expected_version=expected_version,
            assignment_id=assignment_id,
            person_id=uuid.UUID(person_id) if person_id else None,
            status=status,
            note=note,
            note_private=note_private,
            start_time=parse_time(assignment_start_time),
            end_time=parse_time(assignment_end_time),
        )
    except DraftConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return RedirectResponse(f"/manage/workdays/{workday_id}#assignments", status_code=303)


@router.post("/workdays/{workday_id}/assignments/{assignment_id}/remove")
def delete_assignment(
    workday_id: uuid.UUID,
    assignment_id: uuid.UUID,
    request: Request,
    expected_version: int = Form(...),
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
        remove_assignment(
            db,
            workday_id=workday.id,
            draft_id=draft.id,
            expected_version=expected_version,
            assignment_id=assignment_id,
        )
    except DraftConflict as exc:
        raise HTTPException(409, str(exc)) from exc
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
        _builder_context(
            db,
            request,
            workday,
            draft,
            changes=preview_diff(db, workday, draft),
            warnings=_publication_warnings(db, workday, draft),
            conflicts=publication_conflicts(
                db, workday, draft, visible_region_ids=_managed_region_ids(request)
            ),
        ),
    )


@router.post("/workdays/{workday_id}/publish")
def publish_workday(
    workday_id: uuid.UUID,
    request: Request,
    draft_id: uuid.UUID = Form(...),
    expected_version: int = Form(...),
    csrf_token: str = Form(...),
    confirm_conflicts: bool = Form(False),
    db: Session = Depends(get_db),
):
    verify_csrf(request, csrf_token)
    workday = db.get(Workday, workday_id)
    if not workday:
        raise HTTPException(404)
    require_manage_region(request.state.actor, workday.region_id)
    db.commit()
    try:
        publish(
            db,
            workday_id,
            draft_id,
            request.state.user.id,
            expected_version,
            confirm_conflicts=confirm_conflicts,
        )
    except PublishConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    return RedirectResponse(f"/day/{workday_id}", status_code=303)
