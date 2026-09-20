from datetime import date
from pathlib import Path

import pytest

from app.external_calendar.providers.hrnz import parse_race_ics, parse_trials_diary
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
