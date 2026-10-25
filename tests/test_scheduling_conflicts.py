from __future__ import annotations

from datetime import date, time

import pytest
from sqlalchemy import select

from app.catalog.models import Region, Vehicle
from app.core.enums import WorkdayStatus
from app.identity.models import Person, User
from app.rostering.conflicts import operation_publication_conflicts, publication_conflicts
from app.rostering.models import Assignment, Workday, WorkdayRevision
from app.rostering.service import PublishConflict, publish
from app.rostering.travel import TRANSPORT_VEHICLE, effective_person_interval


def _setup(db):  # type: ignore[no-untyped-def]
    region = Region(name="Conflict test region")
    other_region = Region(name="Private conflict region")
    user = User(email="conflicts@example.test", display_name="Manager", credential_hash="hash")
    person = Person(display_name="Alex Crew")
    vehicle = Vehicle(name="Unit 7", lifecycle="ACTIVE")
    db.add_all([region, other_region, user, person, vehicle])
    db.flush()
    return region, other_region, user, person, vehicle


def _workday(
    db,
    *,
    region,
    user,
    work_date,
    start,
    finish,
    person,
    vehicle=None,
    state="PUBLISHED",
    status=WorkdayStatus.SCHEDULED.value,
    title="Other day",
    generated_from=None,
    standard_travel=False,
    on_track=None,
    hotel_minutes=None,
):  # type: ignore[no-untyped-def]
    workday = Workday(
        region_id=region.id,
        category="RACE_DAY",
        status=status,
        generated_from_workday_id=generated_from.id if generated_from else None,
        created_by_user_id=user.id,
    )
    db.add(workday)
    db.flush()
    revision = WorkdayRevision(
        workday_id=workday.id,
        revision_number=1,
        state=state,
        work_date=work_date,
        title=title,
        track_name_snapshot="Test track",
        start_time=start,
        end_time=finish,
        standard_travel_enabled=standard_travel,
        on_track_time=on_track,
        hotel_to_track_minutes=hotel_minutes,
        created_by_user_id=user.id,
    )
    db.add(revision)
    db.flush()
    assignment = Assignment(
        revision_id=revision.id,
        display_name_snapshot="Camera",
        person_id=person.id,
        person_name_snapshot=person.display_name,
        status="ASSIGNED",
        transport_mode=TRANSPORT_VEHICLE if vehicle else "SELF_TRAVEL",
        vehicle_id=vehicle.id if vehicle else None,
        vehicle_name_snapshot=vehicle.name if vehicle else None,
    )
    db.add(assignment)
    if state == "DRAFT":
        workday.current_draft_revision_id = revision.id
    else:
        workday.current_published_revision_id = revision.id
    db.flush()
    return workday, revision, assignment


def test_same_and_adjacent_day_person_and_vehicle_overlaps(db) -> None:  # type: ignore[no-untyped-def]
    region, _, user, person, vehicle = _setup(db)
    _workday(
        db, region=region, user=user, work_date=date(2026, 10, 5), start=time(23),
        finish=time(2), person=person, vehicle=vehicle,
    )
    target, draft, _ = _workday(
        db, region=region, user=user, work_date=date(2026, 10, 6), start=time(1),
        finish=time(4), person=person, vehicle=vehicle, state="DRAFT", title="Target",
    )
    conflicts = publication_conflicts(db, target, draft)
    assert {item.kind for item in conflicts} == {"PERSON", "VEHICLE"}
    assert all("2026-10-05 23:00–2026-10-06 02:00" in item.timing for item in conflicts)


@pytest.mark.parametrize(
    ("first_date", "second_date"),
    [
        (date(2026, 10, 31), date(2026, 11, 1)),
        (date(2026, 12, 31), date(2027, 1, 1)),
        (date(2026, 9, 26), date(2026, 9, 27)),  # NZ daylight-saving boundary
    ],
)
def test_adjacent_overlap_handles_calendar_boundaries(db, first_date, second_date) -> None:  # type: ignore[no-untyped-def]
    region, _, user, person, _ = _setup(db)
    _workday(
        db, region=region, user=user, work_date=first_date, start=time(23), finish=time(2),
        person=person,
    )
    target, draft, _ = _workday(
        db, region=region, user=user, work_date=second_date, start=time(1), finish=time(4),
        person=person, state="DRAFT", title="Target",
    )
    assert len(publication_conflicts(db, target, draft)) == 1


