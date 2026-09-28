from __future__ import annotations

import io
import uuid
import zipfile
from datetime import date, time, timedelta

import pytest
from sqlalchemy import select

from app.admin.data_export import safe_data_export
from app.auth.policy import Actor, actor_for, can_manage_region
from app.auth.security import hash_credential
from app.catalog.models import BasePosition, Region, Track, Vehicle
from app.core.config import get_settings
from app.core.enums import RacingDiscipline, Role, WorkdayCategory
from app.core.time import utcnow
from app.employee.read_models import adjacent_published_workdays
from app.external_calendar.models import ExternalCalendarEvent, ExternalEventObservation
from app.external_calendar.service import adopt_external_event
from app.housekeeping.service import terminal_housekeeping
from app.identity.models import LoginThrottle, Person, RoleGrant, User, UserPersonLink
from app.notifications.models import NotificationEvent
from app.notifications.service import _notification_payload
from app.rostering.conflicts import publication_conflicts
from app.rostering.models import Assignment, Operation, TravelLeg, Workday, WorkdayRevision
from app.rostering.service import (
    DraftAssignmentInput,
    DraftDetailsInput,
    PublishConflict,
    create_workday,
    publish,
    save_draft,
)
from app.rostering.travel import TRANSPORT_VEHICLE, calculate_standard_travel


def test_ruakaka_standard_travel_rounds_last_race_without_extra_allowance(db) -> None:  # type: ignore[no-untyped-def]
    calculation = calculate_standard_travel(
        race_date=date(2026, 10, 14),
        on_track_time=time(9),
        last_race_time=time(16, 24),
        departure_time=time(12),
        travel_to_hotel_minutes=300,
        hotel_to_track_minutes=30,
        pack_up_minutes=60,
        return_travel_minutes=300,
    )
    assert calculation.travel_date == date(2026, 10, 13)
    assert (calculation.travel_start, calculation.travel_finish) == (time(12), time(17))
    assert calculation.race_start == time(8, 30)
    assert calculation.race_clear == time(16, 30)
    assert calculation.pack_up_done == time(17, 30)
    assert calculation.race_finish == time(22, 30)


