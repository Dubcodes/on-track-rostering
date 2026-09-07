from __future__ import annotations

import uuid
from datetime import date, time

import pytest
from sqlalchemy import select

from app.auth.policy import Actor, can_crew_view, can_manage_region
from app.auth.security import credential_error, is_safe_next
from app.catalog.models import BasePosition, CrewGroup, Region, Track
from app.catalog.service import close_colour_warnings
from app.core.enums import (
    AssignmentStatus,
    CapabilitySignal,
    DeclinePolicy,
    OpenApplicationStatus,
    Role,
    WorkdayCategory,
)
from app.core.holidays import holiday_for_date
from app.core.time import worked_minutes
from app.employee.read_models import day_assignments, month_items
from app.identity.models import Person, RoleGrant, User, UserPersonLink
from app.notifications.models import NotificationEvent
from app.open_positions.service import apply_for_position, available_positions, select_application
from app.positions.service import eligibility, set_signal
from app.rostering.models import Assignment, PositionCapability, WorkdayRevision
from app.rostering.service import (
    AssignmentInput,
    add_assignment,
    create_workday,
    decline_published_assignment,
    ensure_draft,
    publish,
)


def actor(user: User, person: Person | None, region: Region, role: Role) -> Actor:
    return Actor(
        user_id=user.id,
        person_id=person.id if person else None,
        global_roles=frozenset({role.value}) if role == Role.ADMIN else frozenset(),
        regional_roles={} if role == Role.ADMIN else {region.id: frozenset({role.value})},
    )


def seed_vertical(db):  # type: ignore[no-untyped-def]
    region = Region(name="Northern")
    group = CrewGroup(name="OB Crew")
    db.add_all([region, group])
    db.flush()
    track = Track(name="Ellerslie", region_id=region.id, display_colour="#C33D52")
    position = BasePosition(name="CCU", crew_group_id=group.id)
    person = Person(display_name="Amy Crew", email="amy@example.test", home_region_id=region.id)
    manager = User(email="manager@example.test", display_name="Manager", credential_hash="unused")
    employee = User(email="amy@example.test", display_name="Amy", credential_hash="unused")
    viewer = User(email="viewer@example.test", display_name="Viewer", credential_hash="unused")
    db.add_all([track, position, person, manager, employee, viewer])
    db.flush()
    db.add_all(
        [
            UserPersonLink(user_id=employee.id, person_id=person.id),
            RoleGrant(user_id=manager.id, role=Role.MANAGER.value, region_id=region.id),
            RoleGrant(user_id=employee.id, role=Role.EMPLOYEE.value, region_id=region.id),
            RoleGrant(user_id=viewer.id, role=Role.VIEWER.value, region_id=region.id),
        ]
    )
    db.commit()
    return region, track, position, person, manager, employee, viewer


def test_credential_contract_and_safe_redirects() -> None:
    assert credential_error("123456", Role.EMPLOYEE.value) == ""
    assert credential_error("12345", Role.EMPLOYEE.value)
    assert credential_error("12345678", Role.ADMIN.value) == ""
    assert credential_error("a strong admin password", Role.ADMIN.value) == ""
    assert credential_error("short", Role.ADMIN.value)
    assert is_safe_next("/month?year=2026")
    assert not is_safe_next("//evil.test")
    assert not is_safe_next("https://evil.test")


def test_hours_have_no_break_deduction_and_support_overnight() -> None:
    assert worked_minutes(date(2026, 9, 8), time(6, 30), time(19, 15)) == 765
    assert worked_minutes(date(2026, 9, 8), time(22, 0), time(2, 0)) == 240


def test_public_holiday_marker() -> None:
    assert holiday_for_date(date(2026, 2, 6)) == "Waitangi Day"
    assert holiday_for_date(date(2026, 7, 10)) == "Matariki"


def test_track_colour_warning_is_regional_not_global() -> None:
    northern, central = uuid.uuid4(), uuid.uuid4()
    tracks = [
        Track(name="A", region_id=northern, display_colour="#CC0000", lifecycle="ACTIVE"),
        Track(name="B", region_id=northern, display_colour="#CD0002", lifecycle="ACTIVE"),
        Track(name="C", region_id=central, display_colour="#CC0000", lifecycle="ACTIVE"),
    ]
    warnings = close_colour_warnings(tracks)
    assert len(warnings) == 1
    assert "A and B" in warnings[0]


