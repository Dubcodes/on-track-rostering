from datetime import date, time

import pytest
from sqlalchemy import func, select

from app.catalog.models import CrewGroup, Region, Track
from app.external_calendar.importer import apply_bundle, parse_bundle, preview_bundle
from app.external_calendar.models import (
    CalendarDisplayPreference,
    ExternalCalendarEvent,
    ExternalEventObservation,
)
from app.external_calendar.read_models import external_calendar_items
from app.external_calendar.service import ProviderObservation, confirm_track_mapping, reconcile_observation
from app.identity.models import Person, User


def foundation(db):  # type: ignore[no-untyped-def]
    user = User(email="source@example.test", display_name="Source Admin", credential_hash="unused")
    region = Region(name="Northern")
    db.add_all([user, region])
    db.flush()
    track = Track(name="Te Rapa", region_id=region.id, palette_slot=7)
    db.add(track)
    db.flush()
    confirm_track_mapping(db, "LOVE_RACING", "Te Rapa", track.id, user.id)
    confirm_track_mapping(db, "API", "Te Rapa Racecourse", track.id, user.id)
    return user, region, track


def observation(
    provider: str, track: str, *, trial: str | None = None, race_count: int = 8, last: str = "16:40"
) -> ProviderObservation:
    facts = {"first_race_time": "12:30", "last_race_time": last, "race_count": race_count}
    if trial:
        facts["first_trial_time"] = trial
    return ProviderObservation(
        provider=provider,
        provider_event_id=f"{provider}-20",
        event_date=date(2026, 9, 20),
        source_track_name=track,
        discipline="THOROUGHBRED",
        event_kind="RACE",
        facts=facts,
        raw_payload={"meeting": track, **facts},
    )


def test_reconciliation_dedupes_cross_provider_and_enriches_by_field(db):  # type: ignore[no-untyped-def]
    _user, _region, _track = foundation(db)
    event, state = reconcile_observation(db, observation("LOVE_RACING", "Te Rapa"))
    assert state == "CREATED" and event.first_race_time == time(12, 30)
    same, state = reconcile_observation(db, observation("LOVE_RACING", "Te Rapa"))
    assert same.id == event.id and state == "DUPLICATE"
    same, state = reconcile_observation(db, observation("API", "Te Rapa Racecourse", trial="10:30"))
    assert same.id == event.id and state == "ENRICHED" and same.first_trial_time == time(10, 30)
    assert same.field_provenance["first_race_time"] == ["API", "LOVE_RACING"]
    assert same.field_provenance["first_trial_time"] == ["API"]
    assert same.presentation_provider == "LOVE_RACING"
    assert db.scalar(select(func.count()).select_from(ExternalCalendarEvent)) == 1
    assert db.scalar(select(func.count()).select_from(ExternalEventObservation)) == 2


def test_conflict_and_unresolved_track_require_review(db):  # type: ignore[no-untyped-def]
    foundation(db)
    event, _ = reconcile_observation(db, observation("LOVE_RACING", "Te Rapa"))
    same, state = reconcile_observation(db, observation("API", "Te Rapa Racecourse", race_count=9))
    assert same.id == event.id and state == "CONFLICT" and same.race_count == 8
    unresolved, state = reconcile_observation(db, observation("HRNZ", "Cambridge Synthetic"))
    assert unresolved is None and state == "CREATED"
    row = db.scalar(select(ExternalEventObservation).where(ExternalEventObservation.provider == "HRNZ"))
    assert row.mapping_state == "UNMATCHED" and row.reconciliation_state == "REVIEW"


def test_import_preview_is_read_only_idempotent_and_allocates_palette(db):  # type: ignore[no-untyped-def]
    user = User(email="import@example.test", display_name="Importer", credential_hash="unused")
    db.add(user)
    db.flush()
    bundle = parse_bundle(
        '{"version":"1","regions":[{"name":"Central"}],"tracks":[{"name":"Awapuni","region":"Central"}],"crew_groups":[{"name":"Cameras"}],"positions":[{"name":"Side 1","crew_group":"Cameras"}],"people":[{"display_name":"Alex Crew","email":"alex@example.test","home_region":"Central","crew_groups":["Cameras"],"capabilities":[{"crew_group":"Cameras","position":"Side 1"}]}]}'
    )
    before = len(db.new), db.scalar(select(func.count()).select_from(Region))
    plan = preview_bundle(db, bundle)
    assert plan.valid and plan.counts["tracks"]["create"] == 1
    assert (len(db.new), db.scalar(select(func.count()).select_from(Region))) == before
    apply_bundle(db, bundle, user.id)
    db.flush()
    assert db.scalar(select(Track.palette_slot).where(Track.name == "Awapuni")) == 1
    counts = tuple(
        db.scalar(select(func.count()).select_from(model)) for model in (Region, Track, CrewGroup, Person)
    )
    apply_bundle(db, bundle, user.id)
    db.flush()
    assert (
        tuple(
            db.scalar(select(func.count()).select_from(model)) for model in (Region, Track, CrewGroup, Person)
        )
        == counts
    )


def test_ambiguous_people_and_secret_fields_are_rejected(db):  # type: ignore[no-untyped-def]
    db.add_all([Person(display_name="John Smith"), Person(display_name="John Smith")])
    db.flush()
    bundle = parse_bundle('{"people":[{"display_name":"John Smith"}]}')
    plan = preview_bundle(db, bundle)
    assert not plan.valid and plan.counts["people"]["ambiguous"] == 1
    with pytest.raises(ValueError, match="forbidden field"):
        parse_bundle('{"people":[],"password":"nope"}')


def test_preferences_filter_external_only_with_expected_defaults(db):  # type: ignore[no-untyped-def]
    user, _region, track = foundation(db)
    for discipline, kind, day in (
        ("THOROUGHBRED", "RACE", 20),
        ("HARNESS", "RACE", 21),
        ("THOROUGHBRED", "TRIAL", 22),
    ):
        db.add(
            ExternalCalendarEvent(
                event_date=date(2026, 9, day),
                track_id=track.id,
                discipline=discipline,
                event_kind=kind,
                external_track_name=track.name,
            )
        )
    db.flush()
    items = external_calendar_items(db, user.id, date(2026, 9, 1), date(2026, 10, 1))
    assert len(items) == 2 and all(item["kind"] == "RACE" for item in items)
    preference = CalendarDisplayPreference(
        user_id=user.id,
        show_thoroughbred=False,
        show_harness=True,
        show_trials=True,
        minimal_external_detail=True,
    )
    db.add(preference)
    db.flush()
    items = external_calendar_items(db, user.id, date(2026, 9, 1), date(2026, 10, 1))
    assert len(items) == 1 and items[0]["discipline"] == "HARNESS" and items[0]["minimal"] is True