def test_standard_plan_generates_one_linked_travel_participation_per_person(db) -> None:  # type: ignore[no-untyped-def]
    region = Region(name="Northern")
    db.add(region)
    db.flush()
    track = Track(name="Ruakaka", region_id=region.id, palette_slot=1)
    manager = _user(db, "travel-manager@example.test")
    person = Person(display_name="Multi-role Crew", home_region_id=region.id)
    local_person = Person(display_name="Local Crew", home_region_id=region.id)
    eng, ccu = BasePosition(name="ENG"), BasePosition(name="CCU1")
    db.add_all([track, person, local_person, eng, ccu])
    db.commit()
    workday = create_workday(
        db,
        region_id=region.id,
        category=WorkdayCategory.RACE_DAY.value,
        racing_discipline=RacingDiscipline.THOROUGHBRED.value,
        work_date=date(2026, 10, 14),
        track_id=track.id,
        title="Ruakaka",
        actor_user_id=manager.id,
    )
    draft = db.get(WorkdayRevision, workday.current_draft_revision_id)
    save_draft(
        db,
        workday_id=workday.id,
        draft_id=draft.id,
        expected_version=workday.lock_version,
        details=DraftDetailsInput(
            work_date=date(2026, 10, 14), track_id=track.id, title="Ruakaka",
            start_time=None, end_time=None, on_track_time=time(9), first_trial_time=None,
            first_race_time=None, last_race_time=time(16, 24), race_count=None,
            day_note="", change_reason="", start_origin="Clow Place",
            finish_destination="Clow Place", category="RACE_DAY",
            racing_discipline="THOROUGHBRED", standard_travel_enabled=True,
            travel_departure_time=time(12), travel_to_hotel_minutes=300,
            default_hotel="Beachfront Hotel", hotel_to_track_minutes=30,
            return_travel_minutes=300, pack_up_minutes=60,
        ),
        assignments=[
            DraftAssignmentInput(
                base_position_id=position.id,
                slot_index=None,
                person_id=person.id,
                status="ASSIGNED",
                accommodation_name="Alternative Lodge",
                hotel_to_track_minutes_override=15,
            )
            for position in (eng, ccu)
        ]
        + [
            DraftAssignmentInput(
                base_position_id=eng.id,
                slot_index=2,
                person_id=local_person.id,
                status="ASSIGNED",
                uses_standard_travel=False,
                start_time=time(8, 50),
                end_time=time(18),
            )
        ],
    )
    db.refresh(workday)
    assert workday.operation_id is not None
    assert db.get(Operation, workday.operation_id)
    assert len(list(db.scalars(select(TravelLeg).where(TravelLeg.operation_id == workday.operation_id)))) == 3
    travel_day = db.scalar(select(Workday).where(
        Workday.operation_id == workday.operation_id, Workday.category == "TRAVEL_DAY"))
    travel_revision = db.get(WorkdayRevision, travel_day.current_draft_revision_id)
    assert (travel_revision.work_date, travel_revision.start_time, travel_revision.end_time) == (
        date(2026, 10, 13), time(12), time(17))
    travel_rows = list(db.scalars(select(Assignment).where(
        Assignment.revision_id == travel_revision.id)))
    assert len(travel_rows) == 1
    assert travel_rows[0].person_id == person.id
    assert travel_rows[0].accommodation_name == "Alternative Lodge"
    race_rows = list(db.scalars(select(Assignment).where(Assignment.revision_id == draft.id)))
    assert len([row for row in race_rows if row.person_id == person.id]) == 2
    assert {row.start_time for row in race_rows if row.person_id == person.id} == {time(8, 45)}
    assert next(row for row in race_rows if row.person_id == local_person.id).start_time == time(8, 50)
    assert (draft.start_time, draft.end_time) == (time(8, 30), time(22, 30))
    db.commit()
    publish(db, workday.id, draft.id, manager.id, workday.lock_version)
    db.refresh(travel_day)
    assert travel_day.current_published_revision_id == travel_revision.id
    assert travel_day.current_draft_revision_id is None


def _user(db, email: str) -> User:  # type: ignore[no-untyped-def]
    user = User(email=email, display_name=email.split("@", 1)[0], credential_hash=hash_credential("123456"))
    db.add(user)
    db.flush()
    return user


def _published(
    db, *, region: Region, user: User, when: date, title: str, person: Person | None = None
) -> tuple[Workday, WorkdayRevision]:  # type: ignore[no-untyped-def]
    workday = Workday(region_id=region.id, category="RACE_DAY", created_by_user_id=user.id)
    db.add(workday)
    db.flush()
    revision = WorkdayRevision(
        workday_id=workday.id,
        revision_number=1,
        state="PUBLISHED",
        work_date=when,
        track_name_snapshot=title,
        title=title,
        created_by_user_id=user.id,
        published_by_user_id=user.id,
        published_at=utcnow(),
    )
    db.add(revision)
    db.flush()
    workday.current_published_revision_id = revision.id
    if person:
        db.add(
            Assignment(
                revision_id=revision.id,
                display_name_snapshot="Camera",
                person_id=person.id,
                person_name_snapshot=person.display_name,
                status="ASSIGNED",
            )
        )
    db.flush()
    return workday, revision


def test_personal_and_management_day_navigation_uses_rostered_dates(db) -> None:  # type: ignore[no-untyped-def]
    north, south, third = Region(name="Northern"), Region(name="Central"), Region(name="Southern")
    db.add_all([north, south, third])
    user = _user(db, "crew@example.test")
    manager = _user(db, "manager@example.test")
    person = Person(display_name="Crew", home_region_id=north.id)
    db.add(person)
    db.flush()
    db.add_all(
        [
            UserPersonLink(user_id=user.id, person_id=person.id),
            RoleGrant(user_id=user.id, role=Role.CONTRACTOR.value, region_id=north.id),
            RoleGrant(user_id=manager.id, role=Role.MANAGER.value, region_id=north.id),
            RoleGrant(user_id=manager.id, role=Role.MANAGER.value, region_id=south.id),
        ]
    )
    previous, _ = _published(db, region=north, user=manager, when=date(2026, 9, 1), title="Previous", person=person)
    same_date, _ = _published(db, region=north, user=manager, when=date(2026, 9, 10), title="Same", person=person)
    following, _ = _published(db, region=north, user=manager, when=date(2026, 9, 25), title="Following", person=person)
    _published(db, region=south, user=manager, when=date(2026, 9, 20), title="Management only")
    db.commit()

    assert adjacent_published_workdays(db, actor_for(db, user), date(2026, 9, 10)) == (
        previous.id,
        following.id,
    )
    manager_actor = actor_for(db, manager)
    assert can_manage_region(manager_actor, north.id) and can_manage_region(manager_actor, south.id)
    assert not can_manage_region(manager_actor, third.id)
    assert adjacent_published_workdays(db, manager_actor, date(2026, 9, 10))[1] is not None
    assert same_date.id not in adjacent_published_workdays(db, actor_for(db, user), date(2026, 9, 10))


