from __future__ import annotations

import json
import struct
from datetime import UTC, date, datetime, time, timedelta
from types import SimpleNamespace

from sqlalchemy import func, select

from app.catalog.models import BasePosition, Region, Track, TrackMap
from app.external_calendar.models import TransitionSourceReference
from app.external_calendar.programme import (
    failure_backoff,
    parse_love_racing_programme,
    programme_refresh_due,
)
from app.external_calendar.service import ProviderObservation, confirm_track_mapping, reconcile_observation
from app.identity.models import Person, User
from app.rostering.models import Assignment, Workday, WorkdayRevision
from app.scheduler import scheduler_lock, scheduler_tick
from app.track_maps.service import effective_map, image_dimensions, reset_manual_map, save_manual_map
from app.transition_import.service import apply_capture, parse_capture, preview_capture


def _foundation(db):  # type: ignore[no-untyped-def]
    user = User(email="admin@example.test", display_name="Admin", credential_hash="unused")
    region = Region(name="Northern")
    db.add_all([user, region])
    db.flush()
    track = Track(name="Te Rapa", region_id=region.id, palette_slot=1)
    db.add(track)
    db.flush()
    confirm_track_mapping(db, "LOVE_RACING", "Te Rapa", track.id, user.id)
    return user, region, track


def test_programme_parser_requires_contiguous_non_conflicting_schedule() -> None:
    complete = parse_love_racing_programme(
        """<html><head><title>Waikato Cup Day | LoveRacing</title></head><body>
        <table><tr><th>Race</th><th>Start</th></tr>
        <tr><td>1</td><td>12:10</td></tr><tr><td>2</td><td>12:45</td></tr>
        <tr><td>3</td><td>13:20</td></tr></table></body></html>"""
    )
    assert complete.status == "COMPLETE"
    assert complete.meeting_name == "Waikato Cup Day"
    assert (complete.race_count, complete.first_race_time, complete.last_race_time) == (
        3,
        time(12, 10),
        time(13, 20),
    )

    partial = parse_love_racing_programme(
        """<table><tr><th>Race</th><th>Scheduled Start</th></tr>
        <tr><td>1</td><td>12:10</td></tr><tr><td>3</td><td>13:20</td></tr>
        <tr><td>3</td><td>13:25</td></tr></table>"""
    )
    assert partial.status == "PARTIAL" and partial.race_count is None
    assert "Race 3 has conflicting scheduled starts." in partial.diagnostics


def test_programme_cadence_and_backoff_are_deterministic() -> None:
    now = datetime(2026, 10, 3, 9, tzinfo=UTC)
    assert programme_refresh_due(date(2026, 10, 3), now - timedelta(hours=2), "PARTIAL", now)[0]
    assert not programme_refresh_due(
        date(2026, 10, 4), now - timedelta(hours=1), "PARTIAL", now
    )[0]
    assert programme_refresh_due(date(2026, 10, 3), None, "COMPLETE", now)[0]
    assert failure_backoff(1) == timedelta(minutes=15)
    assert failure_backoff(99) == timedelta(hours=6)


def test_scheduler_tick_skips_disabled_love_racing_details_but_refreshes_maps(db, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    now = datetime(2026, 10, 3, 9, tzinfo=UTC)
    states = {
        provider: SimpleNamespace(enabled=False, next_refresh_at=None, last_success_at=None)
        for provider in ("LOVE_RACING", "HRNZ")
    }
    monkeypatch.setattr("app.scheduler.ensure_provider_states", lambda _db: states)
    monkeypatch.setattr(
        "app.scheduler.refresh_provider",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("provider refresh called")),
    )
    monkeypatch.setattr(
        "app.scheduler.refresh_due_programmes",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("programme refresh called")),
    )
    track = SimpleNamespace()
    monkeypatch.setattr("app.scheduler.due_tracks", lambda _db: [track])
    monkeypatch.setattr(
        "app.scheduler.refresh_automatic_map",
        lambda _db, refreshed_track, _client: (
            refreshed_track is track
            or (_ for _ in ()).throw(AssertionError("unexpected map track"))
        )
        and SimpleNamespace(automatic_status="AVAILABLE"),
    )

    with scheduler_lock(db) as acquired:
        assert acquired is True
    result = scheduler_tick(db, now=now)
    assert result == {
        "status": "ok",
        "providers": {"LOVE_RACING": "DISABLED", "HRNZ": "DISABLED"},
        "programmes": {"checked": 0, "updated": 0, "failed": 0, "status": "DISABLED"},
        "maps": {"checked": 1, "failed": 0},
    }


