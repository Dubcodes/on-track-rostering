from __future__ import annotations

import uuid
from datetime import date, time, timedelta

import pytest
from sqlalchemy import select

from app.auth.policy import Actor, can_crew_view, can_manage_region
from app.auth.security import credential_error, is_safe_next
from app.catalog.models import BasePosition, CrewGroup, PersonCrewGroup, Region, Track, Vehicle
from app.core.enums import (
    AssignmentStatus,
    CapabilitySignal,
    DeclinePolicy,
    OpenApplicationStatus,
    Role,
    WorkdayCategory,
    WorkdayStatus,
)
from app.core.holidays import holiday_for_date
from app.core.themes import DEFAULT_THEME, normalize_theme
from app.core.time import local_today, worked_minutes
from app.employee.read_models import day_assignments, month_items
from app.identity.models import Person, RoleGrant, User, UserPersonLink
from app.notifications.models import NotificationEvent
from app.open_positions.service import apply_for_position, available_positions, select_application
from app.positions.service import eligibility, set_signal
from app.rostering.builder_read import crew_picker_views
from app.rostering.models import Assignment, PositionCapability, Workday, WorkdayRevision
from app.rostering.service import (
    AssignmentInput,
    DraftAssignmentInput,
    DraftConflict,
    DraftDetailsInput,
    add_assignment,
    create_workday,
    decline_published_assignment,
    ensure_draft,
    publish,
    remove_assignment,
    save_draft,
    update_assignment,
    update_draft_details,
)
from app.unavailability.models import PersonUnavailability


def test_aurora_is_default_without_overwriting_valid_or_legacy_preferences() -> None:
    assert DEFAULT_THEME == "aurora"
    assert normalize_theme(None) == "aurora"
    assert normalize_theme("") == "aurora"
    assert normalize_theme("not-a-theme") == "aurora"
    assert normalize_theme("daylight") == "daylight"
    assert normalize_theme("race-night") == "race-night"
    assert normalize_theme("trackside") == "jade"


def draft_details(draft: WorkdayRevision, **changes) -> DraftDetailsInput:  # type: ignore[no-untyped-def]
    values = {
        "work_date": draft.work_date,
        "track_id": draft.track_id,
        "title": draft.title,
        "start_time": draft.start_time,
        "end_time": draft.end_time,
        "on_track_time": draft.on_track_time,
        "first_trial_time": draft.first_trial_time,
        "first_race_time": draft.first_race_time,
        "last_race_time": draft.last_race_time,
        "race_count": draft.race_count,
        "day_note": draft.day_note,
        "change_reason": draft.change_reason,
    }
    values.update(changes)
    return DraftDetailsInput(**values)


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
    track = Track(name="Ellerslie", region_id=region.id, palette_slot=1)
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


def add_current(db, workday: Workday, draft: WorkdayRevision, item: AssignmentInput):  # type: ignore[no-untyped-def]
    return add_assignment(
        db,
        workday_id=workday.id,
        draft_id=draft.id,
        expected_version=workday.lock_version,
        item=item,
    )


def publish_current(
    db, workday: Workday, draft: WorkdayRevision, actor_user_id: uuid.UUID
):  # type: ignore[no-untyped-def]
    return publish(db, workday.id, draft.id, actor_user_id, workday.lock_version)


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


