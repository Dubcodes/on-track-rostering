from datetime import date, datetime, time

from app.auth.policy import Actor
from app.catalog.models import Region, Track, Vehicle
from app.external_calendar.models import ExternalCalendarEvent
from app.external_calendar.service import adopt_external_event
from app.identity.models import User
from app.rostering.models import Assignment, WorkdayRevision
from app.rostering.service import DraftDetailsInput, create_workday, ensure_draft, save_draft
from app.rostering.timing import ceil_to_quarter, derive_race_day_timing, floor_to_quarter
from app.rostering.travel import effective_person_travel


def test_quarter_rounding_and_canonical_race_day_example() -> None:
    assert floor_to_quarter(datetime(2026, 9, 20, 12, 24)).time() == time(12, 15)
    assert floor_to_quarter(datetime(2026, 9, 20, 12, 30)).time() == time(12, 30)
    assert ceil_to_quarter(datetime(2026, 9, 20, 16, 47)).time() == time(17)
    assert ceil_to_quarter(datetime(2026, 9, 20, 16, 45)).time() == time(16, 45)
    result = derive_race_day_timing(
        work_date=date(2026, 9, 20),
        first_race_time=time(12, 24),
        last_race_time=time(16, 47),
        setup_lead_minutes=120,
        track_travel_minutes=30,
        pack_up_minutes=60,
        return_travel_minutes=30,
    )
    assert (result.on_track, result.start, result.finish) == (
        time(10, 15),
        time(9, 45),
        time(18, 30),
    )


def test_race_day_timing_handles_midnight_missing_and_zero_durations() -> None:
    overnight = derive_race_day_timing(
        work_date=date(2026, 9, 20),
        first_race_time=time(0, 4),
        last_race_time=time(0, 7),
        setup_lead_minutes=30,
        track_travel_minutes=0,
        pack_up_minutes=0,
        return_travel_minutes=0,
    )
    assert (overnight.on_track, overnight.start, overnight.finish) == (
        time(23, 30),
        time(23, 30),
        time(0, 15),
    )
    missing = derive_race_day_timing(
        work_date=date(2026, 9, 20),
        first_race_time=None,
        last_race_time=None,
        setup_lead_minutes=120,
        track_travel_minutes=None,
        pack_up_minutes=60,
        return_travel_minutes=None,
    )
    assert (missing.on_track, missing.start, missing.finish) == (None, None, None)


def _details(**changes):  # type: ignore[no-untyped-def]
    values = {
        "work_date": date(2026, 9, 20),
        "track_id": None,
        "title": "Te Rapa",
        "start_time": None,
        "end_time": None,
        "on_track_time": None,
        "first_trial_time": None,
        "first_race_time": time(12, 24),
        "last_race_time": time(16, 47),
        "race_count": 8,
        "day_note": "",
        "change_reason": "",
        "track_travel_minutes": 30,
        "return_travel_minutes": 30,
        "pack_up_minutes": 60,
    }
    values.update(changes)
    return DraftDetailsInput(**values)


def test_server_derives_values_and_preserves_explicit_overrides(db) -> None:  # type: ignore[no-untyped-def]
    user = User(email="timing@example.test", display_name="Timing Manager", credential_hash="unused")
    region = Region(name="Timing region", lead_minutes_race_day=120)
    db.add_all([user, region])
    db.flush()
    track = Track(name="Te Rapa", region_id=region.id, palette_slot=1, default_travel_minutes=30)
    db.add(track)
    db.flush()
    workday = create_workday(
        db,
        region_id=region.id,
        category="RACE_DAY",
        work_date=date(2026, 9, 20),
        track_id=track.id,
        title="Te Rapa",
        actor_user_id=user.id,
        commit=False,
    )
    draft = db.get(WorkdayRevision, workday.current_draft_revision_id)
    save_draft(
        db,
        workday_id=workday.id,
        draft_id=draft.id,
        expected_version=workday.lock_version,
        details=_details(
            track_id=track.id,
            start_time=time(1),
            on_track_time=time(2),
            end_time=time(3),
        ),
        assignments=[],
        commit=False,
    )
    assert (draft.start_time, draft.on_track_time, draft.end_time) == (
        time(9, 45),
        time(10, 15),
        time(18, 30),
    )

    save_draft(
        db,
        workday_id=workday.id,
        draft_id=draft.id,
        expected_version=workday.lock_version,
        details=_details(
            track_id=track.id,
            first_race_time=time(13, 24),
            last_race_time=time(17, 47),
            on_track_time=time(10),
            on_track_time_is_override=True,
            start_time=time(9, 30),
            start_time_is_override=True,
            end_time=time(19),
            end_time_is_override=True,
        ),
        assignments=[],
        commit=False,
    )
    assert (draft.on_track_time, draft.start_time, draft.end_time) == (
        time(10),
        time(9, 30),
        time(19),
    )

    save_draft(
        db,
        workday_id=workday.id,
        draft_id=draft.id,
        expected_version=workday.lock_version,
        details=_details(track_id=track.id),
        assignments=[],
        commit=False,
    )
    assert (draft.on_track_time, draft.start_time, draft.end_time) == (
        time(10, 15),
        time(9, 45),
        time(18, 30),
    )


