from __future__ import annotations

import json
import struct
from datetime import UTC, date, datetime, time, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from sqlalchemy import func, select

from app.auth.policy import Actor
from app.catalog.models import BasePosition, Region, Track, TrackMap
from app.external_calendar.detail_refresh import due_love_racing_events, refresh_love_racing_programme
from app.external_calendar.http import HTTPResponse, SourceHTTPError
from app.external_calendar.models import ExternalEventObservation, TransitionSourceReference
from app.external_calendar.programme import (
    failure_backoff,
    parse_love_racing_programme,
    parse_programme_clock,
    programme_refresh_due,
)
from app.external_calendar.service import (
    ProviderObservation,
    adopt_external_event,
    confirm_track_mapping,
    reconcile_observation,
)
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


def test_programme_parser_handles_love_racing_clocks_and_meeting_heading() -> None:
    complete = parse_love_racing_programme(_taupo_programme_html())
    assert complete.status == "COMPLETE"
    assert complete.meeting_name == "Racing Taupo @ Taupo"
    assert (complete.race_count, complete.first_race_time, complete.last_race_time) == (
        9,
        time(12, 34),
        time(17, 24),
    )

    partial = parse_love_racing_programme(
        """<table><tr><th>Race</th><th>Scheduled Start</th></tr>
        <tr><td>1</td><td>12:10</td></tr><tr><td>3</td><td>1:20 pm</td></tr>
        <tr><td>3</td><td>1:25 pm</td></tr></table>"""
    )
    assert partial.status == "PARTIAL" and partial.race_count is None
    assert "Race 3 has conflicting scheduled starts." in partial.diagnostics

    duplicate = parse_love_racing_programme(
        """<table><tr><th>Race</th><th>Start</th></tr><tr><td>1</td><td>12:34 pm</td></tr>
        <tr><td>1</td><td>12:34 pm</td></tr><tr><td>2</td><td>1:08 pm</td></tr></table>"""
    )
    assert duplicate.status == "COMPLETE"
    assert "Race 1 appeared 2 times." in duplicate.diagnostics

    awaiting = parse_love_racing_programme(
        """<table><tr><th>Race</th><th>Start</th></tr><tr><td>1</td><td>To be confirmed</td></tr></table>"""
    )
    assert awaiting.status == "AWAITING_SCHEDULE"
    assert "Race 1 had an invalid scheduled start." in awaiting.diagnostics
    assert parse_programme_clock("12:00 am") == time(0, 0)
    assert parse_programme_clock("12:00 pm") == time(12, 0)
    assert parse_programme_clock("17:24") == time(17, 24)
    for value in ("1:08", "25:00", "12:60 pm", "noon"):
        try:
            parse_programme_clock(value)
        except ValueError:
            pass
        else:
            raise AssertionError(f"{value} should be rejected as a programme clock")


def test_programme_cadence_and_backoff_are_deterministic() -> None:
    now = datetime(2026, 10, 8, 9, tzinfo=ZoneInfo("Pacific/Auckland"))
    assert programme_refresh_due(date(2026, 10, 8), now - timedelta(hours=2), "PARTIAL", now)[0]
    assert programme_refresh_due(date(2026, 10, 9), None, "DISCOVERED", now)[0]
    assert not programme_refresh_due(date(2026, 10, 9), now - timedelta(hours=1), "PARTIAL", now)[0]
    assert programme_refresh_due(date(2026, 10, 9), now - timedelta(hours=2), "PARTIAL", now)[0]
    assert not programme_refresh_due(date(2026, 10, 8), now - timedelta(minutes=59), "PARTIAL", now)[0]
    assert programme_refresh_due(date(2026, 10, 8), now - timedelta(hours=1), "PARTIAL", now)[0]
    assert programme_refresh_due(date(2026, 10, 8), None, "COMPLETE", now)[0]
    assert failure_backoff(1) == timedelta(minutes=15)
    assert failure_backoff(99) == timedelta(hours=6)


def _taupo_programme_html(*, last_race: str = "5:24 pm", updated: str = "09/10/2026 2:45 pm") -> str:
    rows = (
        (1, "12:34 pm"),
        (2, "1:08 pm"),
        (3, "1:42 pm"),
        (4, "2:17 pm"),
        (5, "2:51 pm"),
        (6, "3:25 pm"),
        (7, "4:05 pm"),
        (8, "4:47 pm"),
        (9, last_race),
    )
    return "".join(
        (
            "<html><head><title>RaceInfo | Meetings / Fields</title></head><body>",
            f"<h2>Racing Taupo @ Taupo Last updated {updated}</h2>",
            "<table class=\"programme-header\"><tr><th>Race</th><th>Start</th>",
            "<th>Name</th><th>Conditions &amp; Distance</th></tr></table>",
            "<table class=\"further-detail\"><tr><td>1</td><td>12:59 pm</td>",
            "<td>False detail row</td></tr></table>",
            "<table class=\"overview-info mobile\">",
            *(f"<tr><td>{number}</td><td>{clock}</td></tr>" for number, clock in rows[:5]),
            "</table><table class=\"overview-info\">",
            *(f"<tr><td>{number}</td><td>{clock}</td></tr>" for number, clock in rows[5:]),
            "</table><table><tr><td>1</td><td>12:58 pm</td></tr></table></body></html>",
        )
    )


