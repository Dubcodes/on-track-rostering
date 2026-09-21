from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.external_calendar.http import SourceHTTPError
from app.external_calendar.providers.hrnz import (
    PROGRAMMES_URL,
    RACE_DATES_INDEX_URL,
    RACE_ICS_URL,
    TRIALS_DIARY_URL,
    HRNZAdapter,
    parse_programme_index,
    parse_race_dates_index,
    parse_race_dates_month,
    parse_race_ics,
    parse_trials_diary,
)
from app.external_calendar.providers.love_racing import parse_calendar_json, parse_event_tiles

FIXTURES = Path(__file__).parent / "fixtures" / "external_calendar"


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_love_racing_event_tiles_parse_official_race_shape():
    observations, warnings = parse_event_tiles(
        fixture("love_racing_races.html"), reference=date(2026, 9, 1)
    )
    assert warnings == []
    assert len(observations) == 1
    event = observations[0]
    assert event.event_date == date(2026, 9, 23)
    assert event.source_track_name == "Phar Lap Raceway"
    assert event.event_kind == "RACE"
    assert event.discipline == "THOROUGHBRED"


def test_love_racing_calendar_distinguishes_trials_and_warns_on_bad_record():
    observations, warnings = parse_calendar_json(fixture("love_racing_calendar.json"))
    assert [item.event_kind for item in observations] == ["RACE", "TRIAL"]
    assert [item.provider_event_id for item in observations] == ["55924", "56251"]
    assert observations[1].source_track_name == "Foxton"
    assert "first_trial_time" not in observations[1].facts
    assert len(warnings) == 1


def test_love_racing_calendar_accepts_known_types_and_uses_new_zealand_date():
    raw = (
        '[{"DayID":"1","RaceDate":"/Date(1788177600000)/",'
        '"WebMeetingType":"R","Racecourse":"Ruakaka"},'
        '{"DayID":"2","RaceDate":"/Date(1788177600000)/",'
        '"WebMeetingType":"P","Racecourse":"Ellerslie"},'
        '{"DayID":"3","RaceDate":"/Date(1788177600000)/",'
        '"WebMeetingType":"X","Racecourse":"Unknown"}]'
    )
    observations, warnings = parse_calendar_json(raw)
    assert [item.event_kind for item in observations] == ["RACE", "RACE"]
    assert {item.event_date for item in observations} == {date(2026, 9, 1)}
    assert warnings == ["Love Racing calendar record used unknown WebMeetingType X."]


def test_love_racing_invalid_contract_fails_controlled():
    with pytest.raises(ValueError, match="valid JSON"):
        parse_calendar_json("<html>changed</html>")


def test_hrnz_official_ics_parses_race_and_skips_malformed_event():
    observations, warnings = parse_race_ics(fixture("hrnz_races.ics"))
    assert len(observations) == 1
    assert observations[0].provider_event_id == "hrnz-20261002-addington"
    assert observations[0].event_date == date(2026, 10, 2)
    assert observations[0].source_track_name == "Addington Raceway"
    assert observations[0].discipline == "HARNESS"
    assert observations[0].event_kind == "RACE"
    assert len(warnings) == 1


def test_hrnz_trials_diary_explicit_time_and_qualifiers_are_honest():
    observations, warnings = parse_trials_diary(
        fixture("hrnz_trials.html"), reference=date(2026, 9, 1)
    )
    assert warnings == []
    assert len(observations) == 3
    ashburton = [item for item in observations if item.source_track_name == "Ashburton Raceway"]
    assert len(ashburton) == 2
    assert all(item.facts["first_trial_time"] == "11:00" for item in ashburton)
    qualifiers = next(item for item in observations if item.source_track_name == "Invercargill")
    assert qualifiers.facts["qualifiers_before_races"] is True
    assert "first_trial_time" not in qualifiers.facts


def test_hrnz_race_dates_index_is_host_allowlisted_and_horizon_bounded():
    links = parse_race_dates_index(
        fixture("hrnz_race_dates_index.html"),
        start=date(2026, 10, 1),
        end=date(2026, 10, 31),
    )
    assert [(item.month, item.url) for item in links] == [
        (
            date(2026, 10, 1),
            "https://infohorse.hrnz.co.nz/datahrs/calendar/Raceday/2027/dates_october2027.htm",
        )
    ]


def test_hrnz_programmes_capture_explicit_alternate_venues_and_tentative_state():
    programmes, warnings = parse_programme_index(fixture("hrnz_programmes.html"))
    assert len(programmes) == 4
    assert len(warnings) == 1
    addington = next(item for item in programmes if "Metropolitan" in item.club)
    assert addington.venue == "ADDINGTON"
    methven = next(item for item in programmes if item.club.startswith("Akaroa"))
    assert methven.venue == "METHVEN"
    assert methven.tentative is True
    assert methven.programme_id == "2026101136pg-01"