def test_external_adoption_and_person_call_time_precedence(db) -> None:  # type: ignore[no-untyped-def]
    user = User(email="adopt-timing@example.test", display_name="Manager", credential_hash="unused")
    region = Region(name="Adoption region", lead_minutes_race_day=120)
    db.add_all([user, region])
    db.flush()
    track = Track(name="Te Rapa", region_id=region.id, palette_slot=1, default_travel_minutes=30)
    vehicle = Vehicle(name="Unit 1")
    db.add_all([track, vehicle])
    db.flush()
    event = ExternalCalendarEvent(
        event_date=date(2026, 9, 20),
        track_id=track.id,
        discipline="THOROUGHBRED",
        event_kind="RACE",
        first_race_time=time(12, 24),
        last_race_time=time(16, 47),
        race_count=8,
    )
    db.add(event)
    db.flush()
    actor = Actor(user.id, None, frozenset(), {region.id: frozenset({"MANAGER"})})
    workday, created = adopt_external_event(db, event.id, actor)
    assert created
    draft = db.get(WorkdayRevision, workday.current_draft_revision_id)
    assert (
        draft.start_time,
        draft.on_track_time,
        draft.return_travel_minutes,
        draft.end_time,
    ) == (time(9, 45), time(10, 15), 30, time(18, 30))

    event.first_race_time = time(13)
    same, created = adopt_external_event(db, event.id, actor)
    assert not created and same.id == workday.id and draft.first_race_time == time(12, 24)

    vehicle_row = Assignment(transport_mode="VEHICLE")
    self_row = Assignment(transport_mode="SELF_TRAVEL")
    not_required = Assignment(transport_mode="NOT_REQUIRED")
    explicit = Assignment(transport_mode="VEHICLE", start_time=time(9, 30))
    assert effective_person_travel(draft, vehicle_row).start == time(9, 45)
    assert effective_person_travel(draft, self_row).start == time(10, 15)
    assert effective_person_travel(draft, not_required).start == time(10, 15)
    assert effective_person_travel(draft, explicit).start == time(9, 30)


def test_published_snapshot_survives_track_default_change(db) -> None:  # type: ignore[no-untyped-def]
    user = User(email="snapshot@example.test", display_name="Manager", credential_hash="unused")
    region = Region(name="Snapshot region")
    db.add_all([user, region])
    db.flush()
    track = Track(name="Snapshot Track", region_id=region.id, palette_slot=1, default_travel_minutes=30)
    db.add(track)
    db.flush()
    workday = create_workday(
        db,
        region_id=region.id,
        category="RACE_DAY",
        work_date=date(2026, 9, 20),
        track_id=track.id,
        title="Snapshot",
        actor_user_id=user.id,
        commit=False,
    )
    published = db.get(WorkdayRevision, workday.current_draft_revision_id)
    published.state = "PUBLISHED"
    workday.current_published_revision_id = published.id
    workday.current_draft_revision_id = None
    track.default_travel_minutes = 35
    clone = ensure_draft(db, workday, user.id, commit=False)
    assert published.track_travel_minutes == 30
    assert clone.track_travel_minutes == 30
