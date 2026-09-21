import json
from datetime import date, time

import pytest
from sqlalchemy import func, select

from app.auth.policy import Actor
from app.catalog.models import BasePosition, CrewGroup, Region, Track
from app.external_calendar.adapters import NormalizedProviderResult
from app.external_calendar.importer import apply_bundle, parse_bundle, preview_bundle
from app.external_calendar.inventory import source_inventory, structural_master_data_bundle
from app.external_calendar.models import (
    CalendarDisplayPreference,
    ExternalCalendarEvent,
    ExternalEventObservation,
    ExternalProviderState,
    ExternalTrackMapping,
)
from app.external_calendar.read_models import external_calendar_items
from app.external_calendar.refresh import refresh_provider
from app.external_calendar.service import (
    ProviderObservation,
    adopt_external_event,
    confirm_track_mapping,
    mapped_track,
    reconcile_observation,
)
from app.identity.models import Person, User
from app.rostering.models import Workday, WorkdayRevision


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


def actor(user, region, role="EMPLOYEE", person_id=None):  # type: ignore[no-untyped-def]
    return Actor(user.id, person_id, frozenset(), {region.id: frozenset({role})})


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


def test_stable_provider_identity_date_change_is_review_evidence_not_a_move(db):  # type: ignore[no-untyped-def]
    foundation(db)
    event, _state = reconcile_observation(db, observation("LOVE_RACING", "Te Rapa"))
    changed = observation("LOVE_RACING", "Te Rapa")
    changed = ProviderObservation(
        **{**changed.__dict__, "event_date": date(2026, 9, 21), "raw_payload": {"date": "2026-09-21"}}
    )
    same, state = reconcile_observation(db, changed)
    assert same.id == event.id and state == "CONFLICT"
    assert same.event_date == date(2026, 9, 20)


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
    items = external_calendar_items(
        db, actor(user, _region), date(2026, 9, 1), date(2026, 10, 1)
    )
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
    items = external_calendar_items(
        db, actor(user, _region), date(2026, 9, 1), date(2026, 10, 1)
    )
    assert len(items) == 1 and items[0]["discipline"] == "HARNESS" and items[0]["minimal"] is True


def test_external_items_are_region_scoped_and_contractors_get_no_planning_feed(db):  # type: ignore[no-untyped-def]
    user, northern, track = foundation(db)
    central = Region(name="Central")
    db.add(central)
    db.flush()
    central_track = Track(name="Awapuni", region_id=central.id, palette_slot=1)
    db.add_all(
        [
            central_track,
            ExternalCalendarEvent(
                event_date=date(2026, 9, 20),
                track_id=track.id,
                discipline="THOROUGHBRED",
                event_kind="RACE",
            ),
            ExternalCalendarEvent(
                event_date=date(2026, 9, 21),
                track_id=central_track.id,
                discipline="THOROUGHBRED",
                event_kind="RACE",
            ),
        ]
    )
    db.flush()
    rows = external_calendar_items(db, actor(user, northern), date(2026, 9, 1), date(2026, 10, 1))
    assert [row["track"] for row in rows] == ["Te Rapa"]
    contractor = actor(user, northern, "CONTRACTOR")
    assert external_calendar_items(db, contractor, date(2026, 9, 1), date(2026, 10, 1)) == []


def test_private_adoption_remains_visible_then_published_link_suppresses(db):  # type: ignore[no-untyped-def]
    user, region, track = foundation(db)
    event = ExternalCalendarEvent(
        event_date=date(2026, 9, 20),
        track_id=track.id,
        discipline="THOROUGHBRED",
        event_kind="RACE",
        first_race_time=time(12, 30),
        race_count=8,
    )
    db.add(event)
    db.flush()
    manager = actor(user, region, "MANAGER")
    workday, created = adopt_external_event(db, event.id, manager)
    assert created and workday.current_published_revision_id is None
    same, created = adopt_external_event(db, event.id, manager)
    assert not created and same.id == workday.id
    assert len(external_calendar_items(db, manager, date(2026, 9, 1), date(2026, 10, 1))) == 1
    draft = db.get(WorkdayRevision, workday.current_draft_revision_id)
    workday.current_published_revision_id = draft.id
    workday.current_draft_revision_id = None
    draft.state = "PUBLISHED"
    db.flush()
    assert external_calendar_items(db, manager, date(2026, 9, 1), date(2026, 10, 1)) == []
    assert db.scalar(select(func.count()).select_from(Workday)) == 1


