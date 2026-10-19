from __future__ import annotations

import json
import struct
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
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
from app.track_maps.service import (
    canonical_track_name,
    due_tracks,
    effective_map,
    image_candidates,
    image_dimensions,
    map_refresh_due,
    map_status,
    refresh_automatic_map,
    reset_manual_map,
    save_manual_map,
    trusted_image_url,
)
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


def test_trial_heat_programme_uses_same_strict_sequence_contract() -> None:
    complete = parse_love_racing_programme(
        """<h2>Trials at Te Rapa</h2><table><tr><th>Heat</th><th>Time</th></tr>
        <tr><td>1</td><td>12:05</td></tr><tr><td>2</td><td>12:28</td></tr></table>""",
        event_kind="TRIAL",
    )
    assert complete.status == "COMPLETE"
    assert (complete.trial_count, complete.first_trial_time, complete.last_trial_time) == (
        2, time(12, 5), time(12, 28)
    )
    partial = parse_love_racing_programme(
        """<table><tr><th>Heat</th><th>Time</th></tr>
        <tr><td>1</td><td>12:05</td></tr><tr><td>1</td><td>12:10</td></tr></table>""",
        event_kind="TRIAL",
    )
    assert partial.status == "PARTIAL"
    assert "Heat 1 has conflicting scheduled starts." in partial.diagnostics


def test_map_identity_keeps_cambridge_venues_distinct_and_preserves_safe_image_handler() -> None:
    assert canonical_track_name("Cambridge Synthetic") == "cambridge synthetic"
    assert canonical_track_name("Cambridge Harness") == "cambridge harness"
    wrapped = (
        "https://loveracing.nz/Common/Image.ashx?path="
        "%2FOnHorseFiles%2FRacecourses%2FTracks%2FCambridge-synthethic_2D.jpg"
        "&version=3.191&w=400"
    )
    assert trusted_image_url(wrapped, "https://loveracing.nz/course") == wrapped
    assert trusted_image_url("https://example.test/map.jpg", "https://loveracing.nz") is None
    candidates = image_candidates(
        f'<a href="{wrapped}">Map</a>', "https://loveracing.nz/course"
    )
    assert candidates == [wrapped]


def test_official_map_urls_are_encoded_once_and_reject_malformed_paths() -> None:
    base = "https://loveracing.nz/RaceInfo/Clubs-And-Courses/35/Racecourse.aspx"
    te_aroha = "/OnHorseFiles/Racecourses/Tracks/2D with updated Logo/Te-Aroha_new.jpg"
    encoded = "/OnHorseFiles/Racecourses/Tracks/2D%20with%20updated%20Logo/Te-Aroha_new.jpg"
    expected = f"https://loveracing.nz{encoded}"
    assert trusted_image_url(te_aroha, base) == expected
    assert trusted_image_url(encoded, base) == expected
    assert trusted_image_url(
        "/OnHorseFiles/Racecourses/Tracks/Ellerslie_2025.png?version=3.191", base
    ) == "https://loveracing.nz/OnHorseFiles/Racecourses/Tracks/Ellerslie_2025.png?version=3.191"
    assert trusted_image_url(te_aroha + "\r\nX-Test: unsafe", base) is None
    assert trusted_image_url(encoded.replace("%20", "%0d", 1), base) is None
    assert trusted_image_url(encoded.replace("%20", "%ZZ", 1), base) is None


def test_te_aroha_image_handler_keeps_query_and_validates_nested_map_path() -> None:
    base = "https://loveracing.nz/RaceInfo/Clubs-And-Courses/35/Racecourse.aspx"
    handler = (
        "/Common/Image.ashx?p=%2FOnHorseFiles%2FRacecourses%2FTracks%2F"
        "2D+with+updated+Logo%2FTe-Aroha_new.jpg&version=3.191&w=400"
    )
    expected = (
        "https://loveracing.nz/Common/Image.ashx?p=%2FOnHorseFiles%2FRacecourses%2FTracks%2F"
        "2D%20with%20updated%20Logo%2FTe-Aroha_new.jpg&version=3.191&w=400"
    )
    assert trusted_image_url(handler, base) == expected
    assert trusted_image_url(
        "/Common/Image.ashx?p=https%3A%2F%2Fexample.test%2Fmap.jpg&w=400", base
    ) is None
    assert trusted_image_url(
        "/Common/Image.ashx?p=%2FOnHorseFiles%2FRacecourses%2FTracks%2FTe-Aroha_new.jpg"
        "&p=%2FOnHorseFiles%2FRacecourses%2FTracks%2FEllerslie_2025.png",
        base,
    ) is None