def test_exact_boundary_and_distinct_same_day_shifts_do_not_overlap(db) -> None:  # type: ignore[no-untyped-def]
    region, _, user, person, _ = _setup(db)
    _workday(
        db, region=region, user=user, work_date=date(2026, 10, 5), start=time(23),
        finish=time(2), person=person,
    )
    target, draft, _ = _workday(
        db, region=region, user=user, work_date=date(2026, 10, 6), start=time(2),
        finish=time(5), person=person, state="DRAFT", title="Target",
    )
    assert publication_conflicts(db, target, draft) == []
    draft.work_date = date(2026, 10, 5)
    draft.start_time, draft.end_time = time(8), time(12)
    other = db.get(WorkdayRevision, next(iter(db.scalars(
        select(Workday.current_published_revision_id).where(
            Workday.current_published_revision_id.is_not(None)
        )
    ))))
    other.start_time, other.end_time = time(17), time(22)
    assert publication_conflicts(db, target, draft) == []


def test_derived_hotel_start_can_begin_on_preceding_date(db) -> None:  # type: ignore[no-untyped-def]
    region, _, user, person, _ = _setup(db)
    target, draft, row = _workday(
        db, region=region, user=user, work_date=date(2027, 1, 1), start=time(0, 30),
        finish=time(4), person=person, state="DRAFT", standard_travel=True,
        on_track=time(0, 30), hotel_minutes=60,
    )
    interval = effective_person_interval(draft, row)
    assert interval.start.isoformat() == "2026-12-31T23:30:00"  # type: ignore[union-attr]
    _workday(
        db, region=region, user=user, work_date=date(2026, 12, 31), start=time(23, 45),
        finish=time(0, 15), person=person,
    )
    assert len(publication_conflicts(db, target, draft)) == 1


def test_unknown_times_warn_only_on_same_workday_date(db) -> None:  # type: ignore[no-untyped-def]
    region, _, user, person, _ = _setup(db)
    _workday(
        db, region=region, user=user, work_date=date(2026, 10, 5), start=None,
        finish=None, person=person,
    )
    same, same_draft, _ = _workday(
        db, region=region, user=user, work_date=date(2026, 10, 5), start=time(9),
        finish=time(17), person=person, state="DRAFT", title="Same",
    )
    assert "potential same-day conflict" in publication_conflicts(db, same, same_draft)[0].timing
    same.status = WorkdayStatus.CANCELLED.value
    adjacent, adjacent_draft, _ = _workday(
        db, region=region, user=user, work_date=date(2026, 10, 6), start=time(1),
        finish=time(4), person=person, state="DRAFT", title="Adjacent",
    )
    assert publication_conflicts(db, adjacent, adjacent_draft) == []


def test_multi_position_rows_deduplicate_other_workday_warning(db) -> None:  # type: ignore[no-untyped-def]
    region, _, user, person, _ = _setup(db)
    _workday(
        db, region=region, user=user, work_date=date(2026, 10, 5), start=time(9),
        finish=time(17), person=person,
    )
    target, draft, row = _workday(
        db, region=region, user=user, work_date=date(2026, 10, 5), start=time(10),
        finish=time(12), person=person, state="DRAFT", title="Target",
    )
    db.add(
        Assignment(
            revision_id=draft.id, display_name_snapshot="Replay", person_id=person.id,
            person_name_snapshot=person.display_name, status="ASSIGNED",
        )
    )
    assert len(publication_conflicts(db, target, draft)) == 1
    assert row.revision_id == draft.id  # both roles remain valid on this Workday