def test_travel_transport_hotel_and_generated_title_survive_publication(db) -> None:  # type: ignore[no-untyped-def]
    region = Region(name="Builder region")
    db.add(region)
    db.flush()
    track = Track(name="Te Rapa", region_id=region.id, palette_slot=1)
    position = BasePosition(name="Camera")
    person = Person(display_name="Builder Crew")
    vehicle = Vehicle(name="Unit Van", lifecycle="ACTIVE")
    user = _user(db, "builder@example.test")
    db.add_all([track, position, person, vehicle])
    db.commit()
    workday = create_workday(
        db, region_id=region.id, category="RACE_DAY", work_date=date(2026, 11, 1),
        track_id=track.id, title="Race Day", actor_user_id=user.id,
    )
    draft = db.get(WorkdayRevision, workday.current_draft_revision_id)
    save_draft(
        db,
        workday_id=workday.id,
        draft_id=draft.id,
        expected_version=workday.lock_version,
        details=DraftDetailsInput(
            work_date=draft.work_date, track_id=track.id, title="", start_time=time(8),
            end_time=time(18), on_track_time=time(9), first_trial_time=time(10),
            first_race_time=time(12), last_race_time=time(17), race_count=8,
            day_note="", change_reason="", start_origin="Auckland depot",
            finish_destination="Auckland depot",
        ),
        assignments=[
            DraftAssignmentInput(
                base_position_id=position.id, slot_index=1, person_id=person.id,
                status="ASSIGNED", transport_mode=TRANSPORT_VEHICLE,
                vehicle_id=vehicle.id, accommodation_name="Racecourse Hotel",
            ),
            DraftAssignmentInput(
                base_position_id=position.id, slot_index=2, person_id=person.id,
                status="ASSIGNED", transport_mode="SELF_TRAVEL",
            ),
        ],
    )
    db.refresh(workday)
    db.refresh(draft)
    rows = list(db.query(Assignment).filter(Assignment.revision_id == draft.id))
    assert draft.title == "Race Day"
    assert (draft.start_origin, draft.finish_destination) == ("Auckland depot", "Auckland depot")
    assert {row.accommodation_name for row in rows} == {"Racecourse Hotel"}
    assert next(row for row in rows if row.vehicle_id).vehicle_name_snapshot == "Unit Van"
    db.commit()
    published = publish(db, workday.id, draft.id, user.id, workday.lock_version)
    assert published.start_origin == "Auckland depot"