def test_te_aroha_handler_succeeds_when_direct_image_is_unavailable(
    db, monkeypatch, tmp_path
) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr("app.track_maps.service._directory", lambda: tmp_path.resolve())
    _user, _region, track = _foundation(db)
    track.name = "Te Aroha"
    content = _png(640, 480)

    class HandlerClient:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def get(self, url: str, *, accept: str) -> HTTPResponse:
            self.calls.append(url)
            if accept == "text/html":
                return HTTPResponse(
                    url=url,
                    status=200,
                    content_type="text/html",
                    body=(
                        b'<a href="/Common/Image.ashx?p=%2FOnHorseFiles%2FRacecourses%2FTracks%2F'
                        b'2D+with+updated+Logo%2FTe-Aroha_new.jpg&amp;version=3.191&amp;w=400">'
                        b'<img alt="Track - 2D" src="/Common/Image.ashx?p=%2FOnHorseFiles%2F'
                        b'Racecourses%2FTracks%2F2D+with+updated+Logo%2FTe-Aroha_new.jpg&amp;w=400">'
                        b"</a>"
                    ),
                )
            if "/Common/Image.ashx" not in url:
                raise SourceHTTPError("Source returned HTTP 404.")
            assert "2D%20with%20updated%20Logo" in url
            return HTTPResponse(url=url, status=200, content_type="image/png", body=content)

    client = HandlerClient()
    row = refresh_automatic_map(db, track, client)  # type: ignore[arg-type]
    assert row.automatic_status == "AVAILABLE"
    assert row.automatic_error is None
    assert "/Common/Image.ashx" in row.automatic_source_url
    assert (tmp_path / row.automatic_file_name).read_bytes() == content
    assert any("/Common/Image.ashx" in call for call in client.calls)
    assert any("2D%20with%20updated%20Logo/Te-Aroha_new.jpg" in call for call in client.calls)


def test_ellerslie_html_failure_still_downloads_known_official_fallback(
    db, monkeypatch, tmp_path
) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr("app.track_maps.service._directory", lambda: tmp_path.resolve())
    _user, _region, track = _foundation(db)
    track.name = "Ellerslie"
    content = _png(900, 700)

    class FallbackClient:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def get(self, url: str, *, accept: str) -> HTTPResponse:
            self.calls.append(url)
            if url.endswith("/Racecourse.aspx"):
                raise SourceHTTPError("Course page challenge.")
            assert url.endswith("/OnHorseFiles/Racecourses/Tracks/Ellerslie_2025.png")
            assert accept == "image/jpeg,image/png,image/webp"
            return HTTPResponse(url=url, status=200, content_type="image/png", body=content)

    client = FallbackClient()
    row = refresh_automatic_map(db, track, client)  # type: ignore[arg-type]
    assert row.automatic_status == "AVAILABLE"
    assert row.automatic_source_url.endswith("Ellerslie_2025.png")
    assert row.automatic_width == 900 and row.automatic_height == 700
    assert (tmp_path / row.automatic_file_name).read_bytes() == content
    assert map_status(row)["code"] == "AVAILABLE"
    assert len(client.calls) == 2


def test_cambridge_synthetic_candidates_exclude_artwork_and_satellite() -> None:
    course_url = "https://loveracing.nz/RaceInfo/Clubs-And-Courses/7/Racecourse.aspx"
    fallback = "/OnHorseFiles/Racecourses/Tracks/Cambridge-synthethic_2D.jpg"
    html = """
      <meta property="og:image" content="/OnHorseFiles/PageArtwork/cambridge.jpg">
      <a href="/promotions/banner.jpg">Promotion</a>
      <img alt="Satellite" src="/OnHorseFiles/Racecourses/Tracks/Cambridge-satellite.jpg">
      <img alt="Track - 2D" src="/OnHorseFiles/Racecourses/Tracks/Cambridge-synthethic_2D.jpg">
    """
    assert image_candidates(html, course_url, fallback) == [
        "https://loveracing.nz/OnHorseFiles/Racecourses/Tracks/Cambridge-synthethic_2D.jpg"
    ]