def test_due_taupo_programme_refresh_reconciles_and_adopts_unpublished_draft(db) -> None:  # type: ignore[no-untyped-def]
    user, region, _track = _foundation(db)
    taupo = Track(name="Taupo", region_id=region.id, palette_slot=2)
    db.add(taupo)
    db.flush()
    confirm_track_mapping(db, "LOVE_RACING", "Taupo", taupo.id, user.id)
    event, state = reconcile_observation(
        db,
        ProviderObservation(
            provider="LOVE_RACING",
            provider_event_id="55962",
            event_date=date(2026, 10, 9),
            source_track_name="Taupo",
            discipline="THOROUGHBRED",
            event_kind="RACE",
            facts={"status": "SCHEDULED", "meeting_name": "Taupo"},
            raw_payload={"DayID": "55962"},
        ),
    )
    assert event is not None and state == "CREATED"
    now = datetime(2026, 10, 8, 9, tzinfo=ZoneInfo("Pacific/Auckland"))
    assert [row.id for row in due_love_racing_events(db, now=now)] == [event.id]

    class FailingClient:
        def get(self, _url: str, *, accept: str):  # type: ignore[no-untyped-def]
            assert accept == "text/html"
            raise SourceHTTPError("Source returned HTTP 503.")

    failed = refresh_love_racing_programme(db, event, client=FailingClient())
    assert (failed.outcome, failed.programme_status) == ("ERROR", None)
    assert event.detail_failure_count == 1
    assert event.latest_detail_error == "SourceHTTPError: Source returned HTTP 503."
    assert db.scalar(select(func.count()).select_from(ExternalEventObservation)) == 1

    class ProgrammeClient:
        def __init__(self, body: str) -> None:
            self.urls: list[str] = []
            self.body = body

        def get(self, url: str, *, accept: str) -> HTTPResponse:
            self.urls.append(url)
            assert accept == "text/html"
            return HTTPResponse(url=url, status=200, content_type="text/html", body=self.body.encode())

    client = ProgrammeClient(_taupo_programme_html())
    refreshed = refresh_love_racing_programme(db, event, client=client)
    assert (refreshed.outcome, refreshed.programme_status) == ("ENRICHED", "COMPLETE")
    assert client.urls == ["https://loveracing.nz/RaceInfo/55962/Meeting-Overview.aspx"]
    assert (event.meeting_name, event.programme_status, event.race_count) == (
        "Racing Taupo @ Taupo",
        "COMPLETE",
        9,
    )
    assert (event.first_race_time, event.last_race_time) == (time(12, 34), time(17, 24))
    assert event.detail_failure_count == 0 and event.latest_detail_error is None
    assert db.scalar(select(func.count()).select_from(ExternalEventObservation)) == 2

    changed_markup = ProgrammeClient(
        _taupo_programme_html(updated="09/10/2026 2:50 pm")
        .replace("<body>", '<body data-render-id="volatile-second-render">')
    )
    duplicate_result = refresh_love_racing_programme(db, event, client=changed_markup)
    assert (duplicate_result.outcome, duplicate_result.programme_status) == ("DUPLICATE", "COMPLETE")
    assert db.scalar(select(func.count()).select_from(ExternalEventObservation)) == 2

    workday, created = adopt_external_event(
        db,
        event.id,
        Actor(user.id, None, frozenset({"ADMIN"}), {}),
    )
    assert created is True
    draft = db.get(WorkdayRevision, workday.current_draft_revision_id)
    assert draft is not None
    assert (draft.title, draft.race_count, draft.first_race_time, draft.last_race_time) == (
        "Racing Taupo @ Taupo",
        9,
        time(12, 34),
        time(17, 24),
    )

    changed_fact = ProgrammeClient(_taupo_programme_html(last_race="5:30 pm"))
    changed_result = refresh_love_racing_programme(db, event, client=changed_fact)
    assert (changed_result.outcome, changed_result.programme_status) == ("ENRICHED", "COMPLETE")
    assert event.last_race_time == time(17, 30)
    assert draft.last_race_time == time(17, 30)
    assert db.scalar(select(func.count()).select_from(ExternalEventObservation)) == 3


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
        "contractors": {"checked": 0, "changed": 0, "expired": 0},
        "notifications": {
            "status": "DISABLED",
            "reminders": 0,
            "digests": 0,
            "processed": 0,
        },
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