def test_conflicts_require_server_side_publish_override_and_allow_same_workday_roles(db) -> None:  # type: ignore[no-untyped-def]
    region = Region(name="Conflict region")
    user = _user(db, "conflict-manager@example.test")
    person = Person(display_name="Double Booked")
    vehicle = Vehicle(name="Crew Van", lifecycle="ACTIVE")
    db.add_all([region, person, vehicle])
    db.flush()
    other = Workday(region_id=region.id, category="RACE_DAY", created_by_user_id=user.id)
    target = Workday(region_id=region.id, category="RACE_DAY", created_by_user_id=user.id)
    db.add_all([other, target])
    db.flush()
    other_revision = WorkdayRevision(
        workday_id=other.id, revision_number=1, state="PUBLISHED", work_date=date(2026, 10, 1),
        track_name_snapshot="Cambridge", title="Cambridge day", start_time=time(9), end_time=time(17),
        created_by_user_id=user.id, published_by_user_id=user.id, published_at=utcnow(),
    )
    draft = WorkdayRevision(
        workday_id=target.id, revision_number=1, state="DRAFT", work_date=date(2026, 10, 1),
        track_name_snapshot="Te Rapa", title="Race Day", created_by_user_id=user.id,
    )
    db.add_all([other_revision, draft])
    db.flush()
    other.current_published_revision_id = other_revision.id
    target.current_draft_revision_id = draft.id
    for revision_id, role in ((other_revision.id, "Camera"), (draft.id, "Camera"), (draft.id, "Replay")):
        db.add(
            Assignment(
                revision_id=revision_id, display_name_snapshot=role, person_id=person.id,
                person_name_snapshot=person.display_name, status="ASSIGNED",
                transport_mode=TRANSPORT_VEHICLE, vehicle_id=vehicle.id,
                vehicle_name_snapshot=vehicle.name,
            )
        )
    db.commit()
    conflicts = publication_conflicts(db, target, draft)
    assert {item.kind for item in conflicts} == {"PERSON", "VEHICLE"}
    assert all("potential same-day conflict" in item.timing for item in conflicts)
    redacted = publication_conflicts(db, target, draft, visible_region_ids=set())
    assert all("another Workday" in item.message for item in redacted)
    assert all("Cambridge" not in item.message for item in redacted)
    version = target.lock_version
    target_id, draft_id, user_id = target.id, draft.id, user.id
    db.commit()
    with pytest.raises(PublishConflict, match="Publish anyway"):
        publish(db, target_id, draft_id, user_id, version)
    db.rollback()
    published = publish(
        db, target_id, draft_id, user_id, version, confirm_conflicts=True
    )
    assert published.state == "PUBLISHED"


def test_workday_notification_tag_and_current_publication_body(db) -> None:  # type: ignore[no-untyped-def]
    region = Region(name="Notification region")
    db.add(region)
    user = _user(db, "notify@example.test")
    person = Person(display_name="Notify Crew")
    db.add(person)
    db.flush()
    db.add(UserPersonLink(user_id=user.id, person_id=person.id))
    workday, revision = _published(
        db, region=region, user=user, when=date(2026, 10, 2), title="Ellerslie", person=person
    )
    revision.start_time = time(9, 30)
    db.commit()
    tags = []
    for event_type in (
        "NIGHT_BEFORE", "ONE_HOUR_BEFORE", "ROSTER_PUBLISHED", "OPEN_POSITION_AVAILABLE"
    ):
        event = NotificationEvent(
            event_key=f"{event_type}:{uuid.uuid4()}", event_type=event_type,
            workday_id=workday.id, audience_user_id=user.id, payload={},
        )
        db.add(event)
        db.flush()
        payload = _notification_payload(db, event, user.id)
        tags.append(payload["tag"])
        assert "Ellerslie" in payload["body"] and "Camera" in payload["body"]
    assert len(set(tags)) == 1
    other_user = _user(db, "other-notify@example.test")
    assert _notification_payload(db, event, other_user.id)["tag"] != tags[0]
    other_workday, _ = _published(
        db, region=region, user=user, when=date(2026, 10, 3), title="Te Rapa"
    )
    other_event = NotificationEvent(
        event_key="other-workday", event_type="ROSTER_PUBLISHED",
        workday_id=other_workday.id, audience_user_id=user.id, payload={},
    )
    db.add(other_event)
    db.flush()
    assert _notification_payload(db, other_event, user.id)["tag"] != tags[0]
    digest = NotificationEvent(event_key="digest:one", event_type="WEEKLY_DIGEST", payload={})
    db.add(digest)
    db.flush()
    assert _notification_payload(db, digest, user.id)["tag"] == "digest:one"