def test_known_course_map_wins_over_larger_unrelated_track_artwork(
    db, monkeypatch, tmp_path
) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr("app.track_maps.service._directory", lambda: tmp_path.resolve())
    _user, _region, track = _foundation(db)
    track.name = "Ellerslie"
    correct = _png(700, 500)
    unrelated = _png(1600, 1200)

    class CourseClient:
        def get(self, url: str, *, accept: str) -> HTTPResponse:
            if accept == "text/html":
                return HTTPResponse(
                    url=url,
                    status=200,
                    content_type="text/html",
                    body=(
                        b'<img alt="Track - 2D" src="/OnHorseFiles/Racecourses/Tracks/'
                        b'Other-Venue-2D.png">'
                    ),
                )
            body = correct if url.endswith("Ellerslie_2025.png") else unrelated
            return HTTPResponse(url=url, status=200, content_type="image/png", body=body)

    row = refresh_automatic_map(db, track, CourseClient())  # type: ignore[arg-type]
    assert row.automatic_source_url.endswith("Ellerslie_2025.png")
    assert (row.automatic_width, row.automatic_height) == (700, 500)


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
    automatic = _png(400, 300)
    row = TrackMap(
        track_id=track.id,
        automatic_file_name="auto.png",
        automatic_content_type="image/png",
        automatic_width=400,
        automatic_height=300,
        automatic_bytes=len(automatic),
        automatic_status="AVAILABLE",
    )
    db.add(row)
    db.flush()
    (map_directory / "auto.png").write_bytes(automatic)
    saved = save_manual_map(db, track, _png())
    assert effective_map(saved)["manual"] is True  # type: ignore[index]
    assert map_status(saved)["code"] == "MANUAL_OVERRIDE"
    assert (map_directory / saved.manual_file_name).is_file()
    previous = reset_manual_map(db, saved)
    assert effective_map(saved)["manual"] is False  # type: ignore[index]
    assert previous is not None
    previous.unlink()


def test_track_map_refresh_is_quarterly_after_success_and_daily_after_failure() -> None:
    checked_at = datetime(2026, 1, 1, tzinfo=UTC)
    successful = TrackMap(
        automatic_checked_at=checked_at,
        automatic_file_name="last-good.png",
        automatic_status="AVAILABLE",
    )
    assert not map_refresh_due(successful, now=checked_at + timedelta(days=89))
    assert map_refresh_due(successful, now=checked_at + timedelta(days=90))
    failed = TrackMap(
        automatic_checked_at=checked_at,
        automatic_file_name="last-good.png",
        automatic_status="AVAILABLE",
        automatic_error="HTTP/source failure: Source returned HTTP 403.",
    )
    assert not map_refresh_due(failed, now=checked_at + timedelta(hours=23))
    assert map_refresh_due(failed, now=checked_at + timedelta(days=1))


def test_scheduled_map_discovery_uses_effective_course_override(db) -> None:  # type: ignore[no-untyped-def]
    _user, _region, track = _foundation(db)
    track.name = "Custom Northern Venue"
    track.map_reference = "Te Rapa"
    db.flush()
    assert [candidate.id for candidate in due_tracks(db)] == [track.id]


def test_missing_map_files_are_not_effective_and_manual_override_does_not_fall_back(
    db, monkeypatch, tmp_path
) -> None:  # type: ignore[no-untyped-def]
    map_directory = tmp_path.resolve()
    monkeypatch.setattr("app.track_maps.service._directory", lambda: map_directory)
    _user, _region, track = _foundation(db)
    automatic = _png()
    row = TrackMap(
        track_id=track.id,
        automatic_file_name="auto.png",
        automatic_content_type="image/png",
        automatic_width=640,
        automatic_height=480,
        automatic_bytes=len(automatic),
        automatic_status="AVAILABLE",
    )
    assert effective_map(row) is None
    (map_directory / "auto.png").write_bytes(automatic)
    assert effective_map(row)["manual"] is False  # type: ignore[index]
    row.has_manual_override = True
    row.manual_file_name = "missing-manual.png"
    assert effective_map(row) is None