def test_roles_are_capability_and_scope_not_numeric() -> None:
    region = Region(id=uuid.uuid4(), name="Northern")
    user = User(id=uuid.uuid4(), email="x@y.test", display_name="X", credential_hash="x")
    viewer = actor(user, None, region, Role.VIEWER)
    manager = actor(user, None, region, Role.MANAGER)
    contractor = actor(user, None, region, Role.CONTRACTOR)
    assert can_crew_view(viewer, region.id)
    assert not can_manage_region(viewer, region.id)
    assert can_manage_region(manager, region.id)
    assert not can_crew_view(contractor, region.id)


def test_base_position_eligibility_precedence(db) -> None:  # type: ignore[no-untyped-def]
    region, _track, position, person, manager, *_ = seed_vertical(db)
    set_signal(db, person.id, position.id, CapabilitySignal.WORKED.value, manager.id)
    db.commit()
    assert eligibility(db, person.id, position.id) == (True, "Worked before")
    set_signal(db, person.id, position.id, CapabilitySignal.EMPLOYEE_OPT_OUT.value, manager.id)
    db.commit()
    assert eligibility(db, person.id, position.id) == (False, "Employee opted out")
    set_signal(db, person.id, position.id, CapabilitySignal.MANAGER_BLOCK.value, manager.id)
    db.commit()
    assert eligibility(db, person.id, position.id) == (False, "Manager restricted")


def test_publish_vertical_path_and_draft_isolation(db) -> None:  # type: ignore[no-untyped-def]
    region, track, position, person, manager, employee, _viewer = seed_vertical(db)
    workday = create_workday(
        db,
        region_id=region.id,
        category=WorkdayCategory.RACE_DAY.value,
        work_date=date(2026, 9, 14),
        track_id=track.id,
        title="Ellerslie Race Day",
        actor_user_id=manager.id,
    )
    draft = db.get(WorkdayRevision, workday.current_draft_revision_id)
    add_assignment(
        db,
        draft,
        AssignmentInput(
            base_position_id=position.id,
            slot_index=1,
            person_id=person.id,
            status=AssignmentStatus.ASSIGNED.value,
            note="Meet at truck",
            note_private=True,
        ),
    )
    assert not db.scalars(select(PositionCapability)).all(), "draft must not teach history"
    db.commit()
    publish(db, workday.id, draft.id, manager.id)
    db.refresh(workday)
    assert workday.current_published_revision_id == draft.id
    assert db.scalar(select(PositionCapability.signal)) == CapabilitySignal.WORKED.value
    employee_actor = actor(employee, person, region, Role.EMPLOYEE)
    rows = month_items(db, employee_actor, date(2026, 9, 1), date(2026, 10, 1))
    assert rows[0]["track"] == "Ellerslie"
    assert rows[0]["role"] == "CCU 1"
    second_draft = ensure_draft(db, workday, manager.id)
    second_draft.track_name_snapshot = "Draft-only track"
    db.commit()
    assert month_items(db, employee_actor, date(2026, 9, 1), date(2026, 10, 1))[0]["track"] == "Ellerslie"
    unrelated = create_workday(
        db,
        region_id=region.id,
        category=WorkdayCategory.RACE_DAY.value,
        work_date=date(2026, 9, 15),
        track_id=track.id,
        title="Regional day without Amy",
        actor_user_id=manager.id,
    )
    unrelated_draft = db.get(WorkdayRevision, unrelated.current_draft_revision_id)
    db.commit()
    publish(db, unrelated.id, unrelated_draft.id, manager.id)
    db.commit()
    personal_month = month_items(db, employee_actor, date(2026, 9, 1), date(2026, 10, 1))
    assert [row["title"] for row in personal_month] == ["Ellerslie Race Day"]


def test_private_assignment_note_not_leaked_to_viewer(db) -> None:  # type: ignore[no-untyped-def]
    region, track, position, person, manager, employee, viewer = seed_vertical(db)
    workday = create_workday(
        db,
        region_id=region.id,
        category=WorkdayCategory.RACE_DAY.value,
        work_date=date(2026, 9, 14),
        track_id=track.id,
        title="Race Day",
        actor_user_id=manager.id,
    )
    draft = db.get(WorkdayRevision, workday.current_draft_revision_id)
    add_assignment(
        db,
        draft,
        AssignmentInput(
            base_position_id=position.id,
            slot_index=2,
            person_id=person.id,
            status=AssignmentStatus.ASSIGNED.value,
            note="Private transport detail",
            note_private=True,
        ),
    )
    db.commit()
    publish(db, workday.id, draft.id, manager.id)
    own_rows = day_assignments(db, actor(employee, person, region, Role.EMPLOYEE), draft, False)
    viewer_rows = day_assignments(db, actor(viewer, None, region, Role.VIEWER), draft, False)
    manager_rows = day_assignments(db, actor(manager, None, region, Role.MANAGER), draft, True)
    assert own_rows[0]["note"] == "Private transport detail"
    assert viewer_rows[0]["note"] == ""
    assert manager_rows[0]["note"] == "Private transport detail"