def test_track_token_is_theme_independent() -> None:
    from app.catalog.presentation import track_token

    assert track_token(7) == "track-07"
    assert track_token(None) == "unconfirmed"
    assert track_token(7, "OFFICE_DAY") == "office"
    assert track_token(7, "TRAINING_DAY") == "training"
    assert track_token(7, "TRAVEL_DAY") == "track-07"


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
    add_current(
        db,
        workday,
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
    publish_current(db, workday, draft, manager.id)
    db.refresh(workday)
    assert workday.current_published_revision_id == draft.id
    capability = db.scalar(select(PositionCapability))
    assert capability.signal == CapabilitySignal.WORKED.value
    assert eligibility(db, person.id, position.id) == (False, "No capability signal")
    employee_actor = actor(employee, person, region, Role.EMPLOYEE)
    rows = month_items(db, employee_actor, date(2026, 9, 1), date(2026, 10, 1))
    assert rows[0]["track"] == "Ellerslie"
    assert rows[0]["role"] == "CCU 1"
    second_draft = ensure_draft(db, workday, manager.id)
    second_draft.track_name_snapshot = "Draft-only track"
    db.commit()
    assert month_items(db, employee_actor, date(2026, 9, 1), date(2026, 10, 1))[0]["track"] == "Ellerslie"
    manager_rows = month_items(
        db, actor(manager, None, region, Role.MANAGER), date(2026, 9, 1), date(2026, 10, 1)
    )
    assert manager_rows == [], "an unlinked Manager has no invented personal calendar"
    private_draft = create_workday(
        db,
        region_id=region.id,
        category=WorkdayCategory.TRAINING_DAY.value,
        work_date=date(2026, 9, 16),
        track_id=None,
        title="Private planning day",
        actor_user_id=manager.id,
    )
    db.commit()
    manager_rows = month_items(
        db, actor(manager, None, region, Role.MANAGER), date(2026, 9, 1), date(2026, 10, 1)
    )
    assert manager_rows == []
    assert all(
        row["id"] != str(private_draft.id)
        for row in month_items(db, employee_actor, date(2026, 9, 1), date(2026, 10, 1))
    )
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
    publish_current(db, unrelated, unrelated_draft, manager.id)
    db.commit()
    personal_month = month_items(db, employee_actor, date(2026, 9, 1), date(2026, 10, 1))
    assert [row["title"] for row in personal_month] == ["Ellerslie Race Day"]

    blocked_position = BasePosition(name="Blocked personal month role")
    db.add(blocked_position)
    db.flush()
    set_signal(
        db,
        person.id,
        position.id,
        CapabilitySignal.EMPLOYEE_ALLOW.value,
        manager.id,
    )
    set_signal(
        db,
        person.id,
        blocked_position.id,
        CapabilitySignal.MANAGER_BLOCK.value,
        manager.id,
    )
    for day, title, open_position in (
        (17, "Eligible open day", position),
        (18, "Blocked open day", blocked_position),
    ):
        open_day = create_workday(
            db,
            region_id=region.id,
            category=WorkdayCategory.RACE_DAY.value,
            work_date=date(2026, 9, day),
            track_id=track.id,
            title=title,
            actor_user_id=manager.id,
        )
        open_draft = db.get(WorkdayRevision, open_day.current_draft_revision_id)
        add_current(
            db,
            open_day,
            open_draft,
            AssignmentInput(
                base_position_id=open_position.id,
                slot_index=1,
                person_id=None,
                status=AssignmentStatus.OPEN.value,
            ),
        )
        publish_current(db, open_day, open_draft, manager.id)
    db.commit()

    for role in (Role.EMPLOYEE, Role.SUB_MANAGER, Role.MANAGER, Role.VIEWER):
        personal_actor = Actor(
            user_id=employee.id,
            person_id=person.id,
            global_roles=frozenset(),
            regional_roles={
                region.id: frozenset({Role.EMPLOYEE.value, role.value})
            },
        )
        role_rows = month_items(
            db,
            personal_actor,
            date(2026, 9, 1),
            date(2026, 10, 1),
        )
        assert [row["title"] for row in role_rows] == [
            "Ellerslie Race Day",
            "Eligible open day",
        ]


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
    add_current(
        db,
        workday,
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
    publish_current(db, workday, draft, manager.id)
    own_rows = day_assignments(
        db,
        actor(employee, person, region, Role.EMPLOYEE),
        draft,
        can_view_all_rows=True,
        can_view_private_notes=False,
    )
    viewer_rows = day_assignments(
        db,
        actor(viewer, None, region, Role.VIEWER),
        draft,
        can_view_all_rows=True,
        can_view_private_notes=False,
    )
    manager_rows = day_assignments(
        db,
        actor(manager, None, region, Role.MANAGER),
        draft,
        can_view_all_rows=True,
        can_view_private_notes=True,
    )
    assert own_rows[0]["note"] == "Private transport detail"
    assert viewer_rows[0]["note"] == ""
    assert manager_rows[0]["note"] == "Private transport detail"


def test_open_position_apply_select_and_publish(db) -> None:  # type: ignore[no-untyped-def]
    region, track, position, person, manager, employee, _viewer = seed_vertical(db)
    manager_id = manager.id
    position.name = "Turn"
    set_signal(db, person.id, position.id, CapabilitySignal.MANAGER_ALLOW.value, manager.id)
    workday = create_workday(
        db,
        region_id=region.id,
        category=WorkdayCategory.RACE_DAY.value,
        work_date=local_today() + timedelta(days=7),
        track_id=track.id,
        title="Open roster",
        actor_user_id=manager.id,
    )
    first_draft = db.get(WorkdayRevision, workday.current_draft_revision_id)
    open_slot = add_current(
        db,
        workday,
        first_draft,
        AssignmentInput(
            base_position_id=position.id,
            slot_index=2,
            person_id=None,
            status=AssignmentStatus.OPEN.value,
        ),
    )
    db.commit()
    published = publish_current(db, workday, first_draft, manager.id)
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
    db.add(
        PersonUnavailability(
            person_id=person.id,
            start_date=published.work_date,
            end_date=published.work_date,
            note="Manager-only note",
            created_by_user_id=manager.id,
        )
    )
    db.commit()
    draft = ensure_draft(db, workday, manager.id)
    back = BasePosition(name="Back")
    db.add(back)
    db.flush()
    already_assigned = Assignment(
        revision_id=draft.id,
        base_position_id=back.id,
        display_name_snapshot="Back",
        person_id=person.id,
        person_name_snapshot=person.display_name,
        status=AssignmentStatus.ASSIGNED.value,
    )
    db.add(already_assigned)
    db.commit()
    select_application(
        db,
        workday=workday,
        draft=draft,
        application=application,
        expected_version=workday.lock_version,
    )
    selected_positions = list(
        db.scalars(
            select(Assignment).where(
                Assignment.revision_id == draft.id,
                Assignment.person_id == person.id,
                Assignment.status == AssignmentStatus.ASSIGNED.value,
            )
        )
    )
    assert {row.display_name_snapshot for row in selected_positions} == {"Back", "Turn 2"}
    db.commit()
    db.refresh(workday)
    db.refresh(draft)
    db.commit()
    publish_current(db, workday, draft, manager_id)
    db.refresh(application)
    assert application.status == OpenApplicationStatus.ACCEPTED.value
    assigned = db.scalar(
        select(Assignment).where(
            Assignment.revision_id == draft.id, Assignment.slot_key == open_slot.slot_key
        )
    )
    assert assigned.person_id == person.id
    assert db.get(NotificationEvent, f"roster-published:{workday.id}:{published.id}")


def test_stale_shared_draft_mutations_never_overwrite_newer_work(db) -> None:  # type: ignore[no-untyped-def]
    region, track, position, person, manager, *_ = seed_vertical(db)
    workday = create_workday(
        db,
        region_id=region.id,
        category=WorkdayCategory.RACE_DAY.value,
        work_date=date(2026, 10, 1),
        track_id=track.id,
        title="Shared draft",
        actor_user_id=manager.id,
    )
    draft = db.get(WorkdayRevision, workday.current_draft_revision_id)
    stale_version = workday.lock_version
    update_draft_details(
        db,
        workday_id=workday.id,
        draft_id=draft.id,
        expected_version=stale_version,
        work_date=date(2026, 10, 2),
        track_id=track.id,
        title="Manager A title",
        start_time=None,
        end_time=None,
        on_track_time=None,
        first_trial_time=None,
        first_race_time=None,
        last_race_time=None,
        race_count=None,
        day_note="A note",
        change_reason="",
    )
    with pytest.raises(DraftConflict):
        update_draft_details(
            db,
            workday_id=workday.id,
            draft_id=draft.id,
            expected_version=stale_version,
            work_date=date(2026, 10, 3),
            track_id=track.id,
            title="Manager B stale title",
            start_time=None,
            end_time=None,
            on_track_time=None,
            first_trial_time=None,
            first_race_time=None,
            last_race_time=None,
            race_count=None,
            day_note="B stale note",
            change_reason="",
        )
    db.rollback()
    db.refresh(workday)
    db.refresh(draft)
    assert (draft.title, draft.day_note) == ("Manager A title", "A note")

    assignment = add_current(
        db,
        workday,
        draft,
        AssignmentInput(
            base_position_id=position.id,
            slot_index=1,
            person_id=person.id,
            status=AssignmentStatus.ASSIGNED.value,
            note="Current assignment",
        ),
    )
    stale_version = workday.lock_version
    update_assignment(
        db,
        workday_id=workday.id,
        draft_id=draft.id,
        expected_version=stale_version,
        assignment_id=assignment.id,
        person_id=person.id,
        status=AssignmentStatus.ASSIGNED.value,
        note="Manager A assignment",
        note_private=True,
    )
    with pytest.raises(DraftConflict):
        remove_assignment(
            db,
            workday_id=workday.id,
            draft_id=draft.id,
            expected_version=stale_version,
            assignment_id=assignment.id,
        )
    db.rollback()
    db.refresh(assignment)
    assert assignment.note == "Manager A assignment"

    stale_version = workday.lock_version
    workday_id, draft_id, manager_id = workday.id, draft.id, manager.id
    db.commit()
    publish(db, workday_id, draft_id, manager_id, stale_version)
    with pytest.raises(DraftConflict):
        update_assignment(
            db,
            workday_id=workday_id,
            draft_id=draft_id,
            expected_version=stale_version,
            assignment_id=assignment.id,
            person_id=person.id,
            status=AssignmentStatus.ASSIGNED.value,
            note="Post-publication stale edit",
            note_private=True,
        )
    db.rollback()
    db.refresh(assignment)
    assert assignment.note == "Manager A assignment"


def test_atomic_builder_save_preserves_slot_and_published_snapshot(db) -> None:  # type: ignore[no-untyped-def]
    region, track, position, person, manager, *_ = seed_vertical(db)
    replacement = BasePosition(name="Director", crew_group_id=position.crew_group_id)
    second_person = Person(display_name="Ben Crew", home_region_id=region.id)
    db.add_all([replacement, second_person])
    db.commit()
    workday = create_workday(
        db,
        region_id=region.id,
        category=WorkdayCategory.RACE_DAY.value,
        work_date=date(2026, 10, 10),
        track_id=track.id,
        title="Atomic builder",
        actor_user_id=manager.id,
    )
    first_draft = db.get(WorkdayRevision, workday.current_draft_revision_id)
    original = add_current(
        db,
        workday,
        first_draft,
        AssignmentInput(
            base_position_id=position.id,
            slot_index=1,
            person_id=person.id,
            status=AssignmentStatus.ASSIGNED.value,
            note="Published note",
            note_private=True,
        ),
    )
    original_slot = original.slot_key
    removed = add_current(
        db,
        workday,
        first_draft,
        AssignmentInput(
            base_position_id=position.id,
            slot_index=2,
            person_id=second_person.id,
            status=AssignmentStatus.ASSIGNED.value,
            note="Removed only from the next draft",
            note_private=True,
        ),
    )
    removed_slot = removed.slot_key
    publish_current(db, workday, first_draft, manager.id)
    published_id = first_draft.id
    draft = ensure_draft(db, workday, manager.id)
    current = db.scalar(
        select(Assignment).where(
            Assignment.revision_id == draft.id, Assignment.slot_key == original_slot
        )
    )
    version = workday.lock_version
    save_draft(
        db,
        workday_id=workday.id,
        draft_id=draft.id,
        expected_version=version,
        details=draft_details(
            draft,
            title="Atomic builder updated",
            start_time=time(7, 30),
            end_time=time(18, 0),
            day_note="Private draft note",
            change_reason="Crew plan changed",
        ),
        assignments=[
            DraftAssignmentInput(
                assignment_id=current.id,
                base_position_id=replacement.id,
                slot_index=2,
                person_id=person.id,
                status=AssignmentStatus.ASSIGNED.value,
                note="Updated private note",
                note_private=True,
                start_time=time(8, 0),
                end_time=time(17, 30),
            ),
            DraftAssignmentInput(
                base_position_id=position.id,
                slot_index=3,
                person_id=None,
                status=AssignmentStatus.OPEN.value,
                note="Open slot",
                note_private=False,
            ),
        ],
    )
    db.refresh(workday)
    rows = list(db.scalars(select(Assignment).where(Assignment.revision_id == draft.id)))
    changed = next(row for row in rows if row.person_id == person.id)
    opened = next(row for row in rows if row.status == AssignmentStatus.OPEN.value)
    assert workday.lock_version == version + 1
    assert all(row.slot_key != removed_slot for row in rows)
    assert changed.slot_key == original_slot
    assert (changed.base_position_id, changed.slot_index, changed.display_name_snapshot) == (
        replacement.id,
        2,
        "Director 2",
    )
    assert (changed.note, changed.note_private, changed.start_time, changed.end_time) == (
        "Updated private note",
        True,
        time(8, 0),
        time(17, 30),
    )
    assert opened.person_id is None and opened.display_name_snapshot == "CCU 3"
    published = db.scalar(
        select(Assignment).where(
            Assignment.revision_id == published_id, Assignment.slot_key == original_slot
        )
    )
    assert (published.base_position_id, published.display_name_snapshot, published.note) == (
        position.id,
        "CCU 1",
        "Published note",
    )
    assert db.scalar(
        select(Assignment).where(
            Assignment.revision_id == published_id, Assignment.slot_key == removed_slot
        )
    )
    with pytest.raises(DraftConflict):
        save_draft(
            db,
            workday_id=workday.id,
            draft_id=draft.id,
            expected_version=version,
            details=draft_details(draft, title="Stale overwrite"),
            assignments=[],
        )


def test_atomic_builder_rejects_inactive_position_before_mutation(db) -> None:  # type: ignore[no-untyped-def]
    region, track, position, person, manager, *_ = seed_vertical(db)
    inactive = BasePosition(name="Archived", crew_group_id=position.crew_group_id, lifecycle="ARCHIVED")
    db.add(inactive)
    db.commit()
    workday = create_workday(
        db,
        region_id=region.id,
        category=WorkdayCategory.RACE_DAY.value,
        work_date=date(2026, 10, 11),
        track_id=track.id,
        title="Validated builder",
        actor_user_id=manager.id,
    )
    draft = db.get(WorkdayRevision, workday.current_draft_revision_id)
    assignment = add_current(
        db,
        workday,
        draft,
        AssignmentInput(
            base_position_id=position.id,
            slot_index=1,
            person_id=None,
            status=AssignmentStatus.MANAGER_ACTION_REQUIRED.value,
        ),
    )
    version = workday.lock_version
    with pytest.raises(ValueError, match="active base position"):
        save_draft(
            db,
            workday_id=workday.id,
            draft_id=draft.id,
            expected_version=version,
            details=draft_details(draft, title="Must not persist"),
            assignments=[
                DraftAssignmentInput(
                    assignment_id=assignment.id,
                    base_position_id=inactive.id,
                    slot_index=1,
                    person_id=person.id,
                    status=AssignmentStatus.ASSIGNED.value,
                )
            ],
        )
    db.rollback()
    db.refresh(workday)
    db.refresh(draft)
    db.refresh(assignment)
    assert workday.lock_version == version
    assert draft.title == "Validated builder"
    assert assignment.status == AssignmentStatus.MANAGER_ACTION_REQUIRED.value

    save_draft(
        db,
        workday_id=workday.id,
        draft_id=draft.id,
        expected_version=version,
        details=draft_details(draft),
        assignments=[
            DraftAssignmentInput(
                assignment_id=assignment.id,
                base_position_id=position.id,
                slot_index=1,
                person_id=person.id,
                status=AssignmentStatus.ASSIGNED.value,
            )
        ],
    )
    db.refresh(assignment)
    assert assignment.status == AssignmentStatus.ASSIGNED.value


def test_truck_requires_one_explicit_qualified_driver_and_allows_capacity_override(db) -> None:  # type: ignore[no-untyped-def]
    region, track, position, driver, manager, *_ = seed_vertical(db)
    passengers = [
        Person(display_name="Passenger One", home_region_id=region.id),
        Person(display_name="Passenger Two", home_region_id=region.id),
    ]
    truck = Vehicle(
        name="Truck One",
        home_region_id=region.id,
        is_truck=True,
        seat_capacity=2,
    )
    db.add_all([*passengers, truck])
    db.commit()
    workday = create_workday(
        db,
        region_id=region.id,
        category=WorkdayCategory.RACE_DAY.value,
        work_date=date(2026, 10, 13),
        track_id=track.id,
        title="Truck validation",
        actor_user_id=manager.id,
    )
    draft = db.get(WorkdayRevision, workday.current_draft_revision_id)
    rows = [
        DraftAssignmentInput(
            base_position_id=position.id,
            slot_index=index,
            person_id=person.id,
            status=AssignmentStatus.ASSIGNED.value,
            transport_mode="VEHICLE",
            vehicle_id=truck.id,
            is_vehicle_driver=index == 1,
        )
        for index, person in enumerate([driver, *passengers], 1)
    ]
    with pytest.raises(ValueError, match="not qualified"):
        save_draft(
            db,
            workday_id=workday.id,
            draft_id=draft.id,
            expected_version=workday.lock_version,
            details=draft_details(draft),
            assignments=rows,
        )
    db.rollback()
    driver.truck_driver_qualified = True
    db.commit()
    save_draft(
        db,
        workday_id=workday.id,
        draft_id=draft.id,
        expected_version=workday.lock_version,
        details=draft_details(draft),
        assignments=rows,
    )
    saved = list(db.scalars(select(Assignment).where(Assignment.revision_id == draft.id)))
    assert len({row.person_id for row in saved}) == 3  # Deliberate 3 people / 2 seats is retained.
    assert [row.person_id for row in saved if row.is_vehicle_driver] == [driver.id]


def test_trials_atomic_save_updates_timing_and_ignores_only_untouched_new_row(db) -> None:  # type: ignore[no-untyped-def]
    region, track, _position, _person, manager, *_ = seed_vertical(db)
    workday = create_workday(
        db,
        region_id=region.id,
        category=WorkdayCategory.TRIALS.value,
        work_date=date(2026, 10, 12),
        track_id=track.id,
        title="Trials builder",
        actor_user_id=manager.id,
    )
    draft = db.get(WorkdayRevision, workday.current_draft_revision_id)
    draft.on_track_time = time(8, 30)
    draft.first_trial_time = time(9, 15)
    draft.first_race_time = time(12, 45)
    db.commit()
    version = workday.lock_version

    save_draft(
        db,
        workday_id=workday.id,
        draft_id=draft.id,
        expected_version=version,
        details=draft_details(draft, first_trial_time=time(9, 45)),
        assignments=[
            DraftAssignmentInput(
                base_position_id=None,
                slot_index=None,
                person_id=None,
                status=AssignmentStatus.TBC.value,
            )
        ],
    )
    db.refresh(draft)
    assert draft.first_trial_time == time(9, 45)
    assert draft.first_race_time == time(12, 45)
    assert not db.scalars(select(Assignment).where(Assignment.revision_id == draft.id)).all()

    db.refresh(workday)
    with pytest.raises(ValueError, match="Select a position"):
        save_draft(
            db,
            workday_id=workday.id,
            draft_id=draft.id,
            expected_version=workday.lock_version,
            details=draft_details(draft),
            assignments=[
                DraftAssignmentInput(
                    base_position_id=None,
                    slot_index=None,
                    person_id=None,
                    status=AssignmentStatus.OPEN.value,
                    note="Not an untouched row",
                )
            ],
        )


def test_position_aware_crew_picker_and_duplicate_names_are_id_safe(db) -> None:  # type: ignore[no-untyped-def]
    region, track, _position, _person, manager, *_ = seed_vertical(db)
    other_region = Region(name="Southern")
    head_on = BasePosition(name="Head On")
    director = BasePosition(name="Director")
    db.add(other_region)
    db.flush()
    remote = Person(display_name="Remote Crew", home_region_id=other_region.id)
    duplicate_a = Person(display_name="John Smith", home_region_id=other_region.id)
    duplicate_b = Person(display_name="John Smith", home_region_id=other_region.id)
    db.add_all([head_on, director, remote, duplicate_a, duplicate_b])
    db.flush()
    db.add_all(
        [
            PositionCapability(
                person_id=remote.id,
                base_position_id=head_on.id,
                signal=CapabilitySignal.MANAGER_ALLOW.value,
                changed_by_user_id=manager.id,
            ),
            PositionCapability(
                person_id=remote.id,
                base_position_id=director.id,
                signal=CapabilitySignal.MANAGER_BLOCK.value,
                changed_by_user_id=manager.id,
            ),
        ]
    )
    workday = create_workday(
        db,
        region_id=region.id,
        category=WorkdayCategory.TRIALS.value,
        work_date=date(2026, 10, 13),
        track_id=track.id,
        title="Position-aware picker",
        actor_user_id=manager.id,
    )
    draft = db.get(WorkdayRevision, workday.current_draft_revision_id)
    cancelled = Workday(
        region_id=region.id, category=WorkdayCategory.TRIALS.value,
        status=WorkdayStatus.CANCELLED.value, created_by_user_id=manager.id,
    )
    db.add(cancelled)
    db.flush()
    cancelled_revision = WorkdayRevision(
        workday_id=cancelled.id, revision_number=1, state="PUBLISHED",
        work_date=draft.work_date, created_by_user_id=manager.id,
    )
    db.add(cancelled_revision)
    db.flush()
    db.add(Assignment(
        revision_id=cancelled_revision.id, display_name_snapshot="Head On",
        person_id=remote.id, status=AssignmentStatus.ASSIGNED.value,
    ))
    cancelled.current_published_revision_id = cancelled_revision.id
    db.commit()

    views = crew_picker_views(
        db,
        region_id=workday.region_id,
        work_date=draft.work_date,
        exclude_workday_id=workday.id,
        position_ids={head_on.id, director.id},
    )
    head_other = {person.id: person for person in views[head_on.id].other}
    director_other = {person.id: person for person in views[director.id].other}
    assert head_other[remote.id].hint == "Preferred or approved"
    assert head_other[remote.id].eligible is True
    assert head_other[remote.id].same_date is False
    assert director_other[remote.id].hint == "Manager marked unavailable for this position"
    assert director_other[remote.id].eligible is False

    duplicate_options = [
        person
        for _label, people in views[head_on.id].groups
        for person in people
        if person.display_name == "John Smith"
    ]
    assert {person.id for person in duplicate_options} == {duplicate_a.id, duplicate_b.id}
    assert len({person.context_label for person in duplicate_options}) == 2
    assert all("Southern" in person.context_label for person in duplicate_options)


def test_crew_picker_dice_contractors_and_published_track_familiarity(db) -> None:  # type: ignore[no-untyped-def]
    region, track, position, familiar_person, manager, *_ = seed_vertical(db)
    other_position = BasePosition(name="Track history alternate", crew_group_id=position.crew_group_id)
    other_track = Track(name="Other history Track", region_id=region.id, palette_slot=2)
    moderate_person = Person(display_name="Moderate Track Crew", home_region_id=region.id)
    no_history_person = Person(display_name="No Track History Crew", home_region_id=region.id)
    contractor_person = Person(display_name="Manual Contractor", home_region_id=region.id)
    casual_person = Person(display_name="Manual Casual", home_region_id=region.id)
    disabled_person = Person(display_name="Disabled Employee", home_region_id=region.id)
    moderate_user = User(
        email="moderate-track@example.test", display_name=moderate_person.display_name,
        credential_hash="x",
    )
    contractor_user = User(
        email="manual-contractor@example.test", display_name=contractor_person.display_name,
        credential_hash="x",
    )
    casual_user = User(
        email="manual-casual@example.test", display_name=casual_person.display_name,
        credential_hash="x",
    )
    disabled_user = User(
        email="disabled-employee@example.test", display_name=disabled_person.display_name,
        credential_hash="x", status="DISABLED",
    )
    db.add_all(
        [
            other_position,
            other_track,
            moderate_person,
            no_history_person,
            contractor_person,
            casual_person,
            disabled_person,
            moderate_user,
            contractor_user,
            casual_user,
            disabled_user,
        ]
    )
    db.flush()
    db.add_all(
        [
            UserPersonLink(user_id=moderate_user.id, person_id=moderate_person.id),
            UserPersonLink(user_id=contractor_user.id, person_id=contractor_person.id),
            RoleGrant(
                user_id=moderate_user.id,
                role=Role.EMPLOYEE.value,
                region_id=region.id,
            ),
            PersonCrewGroup(
                person_id=familiar_person.id,
                crew_group_id=position.crew_group_id,
            ),
            PersonCrewGroup(
                person_id=moderate_person.id,
                crew_group_id=position.crew_group_id,
            ),
            PositionCapability(
                person_id=moderate_person.id,
                base_position_id=position.id,
                signal=CapabilitySignal.MANAGER_ALLOW.value,
            ),
            PersonCrewGroup(
                person_id=contractor_person.id,
                crew_group_id=position.crew_group_id,
            ),
            RoleGrant(
                user_id=contractor_user.id,
                role=Role.CONTRACTOR.value,
                region_id=region.id,
            ),
            UserPersonLink(user_id=casual_user.id, person_id=casual_person.id),
            RoleGrant(
                user_id=casual_user.id,
                role=Role.CASUAL.value,
                region_id=region.id,
            ),
            PersonCrewGroup(
                person_id=casual_person.id,
                crew_group_id=position.crew_group_id,
            ),
            PositionCapability(
                person_id=casual_person.id,
                base_position_id=position.id,
                signal=CapabilitySignal.MANAGER_ALLOW.value,
            ),
            UserPersonLink(user_id=disabled_user.id, person_id=disabled_person.id),
            RoleGrant(
                user_id=disabled_user.id,
                role=Role.EMPLOYEE.value,
                region_id=region.id,
            ),
            PersonCrewGroup(
                person_id=disabled_person.id,
                crew_group_id=position.crew_group_id,
            ),
            PositionCapability(
                person_id=disabled_person.id,
                base_position_id=position.id,
                signal=CapabilitySignal.MANAGER_ALLOW.value,
            ),
        ]
    )

    def add_history(
        person: Person,
        base_position: BasePosition,
        *,
        work_date: date,
        category: str = WorkdayCategory.RACE_DAY.value,
        status: str = WorkdayStatus.SCHEDULED.value,
        published: bool = True,
        history_track: Track = track,
    ) -> Workday:
        workday = Workday(
            region_id=region.id,
            category=category,
            status=status,
            created_by_user_id=manager.id,
        )
        db.add(workday)
        db.flush()
        revision = WorkdayRevision(
            workday_id=workday.id,
            revision_number=1,
            state="PUBLISHED" if published else "DRAFT",
            work_date=work_date,
            track_id=history_track.id,
            created_by_user_id=manager.id,
        )
        db.add(revision)
        db.flush()
        db.add(
            Assignment(
                revision_id=revision.id,
                base_position_id=base_position.id,
                display_name_snapshot=base_position.name,
                person_id=person.id,
                status=AssignmentStatus.ASSIGNED.value,
            )
        )
        if published:
            workday.current_published_revision_id = revision.id
        return workday

    target_date = date(2026, 11, 20)
    add_history(familiar_person, position, work_date=date(2026, 11, 1))
    add_history(moderate_person, other_position, work_date=date(2026, 11, 2))
    add_history(
        no_history_person, position, work_date=date(2026, 11, 3), published=False
    )
    add_history(
        no_history_person,
        position,
        work_date=date(2026, 11, 4),
        status=WorkdayStatus.CANCELLED.value,
    )
    add_history(
        no_history_person,
        position,
        work_date=date(2026, 11, 5),
        category=WorkdayCategory.TRAVEL_DAY.value,
    )
    add_history(
        no_history_person,
        position,
        work_date=date(2026, 11, 7),
        status=WorkdayStatus.ABANDONED.value,
    )
    add_history(
        no_history_person,
        position,
        work_date=date(2026, 11, 8),
        history_track=other_track,
    )
    add_history(no_history_person, position, work_date=date(2026, 11, 21))
    current = add_history(
        no_history_person, position, work_date=date(2026, 11, 6)
    )
    db.commit()

    view = crew_picker_views(
        db,
        region_id=region.id,
        work_date=target_date,
        track_id=track.id,
        exclude_workday_id=current.id,
        position_ids={position.id},
    )[position.id]
    people = {person.id: person for _label, rows in view.groups for person in rows}
    assert people[familiar_person.id].dice_familiarity == 2
    assert people[moderate_person.id].dice_familiarity == 1
    assert people[moderate_person.id].dice_eligible is True
    assert people[no_history_person.id].dice_familiarity == 0
    assert people[contractor_person.id].dice_eligible is False
    assert people[casual_person.id].dice_eligible is False
    assert people[disabled_person.id].dice_eligible is False
    assert people[contractor_person.id].dice_employment_known is True
    assert people[no_history_person.id].dice_employment_known is False
    assert contractor_person.id in people  # Manual selection remains available.

    without_track = crew_picker_views(
        db,
        region_id=region.id,
        work_date=target_date,
        position_ids={position.id},
    )[position.id]
    assert all(
        person.dice_familiarity == 0
        for _label, rows in without_track.groups
        for person in rows
    )

def test_cross_region_uses_person_home_region_not_role_scope(db) -> None:  # type: ignore[no-untyped-def]
    north, central = Region(name="North"), Region(name="Central")
    db.add_all([north, central])
    db.flush()
    manager = User(email="geo-manager@example.test", display_name="Manager", credential_hash="x")
    contractor = User(email="geo-contractor@example.test", display_name="Contractor", credential_hash="x")
    employee = User(email="geo-employee@example.test", display_name="Employee", credential_hash="x")
    unknown = User(email="geo-unknown@example.test", display_name="Unknown", credential_hash="x")
    people = [
        Person(display_name="Contractor", home_region_id=north.id),
        Person(display_name="Employee", home_region_id=north.id),
        Person(display_name="Unknown", home_region_id=None),
    ]
    position = BasePosition(name="Geo role")
    db.add_all([manager, contractor, employee, unknown, position, *people])
    db.flush()
    for user, person in zip([contractor, employee, unknown], people, strict=True):
        db.add(UserPersonLink(user_id=user.id, person_id=person.id))
    db.commit()

    def published_day(region: Region, person: Person, work_date: date) -> None:
        workday = create_workday(
            db,
            region_id=region.id,
            category=WorkdayCategory.RACE_DAY.value,
            work_date=work_date,
            track_id=None,
            title=f"{region.name} day",
            actor_user_id=manager.id,
        )
        draft = db.get(WorkdayRevision, workday.current_draft_revision_id)
        add_current(
            db,
            workday,
            draft,
            AssignmentInput(
                base_position_id=position.id,
                slot_index=1,
                person_id=person.id,
                status=AssignmentStatus.ASSIGNED.value,
            ),
        )
        publish_current(db, workday, draft, manager.id)

    published_day(north, people[0], date(2026, 11, 1))
    published_day(central, people[0], date(2026, 11, 2))
    published_day(central, people[1], date(2026, 11, 3))
    published_day(central, people[2], date(2026, 11, 4))
    contractor_rows = month_items(
        db,
        Actor(contractor.id, people[0].id, frozenset(), {}),
        date(2026, 11, 1),
        date(2026, 12, 1),
    )
    multi_region_rows = month_items(
        db,
        Actor(
            employee.id,
            people[1].id,
            frozenset(),
            {
                north.id: frozenset({Role.EMPLOYEE.value}),
                central.id: frozenset({Role.EMPLOYEE.value}),
            },
        ),
        date(2026, 11, 1),
        date(2026, 12, 1),
    )
    unknown_rows = month_items(
        db,
        Actor(unknown.id, people[2].id, frozenset(), {}),
        date(2026, 11, 1),
        date(2026, 12, 1),
    )
    assert [row["cross_region"] for row in contractor_rows] == [False, True]
    assert [row["cross_region"] for row in multi_region_rows] == [True]
    assert [row["cross_region"] for row in unknown_rows] == [False]


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
    assignment = add_current(
        db,
        workday,
        draft,
        AssignmentInput(
            base_position_id=position.id,
            slot_index=1,
            person_id=person.id,
            status=AssignmentStatus.ASSIGNED.value,
        ),
    )
    db.commit()
    old_publication = publish_current(db, workday, draft, manager.id)
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