def test_refresh_failure_retains_only_a_usable_automatic_cache(
    db, monkeypatch, tmp_path
) -> None:  # type: ignore[no-untyped-def]
    map_directory = tmp_path.resolve()
    monkeypatch.setattr("app.track_maps.service._directory", lambda: map_directory)
    _user, _region, track = _foundation(db)
    automatic = _png()
    row = TrackMap(
        track_id=track.id,
        automatic_file_name="last-good.png",
        automatic_content_type="image/png",
        automatic_width=640,
        automatic_height=480,
        automatic_bytes=len(automatic),
        automatic_status="AVAILABLE",
    )
    db.add(row)
    db.flush()
    cached = map_directory / "last-good.png"
    cached.write_bytes(automatic)

    class FailingClient:
        def get(self, _url: str, *, accept: str):  # type: ignore[no-untyped-def]
            raise SourceHTTPError(f"Source unavailable for {accept}.")

    refreshed = refresh_automatic_map(db, track, FailingClient())  # type: ignore[arg-type]
    assert refreshed.automatic_status == "AVAILABLE"
    assert refreshed.automatic_error.startswith("HTTP/source failure:")
    assert effective_map(refreshed)["file_name"] == "last-good.png"  # type: ignore[index]
    assert map_status(refreshed)["code"] == "SOURCE_FAILED_RETAINED"
    cached.unlink()
    refreshed = refresh_automatic_map(db, track, FailingClient())  # type: ignore[arg-type]
    assert refreshed.automatic_status == "ERROR"
    assert effective_map(refreshed) is None
    assert map_status(refreshed)["code"] == "MISSING_FILE"
    assert "HTTP/source failure:" in map_status(refreshed)["detail"]


def test_map_refresh_reports_invalid_url_image_and_storage_failures(
    db, monkeypatch, tmp_path
) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr("app.track_maps.service._directory", lambda: tmp_path.resolve())
    _user, _region, track = _foundation(db)

    class UnexpectedClient:
        def get(self, _url: str, *, accept: str):  # type: ignore[no-untyped-def]
            raise AssertionError(f"No request expected for {accept}.")

    monkeypatch.setitem(
        __import__("app.track_maps.service", fromlist=["COURSES"]).COURSES,
        "te rapa",
        ("/RaceInfo/Clubs-And-Courses/38/Racecourse.aspx", "/bad%ZZ/map.jpg"),
    )
    invalid_url = refresh_automatic_map(db, track, UnexpectedClient())  # type: ignore[arg-type]
    assert invalid_url.automatic_error.startswith("Invalid URL or image path:")

    monkeypatch.setitem(
        __import__("app.track_maps.service", fromlist=["COURSES"]).COURSES,
        "te rapa",
        (
            "/RaceInfo/Clubs-And-Courses/38/Racecourse.aspx",
            "/OnHorseFiles/Racecourses/Tracks/2D%20with%20updated%20Logo/Te%20Rapa%20copy.jpg",
        ),
    )

    class InvalidImageClient:
        def get(self, url: str, *, accept: str) -> HTTPResponse:
            if accept == "text/html":
                raise SourceHTTPError("Course page challenge.")
            return HTTPResponse(url=url, status=200, content_type="text/html", body=b"not an image")

    invalid_image = refresh_automatic_map(db, track, InvalidImageClient())  # type: ignore[arg-type]
    assert invalid_image.automatic_error.startswith("Invalid or unsupported image:")

    class ValidImageClient:
        def get(self, url: str, *, accept: str) -> HTTPResponse:
            if accept == "text/html":
                raise SourceHTTPError("Course page challenge.")
            return HTTPResponse(url=url, status=200, content_type="image/png", body=_png())

    monkeypatch.setattr(Path, "write_bytes", lambda self, content: (_ for _ in ()).throw(PermissionError()))
    storage_failure = refresh_automatic_map(db, track, ValidImageClient())  # type: ignore[arg-type]
    assert storage_failure.automatic_error == (
        "File permission/storage failure: the Track-map cache is not writable."
    )