def test_typed_import_rejects_malformed_and_literal_colour() -> None:
    with pytest.raises(ValueError, match="Invalid import field"):
        parse_bundle('{"version":"2"}')
    with pytest.raises(ValueError, match="Invalid import field"):
        parse_bundle('{"tracks":[{"name":"Te Rapa","region":"Northern","colour":"#fff"}]}')
    with pytest.raises(ValueError, match="forbidden field"):
        parse_bundle('{"people":[],"accessToken":"nope"}')


class FixtureAdapter:
    provider = "LOVE_RACING"

    def __init__(self, observations=None, *, warnings=None, failure: Exception | None = None):
        self.observations = observations or []
        self.warnings = warnings or []
        self.failure = failure
        self.fetches = 0

    def fetch(self, start, end):  # type: ignore[no-untyped-def]
        self.fetches += 1
        if self.failure:
            raise self.failure
        return {"fixture": True}

    def normalize(self, payload, start, end):  # type: ignore[no-untyped-def]
        return NormalizedProviderResult(
            self.observations,
            self.warnings,
            {"calendar": "PARTIAL" if self.warnings else "OK"},
        )


def test_refresh_is_idempotent_and_records_provider_metrics(db):  # type: ignore[no-untyped-def]
    user, _region, _track = foundation(db)
    state = ExternalProviderState(provider="LOVE_RACING", enabled=True, status="READY")
    db.add(state)
    db.commit()
    adapter = FixtureAdapter([observation("LOVE_RACING", "Te Rapa")])
    first = refresh_provider(db, "LOVE_RACING", actor_user_id=user.id, adapter=adapter)
    assert first.status == "OK" and first.created == 1
    second = refresh_provider(db, "LOVE_RACING", actor_user_id=user.id, adapter=adapter)
    assert second.status == "OK" and second.duplicates == 1
    assert db.scalar(select(func.count()).select_from(ExternalCalendarEvent)) == 1
    state = db.get(ExternalProviderState, "LOVE_RACING")
    assert state.last_success_at is not None and state.observations_found == 1


def test_partial_refresh_keeps_valid_observations_and_fetch_failure_keeps_good_data(db):  # type: ignore[no-untyped-def]
    user, _region, _track = foundation(db)
    db.add(ExternalProviderState(provider="LOVE_RACING", enabled=True, status="READY"))
    db.commit()
    partial = refresh_provider(
        db,
        "LOVE_RACING",
        actor_user_id=user.id,
        adapter=FixtureAdapter([observation("LOVE_RACING", "Te Rapa")], warnings=["one bad row"]),
    )
    assert partial.status == "PARTIAL" and partial.created == 1
    failed = refresh_provider(
        db,
        "LOVE_RACING",
        actor_user_id=user.id,
        adapter=FixtureAdapter(failure=RuntimeError("upstream unavailable")),
    )
    assert failed.status == "ERROR"
    assert db.scalar(select(func.count()).select_from(ExternalCalendarEvent)) == 1
    assert db.get(ExternalProviderState, "LOVE_RACING").status == "ERROR"


def test_disabled_provider_does_not_fetch(db):  # type: ignore[no-untyped-def]
    db.add(ExternalProviderState(provider="LOVE_RACING", enabled=False, status="DISABLED"))
    db.commit()
    adapter = FixtureAdapter()
    result = refresh_provider(db, "LOVE_RACING", adapter=adapter)
    assert result.status == "DISABLED"
    assert adapter.fetches == 0