def test_open_position_apply_select_and_publish(db) -> None:  # type: ignore[no-untyped-def]
    region, track, position, person, manager, employee, _viewer = seed_vertical(db)
    set_signal(db, person.id, position.id, CapabilitySignal.MANAGER_ALLOW.value, manager.id)
    workday = create_workday(
        db,
        region_id=region.id,
        category=WorkdayCategory.RACE_DAY.value,
        work_date=date(2026, 9, 20),
        track_id=track.id,
        title="Open roster",
        actor_user_id=manager.id,
    )
    first_draft = db.get(WorkdayRevision, workday.current_draft_revision_id)
    open_slot = add_assignment(
        db,
        first_draft,
        AssignmentInput(
            base_position_id=position.id,
            slot_index=2,
            person_id=None,
            status=AssignmentStatus.OPEN.value,
        ),
    )
    db.commit()
    published = publish(db, workday.id, first_draft.id, manager.id)
    employee_actor = actor(employee, person, region, Role.EMPLOYEE)
    assert [item.assignment.slot_key for item in available_positions(db, employee_actor)] == [
        open_slot.slot_key
    ]
    db.commit()
    application = apply_for_position(
        db, actor=employee_actor, workday_id=workday.id, slot_key=open_slot.slot_key
    )
    duplicate = apply_for_position(
        db, actor=employee_actor, workday_id=workday.id, slot_key=open_slot.slot_key
    )
    assert duplicate.id == application.id
    draft = ensure_draft(db, workday, manager.id)
    select_application(db, workday=workday, draft=draft, application=application)
    db.commit()
    publish(db, workday.id, draft.id, manager.id)
    db.refresh(application)
    assert application.status == OpenApplicationStatus.ACCEPTED.value
    assigned = db.scalar(
        select(Assignment).where(
            Assignment.revision_id == draft.id, Assignment.slot_key == open_slot.slot_key
        )
    )
    assert assigned.person_id == person.id
    assert db.get(NotificationEvent, f"roster-published:{workday.id}:{published.id}")


@pytest.mark.parametrize(
    ("policy", "expected_status", "event_type"),
    [
        (DeclinePolicy.OPEN_IMMEDIATELY.value, AssignmentStatus.OPEN.value, "OPEN_POSITION_AVAILABLE"),
        (
            DeclinePolicy.MANAGER_REVIEW.value,
            AssignmentStatus.MANAGER_ACTION_REQUIRED.value,
            "MANAGER_ACTION_REQUIRED",
        ),
    ],
)
def test_employee_decline_creates_new_immutable_publication(
    db, policy: str, expected_status: str, event_type: str
) -> None:  # type: ignore[no-untyped-def]
    region, track, position, person, manager, employee, _viewer = seed_vertical(db)
    region.decline_policy = policy
    workday = create_workday(
        db,
        region_id=region.id,
        category=WorkdayCategory.RACE_DAY.value,
        work_date=date(2026, 9, 22),
        track_id=track.id,
        title="Decline test",
        actor_user_id=manager.id,
    )
    draft = db.get(WorkdayRevision, workday.current_draft_revision_id)
    assignment = add_assignment(
        db,
        draft,
        AssignmentInput(
            base_position_id=position.id,
            slot_index=1,
            person_id=person.id,
            status=AssignmentStatus.ASSIGNED.value,
        ),
    )
    db.commit()
    old_publication = publish(db, workday.id, draft.id, manager.id)
    db.commit()
    new_publication = decline_published_assignment(
        db,
        workday_id=workday.id,
        slot_key=assignment.slot_key,
        person_id=person.id,
        actor_user_id=employee.id,
    )
    old_row = db.scalar(
        select(Assignment).where(
            Assignment.revision_id == old_publication.id,
            Assignment.slot_key == assignment.slot_key,
        )
    )
    new_row = db.scalar(
        select(Assignment).where(
            Assignment.revision_id == new_publication.id,
            Assignment.slot_key == assignment.slot_key,
        )
    )
    assert old_row.person_id == person.id
    assert new_row.person_id is None and new_row.status == expected_status
    event = db.scalar(
        select(NotificationEvent).where(
            NotificationEvent.workday_id == workday.id,
            NotificationEvent.event_type == event_type,
        )
    )
    assert event is not None