def test_effective_map_uses_bounded_header_and_rejects_metadata_mismatch(
    db, monkeypatch, tmp_path
) -> None:  # type: ignore[no-untyped-def]
    map_directory = tmp_path.resolve()
    monkeypatch.setattr("app.track_maps.service._directory", lambda: map_directory)
    _user, _region, track = _foundation(db)
    content = _png() + (b"padding" * 20_000)
    path = map_directory / "bounded.png"
    path.write_bytes(content)
    row = TrackMap(
        track_id=track.id,
        automatic_file_name=path.name,
        automatic_content_type="image/png",
        automatic_width=640,
        automatic_height=480,
        automatic_bytes=len(content),
        automatic_status="AVAILABLE",
    )

    def fail_full_read(_path: Path) -> bytes:
        raise AssertionError("ordinary map availability must not load the whole file")

    monkeypatch.setattr(Path, "read_bytes", fail_full_read)
    assert effective_map(row)["bytes"] == len(content)  # type: ignore[index]
    row.automatic_bytes += 1
    assert effective_map(row) is None
    row.automatic_bytes -= 1
    path.write_bytes(b"not an image" + (b"x" * (len(content) - 12)))
    assert effective_map(row) is None


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
    assert preview.counts["source_published_rows"] == 2
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

    existing_preview = preview_capture(db, payload)
    assert existing_preview.counts["existing_publications"] == 1
    assert existing_preview.counts["importable_assignments"] == 0

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
    assert preview.counts["skipped_workdays"] == 1
    assert preview.counts["skipped_assignments"] == 2
    assert any("unresolved operational location; skip" in row for row in preview.details["tracks"])
    counts = apply_capture(db, payload, user.id)
    assert counts["workdays"] == 0 and counts["people"] == 0
    assert db.scalar(select(func.count()).select_from(Person)) == 0


def test_transition_track_resolution_prefers_exact_before_alias(db) -> None:  # type: ignore[no-untyped-def]
    _user, region, _track_row = _foundation(db)
    cambridge = Track(name="Cambridge", region_id=region.id, palette_slot=2)
    synthetic = Track(name="Cambridge Synthetic", region_id=region.id, palette_slot=3)
    db.add_all([cambridge, synthetic])
    db.flush()

    exact = preview_capture(db, parse_capture(_capture().replace("T- Te Rapa", "T- Cambridge")))
    assert exact.valid
    assert exact.counts["safe_workdays"] == 1
    assert exact.counts["importable_assignments"] == 2
    assert exact.counts["skipped_workdays"] == 0
    assert any("T- Cambridge: Cambridge" == row for row in exact.details["tracks"])

    rotorua = Track(name="Rotorua", region_id=region.id, palette_slot=4)
    arawa = Track(name="Arawa Park", region_id=region.id, palette_slot=5)
    db.add_all([rotorua, arawa])
    db.flush()
    ambiguous = preview_capture(
        db,
        parse_capture(_capture().replace("T- Te Rapa", "T- Rotorua Racecourse")),
    )
    assert ambiguous.valid
    assert ambiguous.counts["safe_workdays"] == 0
    assert ambiguous.counts["skipped_workdays"] == 1
    assert ambiguous.counts["skipped_assignments"] == 2


def test_transition_cambridge_real_shape_keeps_all_40_workdays_and_416_rows(db) -> None:  # type: ignore[no-untyped-def]
    _user, region, _track_row = _foundation(db)
    db.add_all(
        [
            Track(name="Cambridge", region_id=region.id, palette_slot=2),
            Track(name="Cambridge Synthetic", region_id=region.id, palette_slot=3),
        ]
    )
    db.flush()
    local_zone = ZoneInfo("Pacific/Auckland")
    shifts: list[dict[str, object]] = []
    for workday_index in range(40):
        cambridge = workday_index < 10
        row_count = (
            9 if cambridge and workday_index < 8 else
            8 if cambridge else
            11 if workday_index < 38 else
            10
        )
        day = date(2026, 1, 1) + timedelta(days=workday_index)
        for row_index in range(row_count):
            start = datetime.combine(day, time(8, 45), tzinfo=local_zone)
            shifts.append(
                {
                    "id": f"real-{workday_index}-{row_index}",
                    "employee_id": f"person-{row_index % 41 + 1}",
                    "employee_name": f"Person {row_index % 41 + 1}",
                    "location": "T- Cambridge" if cambridge else "T- Te Rapa",
                    "area_id": "side-1",
                    "area_name": "Side 1",
                    "start": start.isoformat(),
                    "end": (start + timedelta(hours=12)).isoformat(),
                    "note": "",
                    "published": True,
                    "open": False,
                }
            )
    assert len(shifts) == 416
    preview = preview_capture(
        db,
        {"version": 1, "source": "DEPUTY_CAPTURE", "areas": [], "shifts": shifts},
    )
    assert preview.valid
    assert preview.counts["source_published_rows"] == 416
    assert preview.counts["safe_workdays"] == 40
    assert preview.counts["importable_assignments"] == 416
    assert preview.counts["skipped_workdays"] == 0
    assert preview.counts["skipped_assignments"] == 0