def test_unmapped_refresh_counts_unresolved_not_created_events(db):  # type: ignore[no-untyped-def]
    db.add(ExternalProviderState(provider="LOVE_RACING", enabled=True, status="READY"))
    db.commit()
    result = refresh_provider(
        db,
        "LOVE_RACING",
        adapter=FixtureAdapter([observation("LOVE_RACING", "Unknown Venue")]),
    )
    assert result.status == "PARTIAL"
    assert result.unresolved == 1 and result.created == 0
    assert db.scalar(select(func.count()).select_from(ExternalCalendarEvent)) == 0


def test_source_inventory_groups_unique_provider_identity_and_suppresses_club_suggestion(db):  # type: ignore[no-untyped-def]
    user = User(email="inventory@example.test", display_name="Inventory Admin", credential_hash="x")
    region = Region(name="Northern")
    db.add_all([user, region])
    db.flush()
    track = Track(name="Te Rapa", region_id=region.id, palette_slot=1)
    db.add(track)
    for index in range(20):
        db.add(
            ExternalEventObservation(
                provider="LOVE_RACING",
                provider_event_id=f"love-{index}",
                payload_hash=f"love-{index:02d}",
                source_track_name="Te Rapa",
                parsed_facts={
                    "event_date": f"2026-09-{index % 9 + 1:02d}",
                    "discipline": "THOROUGHBRED",
                    "event_kind": "RACE" if index < 10 else "TRIAL",
                },
                raw_payload={},
            )
        )
    db.add(
        ExternalEventObservation(
            provider="HRNZ",
            provider_event_id="club-1",
            payload_hash="club-1",
            source_track_name="Te Rapa",
            parsed_facts={
                "event_date": "2026-10-01",
                "discipline": "HARNESS",
                "event_kind": "RACE",
                "venue_confidence": "CLUB_ONLY",
            },
            raw_payload={},
        )
    )
    db.flush()
    rows = source_inventory(db)
    assert len(rows) == 2
    love = next(row for row in rows if row.provider == "LOVE_RACING")
    assert love.observation_count == 20
    assert love.event_kinds == ("RACE", "TRIAL")
    assert love.suggested_track_name == "Te Rapa"
    club = next(row for row in rows if row.provider == "HRNZ")
    assert club.club_only is True
    assert club.suggested_track_name is None
    confirm_track_mapping(db, "LOVE_RACING", "  TE   RAPA ", track.id, user.id)
    mapped = next(
        row
        for row in source_inventory(db, unmatched_only=False)
        if row.provider == "LOVE_RACING"
    )
    assert mapped.current_track_name == "Te Rapa"
    assert mapped.current_region_name == "Northern"


def test_mapping_import_is_previewable_idempotent_and_reconciles_pending(db):  # type: ignore[no-untyped-def]
    user = User(email="mapping@example.test", display_name="Mapping Admin", credential_hash="x")
    region = Region(name="Northern")
    db.add_all([user, region])
    db.flush()
    track = Track(name="Cambridge", region_id=region.id, palette_slot=1)
    db.add(track)
    db.add(
        ExternalEventObservation(
            provider="HRNZ",
            provider_event_id="pending-cambridge",
            payload_hash="pending-cambridge",
            source_track_name="Cambridge Raceway",
            parsed_facts={
                "event_date": "2026-10-01",
                "discipline": "HARNESS",
                "event_kind": "RACE",
                "status": "SCHEDULED",
            },
            raw_payload={},
        )
    )
    db.flush()
    bundle = parse_bundle(
        json.dumps(
            {
                "version": "1",
                "external_track_mappings": [
                    {
                        "provider": "HRNZ",
                        "external_track_name": "Cambridge Raceway",
                        "track": "Cambridge",
                        "region": "Northern",
                    }
                ],
            }
        )
    )
    before = db.scalar(select(func.count()).select_from(ExternalTrackMapping))
    preview = preview_bundle(db, bundle)
    assert preview.valid and preview.counts["external_track_mappings"] == {"create": 1}
    assert db.scalar(select(func.count()).select_from(ExternalTrackMapping)) == before
    apply_bundle(db, bundle, user.id)
    db.flush()
    observation_row = db.scalar(
        select(ExternalEventObservation).where(
            ExternalEventObservation.provider_event_id == "pending-cambridge"
        )
    )
    assert observation_row.event_id is not None and observation_row.mapping_state == "MAPPED"
    assert db.scalar(select(func.count()).select_from(Workday)) == 0
    assert preview_bundle(db, bundle).counts["external_track_mappings"] == {"match": 1}
    mapping_count = db.scalar(select(func.count()).select_from(ExternalTrackMapping))
    apply_bundle(db, bundle, user.id)
    db.flush()
    assert db.scalar(select(func.count()).select_from(ExternalTrackMapping)) == mapping_count