def test_hrnz_month_preserves_club_until_explicit_venue_evidence_exists():
    programmes, _warnings = parse_programme_index(fixture("hrnz_programmes.html"))
    observations, warnings = parse_race_dates_month(
        fixture("hrnz_race_dates_october.html"),
        month=date(2026, 10, 1),
        programmes=programmes,
    )
    assert len(observations) == 4
    assert len(warnings) == 2
    cambridge = next(item for item in observations if item.event_date.day == 1)
    assert cambridge.source_track_name == "Waikato Bay of Plenty Harness Inc"
    assert cambridge.facts["venue_confidence"] == "CLUB_ONLY"
    assert cambridge.facts["meeting_period"] == "NIGHT"
    assert cambridge.facts["tentative"] is True
    addington = next(item for item in observations if item.event_date.day == 2)
    assert addington.source_track_name == "ADDINGTON"
    assert addington.facts["meeting_period"] == "TWILIGHT"
    methven = next(item for item in observations if item.event_date.day == 11)
    assert methven.source_track_name == "Methven"
    assert methven.facts["venue_evidence"] == "Methven"
    oamaru = next(item for item in observations if item.event_date.day == 18)
    assert oamaru.source_track_name == "Oamaru Harness Racing Club Inc"
    assert oamaru.facts["venue_confidence"] == "CLUB_ONLY"


def test_hrnz_month_does_not_choose_an_ambiguous_programme():
    programmes, _warnings = parse_programme_index(fixture("hrnz_programmes_ambiguous.html"))
    html = """<table><tr><td>2</td><td>*</td><td>Friday</td>
    <td><a href='club'>Auckland Trotting Club Inc</a></td></tr></table>"""
    observations, warnings = parse_race_dates_month(
        html, month=date(2026, 10, 1), programmes=programmes
    )
    assert observations[0].source_track_name == "Auckland Trotting Club Inc"
    assert observations[0].facts["venue_confidence"] == "CLUB_ONLY"
    assert warnings == [
        "HRNZ programme match was ambiguous for Auckland Trotting Club Inc on 2026-10-02."
    ]


class _FakeClient:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def get(self, url, *, accept):
        self.calls.append(url)
        value = self.responses.get(url)
        if isinstance(value, Exception):
            raise value
        if value is None:
            raise AssertionError(f"Unexpected URL: {url}")
        return SimpleNamespace(text=value)


def test_hrnz_adapter_does_not_fetch_html_when_ics_is_available():
    client = _FakeClient(
        {
            RACE_ICS_URL: fixture("hrnz_races.ics"),
            TRIALS_DIARY_URL: fixture("hrnz_trials.html"),
        }
    )
    adapter = HRNZAdapter(client)
    payload = adapter.fetch(date(2026, 9, 1), date(2026, 10, 31))
    result = adapter.normalize(payload, date(2026, 9, 1), date(2026, 10, 31))
    assert RACE_DATES_INDEX_URL not in client.calls
    assert PROGRAMMES_URL not in client.calls
    assert result.components["races"] == "PARTIAL"


@pytest.mark.parametrize(
    "ics_value",
    [SourceHTTPError("Source returned HTTP 403."), "<html>not an iCalendar feed</html>"],
)
def test_hrnz_adapter_falls_back_to_bounded_official_html(ics_value):
    month_url = (
        "https://infohorse.hrnz.co.nz/datahrs/calendar/Raceday/2027/"
        "dates_october2027.htm"
    )
    client = _FakeClient(
        {
            RACE_ICS_URL: ics_value,
            RACE_DATES_INDEX_URL: fixture("hrnz_race_dates_index.html"),
            month_url: fixture("hrnz_race_dates_october.html"),
            PROGRAMMES_URL: fixture("hrnz_programmes.html"),
            TRIALS_DIARY_URL: fixture("hrnz_trials.html"),
        }
    )
    adapter = HRNZAdapter(client)
    payload = adapter.fetch(date(2026, 10, 1), date(2026, 10, 31))
    result = adapter.normalize(payload, date(2026, 10, 1), date(2026, 10, 31))
    races = [item for item in result.observations if item.event_kind == "RACE"]
    assert len(races) == 4
    assert result.components["races"] == "PARTIAL_FALLBACK"
    assert client.calls.count(month_url) == 1


def test_hrnz_race_failure_does_not_suppress_independent_trials():
    client = _FakeClient(
        {
            RACE_ICS_URL: SourceHTTPError("Source returned HTTP 403."),
            RACE_DATES_INDEX_URL: SourceHTTPError("Source returned HTTP 403."),
            TRIALS_DIARY_URL: fixture("hrnz_trials.html"),
        }
    )
    adapter = HRNZAdapter(client)
    payload = adapter.fetch(date(2026, 9, 1), date(2026, 10, 31))
    result = adapter.normalize(payload, date(2026, 9, 1), date(2026, 10, 31))
    assert result.components == {"races": "ERROR", "trials": "OK"}
    assert {item.event_kind for item in result.observations} == {"TRIAL"}


def test_hrnz_clean_html_fallback_is_a_successful_race_component():
    month_html = """<table><tr><td>11</td><td></td><td>Sunday</td>
    <td><a href='club'>Akaroa Trotting Club Inc</a> (moved to Methven)</td>
    </tr></table>"""
    programme_html = """<table><tr><td>Akaroa Trotting Club Inc</td><td>Tentative</td>
    <td><a href='2026101136pg-01.htm'>ANNUAL MEETING (AT METHVEN)</a></td>
    <td>Sun, 11 Oct 2026</td></tr></table>"""
    payload = {
        "errors": {"race_ics": "Source returned HTTP 403."},
        "race_months": {"2026-10-01": month_html},
        "programmes": programme_html,
        "trials": fixture("hrnz_trials.html"),
    }
    result = HRNZAdapter(_FakeClient({})).normalize(
        payload, date(2026, 10, 1), date(2026, 10, 31)
    )
    assert result.components == {"races": "OK_FALLBACK", "trials": "OK"}
    assert result.warnings == []