def test_published_and_active_draft_are_current_without_duplicate_warning(db) -> None:  # type: ignore[no-untyped-def]
    region, _, user, person, _ = _setup(db)
    other, published, _ = _workday(
        db, region=region, user=user, work_date=date(2026, 10, 5), start=time(8),
        finish=time(10), person=person,
    )
    private = WorkdayRevision(
        workday_id=other.id, revision_number=2, state="DRAFT", work_date=date(2026, 10, 5),
        title="Other draft", track_name_snapshot="Draft track", start_time=time(12),
        end_time=time(14), based_on_revision_id=published.id, created_by_user_id=user.id,
    )
    db.add(private)
    db.flush()
    db.add(Assignment(
        revision_id=private.id, display_name_snapshot="Replay", person_id=person.id,
        person_name_snapshot=person.display_name, status="ASSIGNED",
    ))
    other.current_draft_revision_id = private.id
    target, draft, _ = _workday(
        db, region=region, user=user, work_date=date(2026, 10, 5), start=time(12, 30),
        finish=time(13), person=person, state="DRAFT", title="Target",
    )
    conflicts = publication_conflicts(db, target, draft)
    assert len(conflicts) == 1
    assert conflicts[0].other_title == "Other draft"


@pytest.mark.parametrize("inactive", [WorkdayStatus.CANCELLED.value, WorkdayStatus.ABANDONED.value])
def test_inactive_workdays_are_not_conflicts(db, inactive) -> None:  # type: ignore[no-untyped-def]
    region, _, user, person, _ = _setup(db)
    _workday(
        db, region=region, user=user, work_date=date(2026, 10, 5), start=time(9),
        finish=time(17), person=person, status=inactive,
    )
    target, draft, _ = _workday(
        db, region=region, user=user, work_date=date(2026, 10, 5), start=time(10),
        finish=time(12), person=person, state="DRAFT", title="Target",
    )
    assert publication_conflicts(db, target, draft) == []


def test_generated_travel_parent_is_not_a_conflict_but_unrelated_day_is(db) -> None:  # type: ignore[no-untyped-def]
    region, _, user, person, _ = _setup(db)
    parent, parent_draft, _ = _workday(
        db, region=region, user=user, work_date=date(2026, 10, 6), start=time(1),
        finish=time(5), person=person, state="DRAFT", title="Parent", standard_travel=True,
    )
    travel, travel_draft, _ = _workday(
        db, region=region, user=user, work_date=date(2026, 10, 5), start=time(23),
        finish=time(1), person=person, state="DRAFT", title="Travel", generated_from=parent,
    )
    assert publication_conflicts(db, travel, travel_draft, include_inactive_current=True) == []
    unrelated, _, _ = _workday(
        db, region=region, user=user, work_date=date(2026, 10, 5), start=time(23, 30),
        finish=time(0, 30), person=person, title="Unrelated",
    )
    conflicts = operation_publication_conflicts(db, parent, parent_draft)
    assert len(conflicts) == 1
    assert conflicts[0].other_workday_id == unrelated.id


def test_cross_region_conflict_redacts_workday_details(db) -> None:  # type: ignore[no-untyped-def]
    region, private_region, user, person, _ = _setup(db)
    _workday(
        db, region=private_region, user=user, work_date=date(2026, 10, 5), start=time(9),
        finish=time(17), person=person, title="Secret event",
    )
    target, draft, _ = _workday(
        db, region=region, user=user, work_date=date(2026, 10, 5), start=time(10),
        finish=time(12), person=person, state="DRAFT", title="Target",
    )
    conflict = publication_conflicts(db, target, draft, visible_region_ids={region.id})[0]
    assert "another Workday" in conflict.message
    assert "Secret event" not in conflict.message
    assert "Test track" not in conflict.message


def test_publish_revalidates_and_requires_explicit_confirmation(db) -> None:  # type: ignore[no-untyped-def]
    region, _, user, person, _ = _setup(db)
    target, draft, _ = _workday(
        db, region=region, user=user, work_date=date(2026, 10, 6), start=time(1),
        finish=time(4), person=person, state="DRAFT", title="Target",
    )
    db.commit()
    assert publication_conflicts(db, target, draft) == []
    _workday(
        db, region=region, user=user, work_date=date(2026, 10, 5), start=time(23),
        finish=time(2), person=person,
    )
    db.commit()
    target_id, draft_id, user_id, version = target.id, draft.id, user.id, target.lock_version
    with pytest.raises(PublishConflict, match="Publish anyway"):
        publish(db, target_id, draft_id, user_id, version)
    db.rollback()
    published = publish(db, target_id, draft_id, user_id, version, confirm_conflicts=True)
    assert published.state == "PUBLISHED"