def test_mapping_import_supports_bundle_track_and_blocks_conflict_missing_or_invalid(db):  # type: ignore[no-untyped-def]
    user = User(email="bundle-map@example.test", display_name="Bundle Admin", credential_hash="x")
    north = Region(name="Northern")
    central = Region(name="Central")
    db.add_all([user, north, central])
    db.flush()
    other = Track(name="Other", region_id=central.id, palette_slot=1)
    db.add(other)
    db.flush()
    confirm_track_mapping(db, "LOVE_RACING", "Te Rapa", other.id, user.id)
    bundle = parse_bundle(
        '{"tracks":[{"name":"Te Rapa","region":"Northern"}],'
        '"external_track_mappings":[{"provider":"HRNZ","external_track_name":"Te Rapa",'
        '"track":"Te Rapa","region":"Northern"}]}'
    )
    assert preview_bundle(db, bundle).valid
    apply_bundle(db, bundle, user.id)
    db.flush()
    assert mapped_track(db, "HRNZ", "Te Rapa").region_id == north.id

    conflict = parse_bundle(
        '{"external_track_mappings":[{"provider":"LOVE_RACING",'
        '"external_track_name":"Te Rapa","track":"Te Rapa","region":"Northern"}]}'
    )
    conflict_plan = preview_bundle(db, conflict)
    assert not conflict_plan.valid
    assert conflict_plan.counts["external_track_mappings"]["conflict"] == 1
    late_conflict = parse_bundle(
        '{"tracks":[{"name":"Late Conflict Track","region":"Northern"}],'
        '"external_track_mappings":[{"provider":"LOVE_RACING",'
        '"external_track_name":"Te Rapa","track":"Late Conflict Track",'
        '"region":"Northern"}]}'
    )
    with pytest.raises(ValueError, match="Resolve import conflicts"):
        apply_bundle(db, late_conflict, user.id)
    assert db.scalar(select(Track).where(Track.name == "Late Conflict Track")) is None
    missing = parse_bundle(
        '{"external_track_mappings":[{"provider":"HRNZ","external_track_name":"Missing",'
        '"track":"Missing","region":"Northern"}]}'
    )
    assert preview_bundle(db, missing).counts["external_track_mappings"]["missing_track"] == 1
    invalid = parse_bundle(
        '{"external_track_mappings":[{"provider":"UNKNOWN","external_track_name":"Te Rapa",'
        '"track":"Te Rapa","region":"Northern"}]}'
    )
    assert preview_bundle(db, invalid).counts["external_track_mappings"]["invalid_provider"] == 1


def test_structural_export_is_safe_strict_and_round_trips_to_matches(db):  # type: ignore[no-untyped-def]
    user, _region, _track = foundation(db)
    group = CrewGroup(name="Camera Crew")
    db.add(group)
    db.flush()
    db.add(BasePosition(name="Side 1", crew_group_id=group.id))
    db.flush()
    exported = structural_master_data_bundle(db)
    raw = json.dumps(exported)
    for forbidden in (
        "credential_hash",
        "password",
        "passkey",
        "session",
        "trusted_device",
        "push_subscription",
        "private",
    ):
        assert forbidden not in raw.casefold()
    assert "people" not in exported
    parsed = parse_bundle(raw)
    plan = preview_bundle(db, parsed)
    assert plan.valid
    assert all(values.get("create", 0) == 0 for values in plan.counts.values())
    assert user.email not in raw