def test_scheduler_tick_runs_love_racing_details_only_when_enabled(db, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    now = datetime(2026, 10, 3, 9, tzinfo=UTC)
    states = {
        "LOVE_RACING": SimpleNamespace(enabled=True, next_refresh_at=None, last_success_at=None),
        "HRNZ": SimpleNamespace(enabled=False, next_refresh_at=None, last_success_at=None),
    }
    refreshes: list[str] = []
    programme_calls: list[datetime] = []
    monkeypatch.setattr("app.scheduler.ensure_provider_states", lambda _db: states)
    monkeypatch.setattr(
        "app.scheduler.refresh_provider",
        lambda _db, provider: refreshes.append(provider) or SimpleNamespace(status="OK"),
    )
    monkeypatch.setattr(
        "app.scheduler.refresh_due_programmes",
        lambda _db, *, now: programme_calls.append(now) or {"checked": 1, "updated": 1, "failed": 0},
    )
    monkeypatch.setattr("app.scheduler.due_tracks", lambda _db: [])

    result = scheduler_tick(db, now=now)

    assert refreshes == ["LOVE_RACING"]
    assert programme_calls == [now]
    assert result["providers"] == {"LOVE_RACING": "OK", "HRNZ": "DISABLED"}
    assert result["programmes"] == {"checked": 1, "updated": 1, "failed": 0}


def test_same_provider_updates_facts_but_partial_never_erases_complete(db) -> None:  # type: ignore[no-untyped-def]
    user, _region, track = _foundation(db)
    first = ProviderObservation(
        provider="LOVE_RACING",
        provider_event_id="2026-10-03/te-rapa",
        event_date=date(2026, 10, 3),
        source_track_name="Te Rapa",
        discipline="THOROUGHBRED",
        event_kind="RACE",
        facts={
            "programme_status": "COMPLETE",
            "race_count": 8,
            "first_race_time": "12:10",
            "last_race_time": "16:30",
        },
        raw_payload={"version": 1},
    )
    event, _state = reconcile_observation(db, first)
    changed = ProviderObservation(
        **{
            **first.__dict__,
            "facts": {
                "programme_status": "PARTIAL",
                "race_count": 9,
                "first_race_time": "12:15",
                "last_race_time": "17:05",
            },
            "raw_payload": {"version": 2},
        }
    )
    same, state = reconcile_observation(db, changed)
    assert same.id == event.id and state == "ENRICHED"
    assert same.programme_status == "COMPLETE"
    assert (same.race_count, same.first_race_time, same.last_race_time) == (
        9,
        time(12, 15),
        time(17, 5),
    )
    assert db.get(User, user.id) is not None and db.get(Track, track.id) is not None


def _png(width: int = 640, height: int = 480) -> bytes:
    return b"\x89PNG\r\n\x1a\n" + (b"\0" * 8) + struct.pack(">II", width, height) + b"map"


def test_webp_dimension_variants_are_validated() -> None:
    width, height = 640, 480
    bits = (width - 1) | ((height - 1) << 14)
    lossless = b"RIFF" + b"\0" * 4 + b"WEBPVP8L" + b"\0" * 4 + b"\x2f" + bits.to_bytes(4, "little")
    assert image_dimensions(lossless) == (width, height)


def test_manual_map_precedes_automatic_and_reset_restores_it(db, monkeypatch, tmp_path) -> None:  # type: ignore[no-untyped-def]
    map_directory = tmp_path.resolve()
    monkeypatch.setattr("app.track_maps.service._directory", lambda: map_directory)
    _user, _region, track = _foundation(db)
    row = TrackMap(
        track_id=track.id,
        automatic_file_name="auto.png",
        automatic_content_type="image/png",
        automatic_width=400,
        automatic_height=300,
        automatic_bytes=100,
        automatic_status="AVAILABLE",
    )
    db.add(row)
    db.flush()
    saved = save_manual_map(db, track, _png())
    assert effective_map(saved)["manual"] is True  # type: ignore[index]
    assert (map_directory / saved.manual_file_name).is_file()
    previous = reset_manual_map(db, saved)
    assert effective_map(saved)["manual"] is False  # type: ignore[index]
    assert previous is not None
    previous.unlink()


def _capture() -> str:
    areas = [{"areaId": "10", "areaName": "Director"}, {"areaId": "11", "areaName": "Camera"}]
    shifts = [
        {
            "shiftId": "shift-1",
            "employeeId": "person-1",
            "employeeName": "Alex Crew",
            "locationName": "T- Te Rapa",
            "areaId": "10",
            "start": "2026-10-03T20:00:00Z",
            "end": "2026-10-04T04:00:00Z",
            "note": "8 races 1210 | 1630",
            "isPublished": True,
            "password": "must be ignored",
        },
        {
            "shiftId": "shift-2",
            "employeeId": "person-1",
            "employeeName": "Alex Crew",
            "locationName": "T- Te Rapa",
            "areaId": "11",
            "start": "2026-10-03T20:30:00Z",
            "end": "2026-10-04T04:30:00Z",
            "note": "8 races 1210 | 1630",
            "isPublished": True,
            "isOpen": True,
        },
        {
            "shiftId": "draft-ignored",
            "employeeId": "person-2",
            "employeeName": "Private Draft",
            "locationName": "T- Te Rapa",
            "areaId": "10",
            "start": "2026-10-03T20:00:00Z",
            "end": "2026-10-04T04:00:00Z",
            "isPublished": False,
        },
    ]
    return (
        "Deputy Web Capture\nSchedule Area References\n"
        + json.dumps(areas)
        + "\nExtracted Schedule Shift Records\n"
        + json.dumps(shifts)
    )


def test_transition_import_is_safe_atomic_published_and_idempotent(db) -> None:  # type: ignore[no-untyped-def]
    user, _region, _track_row = _foundation(db)
    payload = parse_capture(_capture())
    assert "password" not in json.dumps(payload).casefold()
    preview = preview_capture(db, payload)
    assert preview.valid
    assert preview.counts["published_rows"] == 2
    assert preview.counts["unpublished_ignored"] == 1

    counts = apply_capture(db, payload, user.id)
    db.flush()
    assert counts == {"people": 1, "workdays": 1, "assignments": 2, "open": 1, "existing": 0}
    assert db.scalar(select(func.count()).select_from(User)) == 1
    assert db.scalar(select(func.count()).select_from(Person)) == 1
    day = db.scalar(select(Workday))
    assert day is not None and day.current_draft_revision_id is None
    revision = db.get(WorkdayRevision, day.current_published_revision_id)
    assert revision is not None and revision.state == "PUBLISHED"
    assert revision.work_date == date(2026, 10, 4)
    assert (revision.race_count, revision.first_race_time, revision.last_race_time) == (
        8,
        time(12, 10),
        time(16, 30),
    )
    assignments = list(db.scalars(select(Assignment).order_by(Assignment.display_name_snapshot)))
    assert {row.status for row in assignments} == {"ASSIGNED", "OPEN"}
    assert next(row for row in assignments if row.status == "OPEN").person_id is None
    assert next(row for row in assignments if row.status == "ASSIGNED").person_id is not None
    assert all(not row.note for row in assignments)
    assert db.scalar(select(func.count()).select_from(BasePosition)) == 2

    again = apply_capture(db, payload, user.id)
    db.flush()
    assert again["workdays"] == 0 and again["existing"] == 1
    assert db.scalar(select(func.count()).select_from(Workday)) == 1
    assert db.scalar(select(func.count()).select_from(TransitionSourceReference)) == 4


def test_transition_preview_skips_unresolved_location_without_invalidating_import(db) -> None:  # type: ignore[no-untyped-def]
    user, _region, _track_row = _foundation(db)
    payload = parse_capture(_capture().replace("T- Te Rapa", "Unknown Office"))
    preview = preview_capture(db, payload)
    assert preview.valid and preview.counts["safe_workdays"] == 0
    assert any("unresolved operational location; skip" in row for row in preview.details["tracks"])
    counts = apply_capture(db, payload, user.id)
    assert counts["workdays"] == 0 and counts["people"] == 0
    assert db.scalar(select(func.count()).select_from(Person)) == 0
