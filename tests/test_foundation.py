from __future__ import annotations

import uuid
from datetime import date, time, timedelta

import pytest
from sqlalchemy import select

from app.auth.policy import Actor, can_crew_view, can_manage_region
from app.auth.security import credential_error, is_safe_next
from app.catalog.models import BasePosition, CrewGroup, Region, Track
from app.core.enums import (
    AssignmentStatus,
    CapabilitySignal,
    DeclinePolicy,
    OpenApplicationStatus,
    Role,
    WorkdayCategory,
)
from app.core.holidays import holiday_for_date
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
    publish_current(db, unrelated, unrelated_draft, manager.id)
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
    draft = ensure_draft(db, workday, manager.id)
    select_application(
        db,
        workday=workday,
        draft=draft,
        application=application,
        expected_version=workday.lock_version,
    )
    db.commit()
    publish_current(db, workday, draft, manager.id)
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
    db.commit()

    views = crew_picker_views(
        db,
        workday=workday,
        draft=draft,
        position_ids={head_on.id, director.id},
    )
    head_relevant = {person.id: person for person in views[head_on.id].relevant}
    director_other = {person.id: person for person in views[director.id].other}
    assert head_relevant[remote.id].hint == "Preferred or approved"
    assert director_other[remote.id].hint == "Manager marked unavailable for this position"

    duplicate_options = [
        person
        for _label, people in views[head_on.id].groups
        for person in people
        if person.display_name == "John Smith"
    ]
    assert {person.id for person in duplicate_options} == {duplicate_a.id, duplicate_b.id}
    assert len({person.context_label for person in duplicate_options}) == 2
    assert all("Southern" in person.context_label for person in duplicate_options)

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