def test_transition_import_uses_modal_span_and_preserves_person_exceptions(db) -> None:  # type: ignore[no-untyped-def]
    user, _region, _track_row = _foundation(db)
    zone = ZoneInfo("Pacific/Auckland")
    shifts: list[dict[str, object]] = []
    for index in range(11):
        start_time = time(8, 30) if index < 2 else time(8, 45)
        start = datetime.combine(date(2026, 10, 14), start_time, tzinfo=zone)
        finish = datetime.combine(date(2026, 10, 14), time(20, 30), tzinfo=zone)
        shifts.append(
            {
                "id": f"modal-{index}",
                "employee_id": f"modal-person-{index}",
                "employee_name": f"Modal Person {index}",
                "location": "T- Te Rapa",
                "area_id": f"position-{index}",
                "area_name": f"Position {index}",
                "start": start.isoformat(),
                "end": finish.isoformat(),
                "note": "",
                "published": True,
                "open": False,
            }
        )
    for index, (start_time, finish_time) in enumerate(
        ((time(8, 30), time(20, 15)), (time(8, 45), time(20, 30)))
    ):
        start = datetime.combine(date(2026, 10, 15), start_time, tzinfo=zone)
        finish = datetime.combine(date(2026, 10, 15), finish_time, tzinfo=zone)
        for copy in range(2):
            shifts.append(
                {
                    "id": f"tie-{index}-{copy}",
                    "employee_id": f"tie-person-{index}-{copy}",
                    "employee_name": f"Tie Person {index} {copy}",
                    "location": "T- Te Rapa",
                    "area_id": f"tie-position-{index}",
                    "area_name": f"Tie Position {index}",
                    "start": start.isoformat(),
                    "end": finish.isoformat(),
                    "note": "",
                    "published": True,
                    "open": False,
                }
            )
    payload = {"version": 1, "source": "DEPUTY_CAPTURE", "areas": [], "shifts": shifts}
    counts = apply_capture(db, payload, user.id)
    assert counts["workdays"] == 2 and counts["assignments"] == 15
    revisions = list(db.scalars(select(WorkdayRevision).order_by(WorkdayRevision.work_date)))
    assert (revisions[0].start_time, revisions[0].end_time) == (time(8, 45), time(20, 30))
    first_assignments = list(
        db.scalars(select(Assignment).where(Assignment.revision_id == revisions[0].id))
    )
    assert sum(row.start_time == time(8, 30) for row in first_assignments) == 2
    assert (revisions[1].start_time, revisions[1].end_time) == (time(8, 30), time(20, 30))


def test_transition_import_maps_numbered_ccu_to_one_base_position_and_slot(db) -> None:  # type: ignore[no-untyped-def]
    user, _region, _track_row = _foundation(db)
    ccu = BasePosition(name="CCU")
    db.add(ccu)
    db.flush()
    payload = parse_capture(_capture())
    published = [row for row in payload["shifts"] if row["published"]]
    published[0]["area_name"] = "CCU1"
    published[1]["area_name"] = "CCU 2"
    counts = apply_capture(db, payload, user.id)
    assert counts["assignments"] == 2
    assignments = list(db.scalars(select(Assignment).order_by(Assignment.slot_index)))
    assert [(row.base_position_id, row.slot_index, row.display_name_snapshot) for row in assignments] == [
        (ccu.id, 1, "CCU 1"),
        (ccu.id, 2, "CCU 2"),
    ]
    assert db.scalar(
        select(func.count()).select_from(BasePosition).where(BasePosition.lifecycle == "ACTIVE")
    ) == 1


def test_transition_import_blocks_vehicle_identifiers_from_becoming_positions(db) -> None:  # type: ignore[no-untyped-def]
    _user, _region, _track_row = _foundation(db)
    payload = parse_capture(_capture())
    published = [row for row in payload["shifts"] if row["published"]]
    published[0]["area_name"] = "684"
    published[1]["area_name"] = "Rav91"
    preview = preview_capture(db, payload)
    assert not preview.valid
    assert sum("source Vehicle identifier" in item for item in preview.conflicts) == 2
    assert db.scalar(select(func.count()).select_from(BasePosition)) == 0