def test_source_programme_title_seeds_secondary_meeting_name(db) -> None:  # type: ignore[no-untyped-def]
    region = Region(name="Source region")
    user = _user(db, "source-manager@example.test")
    db.add(region)
    db.flush()
    track = Track(name="Cambridge", region_id=region.id, palette_slot=1)
    db.add(track)
    db.flush()
    event = ExternalCalendarEvent(
        event_date=date(2026, 12, 1), track_id=track.id, discipline="HARNESS",
        event_kind="RACE", status="SCHEDULED",
    )
    db.add(event)
    db.flush()
    db.add(
        ExternalEventObservation(
            event_id=event.id, provider="HRNZ", provider_event_id="programme-1",
            payload_hash="programme-title", source_track_name="Cambridge",
            parsed_facts={"programme_title": "Waikato Trotting Club Summer Meeting"},
            raw_payload={}, mapping_state="MAPPED", reconciliation_state="MATCHED",
        )
    )
    db.commit()
    actor = Actor(
        user_id=user.id, person_id=None, global_roles=frozenset(),
        regional_roles={region.id: frozenset({Role.MANAGER.value})},
    )
    workday, created = adopt_external_event(db, event.id, actor)
    draft = db.get(WorkdayRevision, workday.current_draft_revision_id)
    assert created is True
    assert draft.title == "Waikato Trotting Club Summer Meeting"


def test_retention_defaults_export_excludes_secrets_and_housekeeping_dry_run(db, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.delenv("ONTRACK_RETENTION_DAYS", raising=False)
    get_settings.cache_clear()
    assert get_settings().retention_days == 365
    monkeypatch.setenv("ONTRACK_RETENTION_DAYS", "360")
    get_settings.cache_clear()
    assert get_settings().retention_days == 360
    user = _user(db, "export@example.test")
    secret_hash = user.credential_hash
    throttle = LoginThrottle(
        key_hash="private-throttle-key", failure_count=0,
        window_started_at=utcnow() - timedelta(days=500),
        updated_at=utcnow() - timedelta(days=500),
    )
    db.add(throttle)
    db.commit()
    result = terminal_housekeeping(db, retention_days=365, apply=False)
    assert result.counts["login_throttles"] == 1
    assert db.get(LoginThrottle, throttle.key_hash) is not None
    export = safe_data_export(db)
    with zipfile.ZipFile(io.BytesIO(export)) as archive:
        names = set(archive.namelist())
        contents = b"".join(archive.read(name) for name in names)
    assert {
        "accounts.csv",
        "workdays.csv",
        "operations.csv",
        "travel_legs.csv",
        "audit_events.csv",
    } <= names
    assert secret_hash.encode() not in contents
    assert b"credential_hash" not in contents and b"encrypted_subscription" not in contents
    get_settings.cache_clear()


def test_staging_build_id_optional_but_production_explicit() -> None:
    staging = open("compose.staging.yaml", encoding="utf-8").read()
    staging_example = open(".env.staging.example", encoding="utf-8").read()
    production = open("compose.yaml", encoding="utf-8").read()
    dockerfile = open("Dockerfile", encoding="utf-8").read()
    assert "${STAGING_ONTRACK_BUILD_ID:-}" in staging
    assert "\nSTAGING_ONTRACK_BUILD_ID=" not in staging_example
    assert "# STAGING_ONTRACK_BUILD_ID=<qualified-git-sha>" in staging_example
    assert "${ONTRACK_BUILD_ID:?" in production
    assert ".ontrack-build-id" in dockerfile and "sha256sum" in dockerfile


def test_admin_retention_copy_and_compact_checkbox_remain_concise() -> None:
    template = open("app/templates/admin.html", encoding="utf-8").read()
    stylesheet = open("app/static/redeputy.css", encoding="utf-8").read()
    assert "Retention days set to" in template
    assert "Download data" in template
    assert "Housekeeping defaults to a dry run" not in template
    assert '.compact-check input[type="checkbox"]' in stylesheet
    assert "width: auto" in stylesheet
    assert "min-height: 0" in stylesheet


def test_day_navigation_javascript_requires_two_matching_swipes() -> None:
    source = open("app/static/app.js", encoding="utf-8").read()
    assert 'lastDaySwipe.direction === direction' in source
    assert "now - lastDaySwipe.at <= 1300" in source
    assert "showDaySwipeHint(direction)" in source
    assert 'key === "n"' in source and 'key === "p"' in source
