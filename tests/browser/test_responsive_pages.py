from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
import uuid
from datetime import time as clock_time
from datetime import timedelta
from pathlib import Path

import httpx
import pytest
from playwright.sync_api import Page, sync_playwright
from sqlalchemy import select

from app.auth.security import hash_credential, token_hash
from app.branding.models import SystemBranding
from app.catalog.models import BasePosition, CrewGroup, Region, Track, Vehicle
from app.core.database import Base, SessionLocal, engine
from app.core.enums import CapabilitySignal, Role
from app.core.time import local_today, utcnow
from app.external_calendar.models import (
    ExternalCalendarEvent,
    ExternalEventObservation,
    ExternalProviderState,
)
from app.identity.models import Person, RoleGrant, TrustedDevice, User, UserPersonLink
from app.notices.models import OperationalNotice
from app.notifications.models import NotificationPreference
from app.rostering.models import (
    Assignment,
    OpenPositionApplication,
    PositionCapability,
    Workday,
    WorkdayRevision,
)
from app.unavailability.models import PersonUnavailability

pytestmark = pytest.mark.skipif(
    os.environ.get("ONTRACK_RUN_BROWSER_TESTS") != "1",
    reason="browser qualification is opt-in and requires installed Playwright Chromium",
)


@pytest.fixture(scope="module")
def browser_site():  # type: ignore[no-untyped-def]
    if engine.dialect.name == "sqlite":
        Base.metadata.create_all(engine)
    suffix = uuid.uuid4().hex[:10]
    with SessionLocal() as db:
        region = Region(name=f"Browser Region {suffix}", statutory_holiday_region="Auckland")
        cross_region = Region(name=f"Cross Region {suffix}")
        group = CrewGroup(name=f"Browser Crew {suffix}")
        manager = User(
            email=f"manager-{suffix}@example.com",
            display_name="Browser Manager",
            credential_hash=hash_credential("123456"),
            credential_kind="pin",
        )
        employee = User(
            email=f"employee-{suffix}@example.com",
            display_name="Browser Employee",
            credential_hash=hash_credential("654321"),
            credential_kind="pin",
        )
        admin = User(
            email=f"admin-{suffix}@example.com",
            display_name="Browser Admin",
            credential_hash=hash_credential("12345678"),
            credential_kind="pin",
        )
        viewer = User(
            email=f"viewer-{suffix}@example.com",
            display_name="Browser Viewer",
            credential_hash=hash_credential("112233"),
            credential_kind="pin",
        )
        contractor = User(
            email=f"contractor-{suffix}@example.com",
            display_name="Browser Contractor",
            credential_hash=hash_credential("246810"),
            credential_kind="pin",
            contractor_manual_extension_at=utcnow(),
            contractor_access_expires_at=utcnow() + timedelta(days=60),
        )
        person = Person(display_name="Browser Crew Member")
        contractor_person = Person(
            display_name="Browser Contractor",
            email=contractor.email,
        )
        manager_person = Person(display_name="Browser Roster Manager")
        other_person = Person(display_name="Unrelated Browser Crew")
        leave_managed_person = Person(display_name="Browser Leave Managed Crew")
        db.add_all(
            [
                region,
                cross_region,
                group,
                manager,
                employee,
                admin,
                viewer,
                contractor,
                person,
                contractor_person,
                manager_person,
                other_person,
                leave_managed_person,
            ]
        )
        db.flush()
        branding = db.get(SystemBranding, 1)
        if branding:
            branding.product_name = "On Track"
            branding.updated_by_user_id = admin.id
        else:
            db.add(SystemBranding(id=1, product_name="On Track", updated_by_user_id=admin.id))
        track = Track(
            name=f"Browser Track {suffix}", region_id=region.id, palette_slot=1,
            default_travel_minutes=30,
        )
        cross_track = Track(
            name=f"Cross Track {suffix}",
            region_id=cross_region.id,
            palette_slot=1,
        )
        position = BasePosition(name=f"Browser Position {suffix}", crew_group_id=group.id)
        head_on = BasePosition(name=f"Head On {suffix}", crew_group_id=group.id)
        director = BasePosition(name=f"Director {suffix}", crew_group_id=group.id)
        side_one = BasePosition(name="Side 1", crew_group_id=group.id)
        side_two = BasePosition(name="Side 2", crew_group_id=group.id)
        vehicle = Vehicle(name=f"Browser Vehicle {suffix}", home_region_id=region.id)
        duplicate_a = Person(display_name="John Smith", home_region_id=cross_region.id)
        duplicate_b = Person(display_name="John Smith", home_region_id=cross_region.id)
        person.home_region_id = region.id
        contractor_person.home_region_id = region.id
        manager_person.home_region_id = region.id
        leave_managed_person.home_region_id = region.id
        db.add_all(
            [
                track,
                cross_track,
                position,
                head_on,
                director,
                side_one,
                side_two,
                vehicle,
                duplicate_a,
                duplicate_b,
            ]
        )
        db.flush()
        db.add_all(
            [
                UserPersonLink(user_id=employee.id, person_id=person.id),
                UserPersonLink(user_id=contractor.id, person_id=contractor_person.id),
                UserPersonLink(user_id=manager.id, person_id=manager_person.id),
                RoleGrant(user_id=employee.id, role=Role.EMPLOYEE.value, region_id=region.id),
                RoleGrant(user_id=manager.id, role=Role.MANAGER.value, region_id=region.id),
                RoleGrant(user_id=admin.id, role=Role.ADMIN.value),
                RoleGrant(user_id=viewer.id, role=Role.VIEWER.value, region_id=region.id),
                RoleGrant(user_id=contractor.id, role=Role.CONTRACTOR.value, region_id=region.id),
                NotificationPreference(user_id=employee.id, weekly_digest=True),
                PositionCapability(
                    person_id=other_person.id,
                    base_position_id=head_on.id,
                    signal=CapabilitySignal.MANAGER_ALLOW.value,
                    changed_by_user_id=manager.id,
                ),
                PositionCapability(
                    person_id=other_person.id,
                    base_position_id=director.id,
                    signal=CapabilitySignal.MANAGER_BLOCK.value,
                    changed_by_user_id=manager.id,
                ),
                PersonUnavailability(
                    person_id=leave_managed_person.id,
                    start_date=local_today() - timedelta(days=1),
                    end_date=local_today() + timedelta(days=2),
                    note="Private browser leave note",
                    created_by_user_id=manager.id,
                ),
                PersonUnavailability(
                    person_id=other_person.id,
                    start_date=local_today() - timedelta(days=1),
                    end_date=local_today() + timedelta(days=2),
                    note="Builder-only private leave note",
                    created_by_user_id=manager.id,
                ),
                PersonUnavailability(
                    person_id=duplicate_a.id,
                    start_date=local_today() - timedelta(days=1),
                    end_date=local_today() + timedelta(days=2),
                    note="Another private browser note",
                    created_by_user_id=manager.id,
                ),
            ]
        )
        workday = Workday(region_id=region.id, created_by_user_id=manager.id)
        db.add(workday)
        db.flush()
        revision = WorkdayRevision(
            workday_id=workday.id,
            revision_number=1,
            state="PUBLISHED",
            work_date=local_today(),
            track_id=track.id,
            track_name_snapshot=track.name,

            title="Browser qualification day",
            start_time=clock_time(7, 30),
            on_track_time=clock_time(9),
            first_trial_time=clock_time(10),
            first_race_time=clock_time(11),
            last_race_time=clock_time(18),
            race_count=10,
            end_time=clock_time(19, 30),
            day_note="Browser normal Day note",
            created_by_user_id=manager.id,
            published_by_user_id=manager.id,
        )
        db.add(revision)
        db.flush()
        open_assignment = Assignment(
            revision_id=revision.id,
            base_position_id=side_one.id,
            display_name_snapshot="Side 1",
            status="OPEN",
        )
        db.add_all(
            [Assignment(
                revision_id=revision.id,
                base_position_id=position.id,
                display_name_snapshot=position.name,
                person_id=person.id,
                person_name_snapshot=person.display_name,
                status="ASSIGNED",
                note="Browser private roster detail",
                note_private=True,
            ),
            Assignment(
                revision_id=revision.id,
                base_position_id=position.id,
                display_name_snapshot="Unrelated position",
                person_id=other_person.id,
                person_name_snapshot=other_person.display_name,
                status="ASSIGNED",
                note="Unrelated private browser detail",
                note_private=True,
            ),
            Assignment(
                revision_id=revision.id,
                base_position_id=position.id,
                display_name_snapshot="Roster manager",
                person_id=manager_person.id,
                person_name_snapshot=manager_person.display_name,
                status="ASSIGNED",
            ),
            Assignment(
                revision_id=revision.id,
                base_position_id=position.id,
                display_name_snapshot="Reserve camera",
                status="TBC",
            ),
            Assignment(
                revision_id=revision.id,
                base_position_id=position.id,
                display_name_snapshot="Declined camera",
                status="MANAGER_ACTION_REQUIRED",
            ),
            open_assignment]
        )
        db.flush()
        db.add(
            OpenPositionApplication(
                revision_id=revision.id,
                slot_key=open_assignment.slot_key,
                person_id=person.id,
                status="APPLIED",
            )
        )
        db.add(
            OpenPositionApplication(
                revision_id=revision.id,
                slot_key=open_assignment.slot_key,
                person_id=other_person.id,
                status="APPLIED",
            )
        )
        workday.current_published_revision_id = revision.id
        cross_workday = Workday(region_id=cross_region.id, created_by_user_id=manager.id)
        db.add(cross_workday)
        db.flush()
        cross_revision = WorkdayRevision(
            workday_id=cross_workday.id,
            revision_number=1,
            state="PUBLISHED",
            work_date=local_today(),
            track_id=cross_track.id,
            track_name_snapshot=cross_track.name,

            title="Cross-region qualification day",
            start_time=clock_time(8),
            end_time=clock_time(17),
            created_by_user_id=manager.id,
            published_by_user_id=manager.id,
        )
        db.add(cross_revision)
        db.flush()
        db.add(
            Assignment(
                revision_id=cross_revision.id,
                base_position_id=position.id,
                display_name_snapshot=position.name,
                person_id=person.id,
                person_name_snapshot=person.display_name,
                status="ASSIGNED",
            )
        )
        cross_workday.current_published_revision_id = cross_revision.id
        contractor_workday = Workday(region_id=region.id, created_by_user_id=manager.id)
        db.add(contractor_workday)
        db.flush()
        contractor_revision = WorkdayRevision(
            workday_id=contractor_workday.id,
            revision_number=1,
            state="PUBLISHED",
            work_date=local_today() + timedelta(days=2),
            track_id=track.id,
            track_name_snapshot=track.name,
            title="Contractor browser day",
            start_time=clock_time(8),
            end_time=clock_time(17),
            created_by_user_id=manager.id,
            published_by_user_id=manager.id,
        )
        db.add(contractor_revision)
        db.flush()
        db.add_all(
            [
                Assignment(
                    revision_id=contractor_revision.id,
                    base_position_id=position.id,
                    display_name_snapshot="Contract camera",
                    person_id=contractor_person.id,
                    person_name_snapshot=contractor_person.display_name,
                    status="ASSIGNED",
                    note="Contractor own browser detail",
                    note_private=True,
                ),
                Assignment(
                    revision_id=contractor_revision.id,
                    base_position_id=head_on.id,
                    display_name_snapshot="Unrelated browser role",
                    person_id=other_person.id,
                    person_name_snapshot=other_person.display_name,
                    status="ASSIGNED",
                    note="Hidden from contractor",
                    note_private=True,
                ),
            ]
        )
        contractor_workday.current_published_revision_id = contractor_revision.id
        travel_workday = Workday(
            region_id=region.id,
            category="TRAVEL_DAY",
            created_by_user_id=manager.id,
        )
        db.add(travel_workday)
        db.flush()
        travel_revision = WorkdayRevision(
            workday_id=travel_workday.id,
            revision_number=1,
            state="PUBLISHED",
            work_date=local_today().replace(day=8),
            track_name_snapshot="Operations Transit",

            title="Travel to race meeting",
            start_time=clock_time(9),
            end_time=clock_time(16, 30),
            created_by_user_id=manager.id,
            published_by_user_id=manager.id,
        )
        db.add(travel_revision)
        db.flush()
        db.add_all(
            [
                Assignment(
                    revision_id=travel_revision.id,
                    base_position_id=position.id,
                    display_name_snapshot="Travel",
                    person_id=person.id,
                    person_name_snapshot=person.display_name,
                    status="ASSIGNED",
                ),
                Assignment(
                    revision_id=travel_revision.id,
                    base_position_id=position.id,
                    display_name_snapshot="Travel lead",
                    person_id=manager_person.id,
                    person_name_snapshot=manager_person.display_name,
                    status="ASSIGNED",
                ),
            ]
        )
        travel_workday.current_published_revision_id = travel_revision.id
        navigation_workday = Workday(region_id=region.id, created_by_user_id=manager.id)
        db.add(navigation_workday)
        db.flush()
        navigation_revision = WorkdayRevision(
            workday_id=navigation_workday.id,
            revision_number=1,
            state="PUBLISHED",
            work_date=local_today() - timedelta(days=1),
            track_id=track.id,
            track_name_snapshot=track.name,
            title="Browser previous-day navigation",
            start_time=clock_time(8),
            end_time=clock_time(17),
            created_by_user_id=manager.id,
            published_by_user_id=manager.id,
        )
        db.add(navigation_revision)
        db.flush()
        db.add(
            Assignment(
                revision_id=navigation_revision.id,
                base_position_id=position.id,
                display_name_snapshot=position.name,
                person_id=manager_person.id,
                person_name_snapshot=manager_person.display_name,
                status="ASSIGNED",
            )
        )
        navigation_workday.current_published_revision_id = navigation_revision.id
        open_workday = Workday(region_id=region.id, created_by_user_id=manager.id)
        db.add(open_workday)
        db.flush()
        open_revision = WorkdayRevision(
            workday_id=open_workday.id,
            revision_number=1,
            state="PUBLISHED",
            work_date=local_today().replace(day=22),
            track_name_snapshot="Browser Track Open Day",

            title="Open race day",
            start_time=clock_time(8),
            end_time=clock_time(17),
            created_by_user_id=manager.id,
            published_by_user_id=manager.id,
        )
        db.add(open_revision)
        db.flush()
        db.add(
            Assignment(
                revision_id=open_revision.id,
                base_position_id=position.id,
                display_name_snapshot="Open CCU",
                status="OPEN",
            )
        )
        open_workday.current_published_revision_id = open_revision.id
        notice_now = utcnow()
        active_notice_text = "Do not park on the grass today."
        expired_notice_text = "Expired browser notice"
        db.add_all(
            [
                OperationalNotice(
                    scope="GLOBAL",
                    message="Global browser crew notice",
                    starts_at=notice_now - timedelta(minutes=10),
                    expires_at=notice_now + timedelta(hours=12),
                    created_by_user_id=admin.id,
                ),
                OperationalNotice(
                    scope="REGION",
                    region_id=region.id,
                    message=active_notice_text,
                    starts_at=notice_now - timedelta(minutes=5),
                    expires_at=notice_now + timedelta(hours=12),
                    created_by_user_id=manager.id,
                ),
                OperationalNotice(
                    scope="REGION",
                    region_id=region.id,
                    message=expired_notice_text,
                    starts_at=notice_now - timedelta(days=1),
                    expires_at=notice_now - timedelta(minutes=1),
                    created_by_user_id=manager.id,
                ),
            ]
        )
        external_event = ExternalCalendarEvent(
            event_date=local_today(),
            track_id=track.id,
            external_track_name=track.name,
            discipline="THOROUGHBRED",
            event_kind="RACE",
            meeting_name="Racing Browser Track @ Browser Track",
            programme_status="COMPLETE",
            first_race_time=clock_time(12, 30),
            last_race_time=clock_time(17, 24),
            race_count=8,
            presentation_provider="LOVE_RACING",
            field_provenance={"race_count": ["LOVE_RACING"]},
        )
        db.add(external_event)
        db.flush()
        linked_race_event = ExternalCalendarEvent(
            event_date=local_today(),
            track_id=track.id,
            external_track_name=track.name,
            discipline="HARNESS",
            event_kind="RACE",
            first_race_time=clock_time(11),
            race_count=10,
            presentation_provider="API",
            field_provenance={"first_race_time": ["API"]},
        )
        db.add(linked_race_event)
        db.flush()
        workday.external_event_id = linked_race_event.id
        trial_date = local_today() + timedelta(days=1)
        if trial_date == local_today().replace(day=8):
            trial_date += timedelta(days=1)
        trial_event = ExternalCalendarEvent(
            event_date=trial_date,
            track_id=track.id,
            external_track_name=track.name,
            discipline="THOROUGHBRED",
            event_kind="TRIAL",
            first_trial_time=clock_time(10),
            presentation_provider="LOVE_RACING",
            field_provenance={"first_trial_time": ["LOVE_RACING"]},
        )
        db.add(trial_event)
        db.flush()
        trial_workday = Workday(
            region_id=region.id,
            category="TRIALS",
            racing_discipline="THOROUGHBRED",
            created_by_user_id=manager.id,
            external_event_id=trial_event.id,
        )
        db.add(trial_workday)
        db.flush()
        trial_revision = WorkdayRevision(
            workday_id=trial_workday.id,
            revision_number=1,
            state="PUBLISHED",
            work_date=trial_event.event_date,
            track_id=track.id,
            track_name_snapshot=track.name,
            title="Browser trial day",
            start_time=clock_time(8),
            on_track_time=clock_time(9),
            first_trial_time=clock_time(10),
            end_time=clock_time(14),
            created_by_user_id=manager.id,
            published_by_user_id=manager.id,
        )
        db.add(trial_revision)
        db.flush()
        db.add(
            Assignment(
                revision_id=trial_revision.id,
                base_position_id=position.id,
                display_name_snapshot=position.name,
                person_id=person.id,
                person_name_snapshot=person.display_name,
                status="ASSIGNED",
            )
        )
        trial_workday.current_published_revision_id = trial_revision.id
        db.add_all(
            [
                    ExternalEventObservation(
                        provider="HRNZ",
                        source_track_name="Unmatched Browser Track",
                    payload_hash=uuid.uuid4().hex,
                    parsed_facts={
                        "event_date": local_today().isoformat(),
                        "discipline": "HARNESS",
                        "event_kind": "TRIAL",
                    },
                    raw_payload={"meeting": "Unmatched Browser Track"},
                    mapping_state="UNMATCHED",
                        reconciliation_state="REVIEW",
                    ),
                    ExternalEventObservation(
                        provider="HRNZ",
                        source_track_name=track.name,
                        payload_hash=uuid.uuid4().hex,
                        parsed_facts={
                            "event_date": (local_today() + timedelta(days=2)).isoformat(),
                            "discipline": "HARNESS",
                            "event_kind": "TRIAL",
                        },
                        raw_payload={"meeting": track.name},
                        mapping_state="UNMATCHED",
                        reconciliation_state="REVIEW",
                    ),
                    *[
                        ExternalEventObservation(
                            provider="HRNZ",
                            source_track_name="Unmatched Browser Track",
                            payload_hash=uuid.uuid4().hex,
                            parsed_facts={
                                "event_date": (local_today() + timedelta(days=index + 1)).isoformat(),
                                "discipline": "HARNESS",
                                "event_kind": "RACE" if index % 2 else "TRIAL",
                                "venue_confidence": "EXPLICIT",
                            },
                            raw_payload={},
                            mapping_state="UNMATCHED",
                            reconciliation_state="REVIEW",
                        )
                        for index in range(5)
                    ],
                    ExternalEventObservation(
                        provider="HRNZ",
                        source_track_name="Auckland Trotting Club Browser",
                        payload_hash=uuid.uuid4().hex,
                        parsed_facts={
                            "event_date": local_today().isoformat(),
                            "discipline": "HARNESS",
                            "event_kind": "RACE",
                            "venue_confidence": "CLUB_ONLY",
                        },
                        raw_payload={},
                        mapping_state="UNMATCHED",
                        reconciliation_state="REVIEW",
                    ),
                    ExternalEventObservation(
                        provider="LOVE_RACING",
                        source_track_name="Create Browser Venue",
                        payload_hash=uuid.uuid4().hex,
                        parsed_facts={
                            "event_date": local_today().isoformat(),
                            "discipline": "THOROUGHBRED",
                            "event_kind": "RACE",
                            "venue_confidence": "EXPLICIT",
                        },
                        raw_payload={},
                        mapping_state="UNMATCHED",
                        reconciliation_state="REVIEW",
                    ),
                ExternalEventObservation(
                    event_id=external_event.id,
                    provider="API",
                    provider_event_id=f"conflict-{suffix}",
                    source_track_name=track.name,
                    payload_hash=uuid.uuid4().hex,
                    parsed_facts={"race_count": 9},
                    raw_payload={"race_count": 9},
                    mapping_state="MAPPED",
                    reconciliation_state="CONFLICT",
                ),
                ExternalEventObservation(
                    event_id=external_event.id,
                    provider="LOVE_RACING",
                    provider_event_id=f"browser-programme-{suffix}",
                    source_track_name=track.name,
                    payload_hash=uuid.uuid4().hex,
                    parsed_facts={"race_count": 8},
                    raw_payload={"race_count": 8},
                    mapping_state="MAPPED",
                    reconciliation_state="MATCHED",
                ),
                ExternalEventObservation(
                    event_id=linked_race_event.id,
                    provider="API",
                    provider_event_id=f"linked-race-{suffix}",
                    source_track_name=track.name,
                    payload_hash=uuid.uuid4().hex,
                    parsed_facts={"first_race_time": "11:00", "race_count": 10},
                    raw_payload={"first_race_time": "11:00", "race_count": 10},
                    mapping_state="MAPPED",
                    reconciliation_state="MATCHED",
                ),
                ExternalEventObservation(
                    event_id=trial_event.id,
                    provider="LOVE_RACING",
                    provider_event_id=f"trial-{suffix}",
                    source_track_name=track.name,
                    payload_hash=uuid.uuid4().hex,
                    parsed_facts={"first_trial_time": "10:00"},
                    raw_payload={"first_trial_time": "10:00"},
                    mapping_state="MAPPED",
                    reconciliation_state="MATCHED",
                ),
            ]
        )
        for provider, status, observations, created, enriched, warnings in (
            ("LOVE_RACING", "OK", 42, 4, 3, 0),
            ("HRNZ", "PARTIAL", 18, 2, 1, 1),
        ):
            provider_state = db.get(ExternalProviderState, provider)
            if provider_state is None:
                provider_state = ExternalProviderState(provider=provider)
                db.add(provider_state)
            provider_state.enabled = True
            provider_state.status = status
            provider_state.observations_found = observations
            provider_state.events_created = created
            provider_state.events_enriched = enriched
            provider_state.warning_count = warnings
        db.commit()
        values = {
            "manager": (manager.email, "123456"),
            "employee": (employee.email, "654321"),
            "admin": (admin.email, "12345678"),
            "viewer": (viewer.email, "112233"),
            "contractor": (contractor.email, "246810"),
            "contractor_workday_id": str(contractor_workday.id),
            "workday_id": str(workday.id),
            "cross_workday_id": str(cross_workday.id),
                "region_id": str(region.id),
                "region_name": region.name,
            "cross_region_id": str(cross_region.id),
            "track_id": str(track.id),
            "track_name": track.name,
            "cross_track_id": str(cross_track.id),
            "active_notice_text": active_notice_text,
            "expired_notice_text": expired_notice_text,
            "external_event_id": str(external_event.id),
            "trial_workday_id": str(trial_workday.id),
            "head_on_position_id": str(head_on.id),
            "director_position_id": str(director.id),
            "browser_person_id": str(person.id),
            "position_aware_person_id": str(other_person.id),
            "duplicate_person_ids": [str(duplicate_a.id), str(duplicate_b.id)],
        }

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    process = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.STDOUT,
        env=os.environ.copy(),
    )
    base_url = f"http://127.0.0.1:{port}"
    try:
        for _ in range(50):
            try:
                if httpx.get(base_url + "/health/live", timeout=0.5).status_code == 200:
                    break
            except httpx.HTTPError:
                time.sleep(0.1)
        else:
            raise RuntimeError("Browser qualification server did not start.")
        with sync_playwright() as playwright:
            configured_browser = os.environ.get("ONTRACK_PLAYWRIGHT_CHROMIUM_PATH", "")
            launch_options = (
                {"executable_path": str(Path(configured_browser).resolve())}
                if configured_browser and Path(configured_browser).is_file()
                else {}
            )
            browser = playwright.chromium.launch(**launch_options)
            try:
                yield browser, base_url, values
            finally:
                browser.close()
    finally:
        process.terminate()
        process.wait(timeout=10)


@pytest.mark.parametrize("width", [1280, 430, 375, 320])
def test_workday_region_tracks_and_friendly_time(browser_site, width: int) -> None:  # type: ignore[no-untyped-def]
    browser, base_url, values = browser_site
    context = browser.new_context(viewport={"width": width, "height": 900})
    page = context.new_page()
    _login(page, base_url, values["admin"])
    page.goto(base_url + "/manage/workdays/new")
    region = page.locator("[data-track-region]")
    tracks = page.locator("[data-region-track]")
    region.select_option(values["region_id"])
    assert tracks.locator(f'option[value="{values["track_id"]}"]').count() == 1
    assert tracks.locator(f'option[value="{values["cross_track_id"]}"]').count() == 0
    tracks.select_option(values["track_id"])
    region.select_option(values["cross_region_id"])
    assert tracks.input_value() == ""
    assert tracks.locator(f'option[value="{values["track_id"]}"]').count() == 0
    tracks.select_option(values["cross_track_id"])
    region.select_option(values["region_id"])
    assert tracks.input_value() == ""
    _assert_no_horizontal_overflow(page)
    _capture_page(page, f"region-track-filter-{width}.png")
    page.goto(base_url + f'/manage/workdays/{values["workday_id"]}')
    start = page.locator('input[name="start_time"]')
    if not start.is_visible():
        page.locator(".builder-inline-advanced > summary").click()
    assert start.get_attribute("type") == "time"
    start.fill("09:30")
    start.press("Tab")
    assert start.input_value() == "09:30"
    context.close()


@pytest.mark.parametrize("width", [1280, 320])
def test_live_staging_management_build_entry_and_contextual_help(browser_site, width: int) -> None:  # type: ignore[no-untyped-def]
    browser, base_url, values = browser_site
    context = browser.new_context(viewport={"width": width, "height": 900})
    page = context.new_page()
    _login(page, base_url, values["admin"])

    page.goto(base_url + "/admin")
    assert page.get_by_role("heading", name="System branding").count() == 1
    assert page.get_by_role("heading", name="Operational settings").count() == 1
    assert page.get_by_role("heading", name="Backup & recovery").count() == 1
    assert page.get_by_text("Active Regions", exact=True).count() == 0
    _assert_no_horizontal_overflow(page)
    _capture_page(page, f"admin-{width}.png")

    page.goto(base_url + "/manage/catalog")
    assert page.get_by_text("Active Regions", exact=True).count() == 1
    assert page.get_by_text("Active Tracks", exact=True).count() == 1
    assert page.get_by_text("Remove unused", exact=True).count() >= 2
    assert page.locator("details.archived-records > summary").filter(
        has_text="Archived Regions"
    ).count() == 1
    assert page.locator("details.archived-records > summary").filter(
        has_text="Archived Tracks"
    ).count() == 1
    _assert_no_horizontal_overflow(page)
    if width == 1280:
        _capture_page(page, "master-data-1280.png")

    page.goto(base_url + "/settings")
    page.get_by_role("link", name="Build", exact=True).click()
    page.wait_for_url("**/manage/workdays/new")
    assert page.locator(".builder-form").count() == 1
    assert page.get_by_text("Unsaved Builder", exact=True).count() == 1
    assert page.get_by_role("button", name="Save & Preview").count() == 1
    assert page.get_by_text("Step 1", exact=False).count() == 0
    _assert_no_horizontal_overflow(page)
    _capture_page(page, f"build-new-{width}.png")
    preset_row = page.locator("[data-assignment-row]").first
    assert preset_row.locator("[data-position-value]").input_value()
    page.locator('select[name="region_id"]').select_option(values["region_id"])
    page.locator('input[name="work_date"]').fill(
        "2030-01-10" if width == 1280 else "2030-01-11"
    )
    page.locator('input[name="work_date"]').dispatch_event("change")
    crew_option = preset_row.locator(
        '[data-picker-kind="person"] [data-picker-groups] [data-picker-option]:not([disabled])'
    ).first
    crew_option.wait_for(state="attached")
    person_input = preset_row.locator('[data-picker-kind="person"] [data-picker-input]')
    person_input.click()
    person_input.fill("")
    crew_option.wait_for(state="visible")
    crew_option.click()
    assert preset_row.locator("[data-person-value]").input_value()
    preset_row.locator('[data-picker-kind="person"] [data-picker-input]').fill("not selected")
    assert preset_row.locator("[data-person-value]").input_value() == ""
    page.locator('select[name="day_type"]').select_option("OFFICE_DAY:")
    page.once("dialog", lambda dialog: dialog.accept())
    page.get_by_role("button", name="Apply defaults").click()
    assert page.locator("[data-assignment-row]").count() == 0
    page.locator('select[name="track_id"]').select_option(values["track_id"])
    page.locator('details:has(input[name="title"]) > summary').click()
    page.locator('input[name="title"]').fill(f"Browser private draft {width}")
    invalid = page.locator(".builder-form").evaluate(
        "form => [...form.querySelectorAll(':invalid')].map(field => `${field.name}:${field.value}`)"
    )
    assert invalid == []
    with page.expect_response(
        lambda response: response.request.method == "POST"
        and response.url.endswith("/manage/workdays")
    ) as save_response:
        page.get_by_role("button", name="Save & Preview").click()
    response = save_response.value
    if response.status >= 400:
        pytest.fail(f"New Workday save returned {response.status}: {response.text()}")
    page.wait_for_url("**/manage/workdays/*/preview")
    assert page.get_by_text("Publication preview", exact=True).count() == 1

    for context_key, heading in (
        ("settings", "Settings"),
        ("admin", "Administration"),
        ("online-sources", "Online Sources"),
    ):
        page.goto(base_url + f"/help?context_key={context_key}")
        assert page.get_by_role("heading", name=heading, exact=True).count() == 1
        _assert_no_horizontal_overflow(page)
        _capture_page(page, f"help-{context_key}-{width}.png")
    context.close()

    employee_context = browser.new_context(viewport={"width": width, "height": 900})
    employee_page = employee_context.new_page()
    _login(employee_page, base_url, values["employee"])
    employee_page.goto(base_url + "/help")
    assert employee_page.get_by_role("heading", name="Help home").count() == 1
    assert employee_page.locator('a[href="/admin"]').count() == 0
    assert employee_page.locator('a[href="/manage/catalog"]').count() == 0
    assert employee_page.locator('a[href="/manage/workdays/new"]').count() == 0
    _assert_no_horizontal_overflow(employee_page)
    employee_context.close()


@pytest.mark.parametrize("width", [1280, 320])
def test_master_data_palette_changes_with_theme(browser_site, width: int) -> None:  # type: ignore[no-untyped-def]
    browser, base_url, values = browser_site
    context = browser.new_context(viewport={"width": width, "height": 900})
    page = context.new_page()
    _login(page, base_url, values["admin"])
    colours = []
    for theme in ("race-night", "daylight", "high-contrast", "track-colours"):
        _select_theme(page, base_url, theme)
        page.goto(base_url + "/manage/catalog")
        assert page.locator('input[type="color"]').count() == 0
        record = page.locator(f'form[action="/manage/catalog/tracks/{values["track_id"]}"]')
        record.locator("..").locator("summary").click()
        assert record.locator('select[name="region_id"]').is_visible()
        assert record.locator('input[name="map_reference"]').is_visible()
        colours.append(page.locator('.track-dot[data-presentation="track-01"]').first.evaluate("element => getComputedStyle(element).getPropertyValue('--track').trim()"))
        _assert_no_horizontal_overflow(page)
        _capture_page(page, f"master-data-palette-{theme}-{width}.png")
    assert len(set(colours)) == 4
    context.close()


@pytest.mark.parametrize("width", [1280, 430, 375, 320])
def test_external_source_import_preferences_and_detail(browser_site, width: int) -> None:  # type: ignore[no-untyped-def]
    browser, base_url, values = browser_site
    context = browser.new_context(viewport={"width": width, "height": 900})
    page = context.new_page()
    _login(page, base_url, values["admin"])
    page.goto(base_url + "/settings#calendar")
    browser_errors = _watch_browser_errors(page)
    minimal = page.locator('input[name="minimal_external_detail"]')
    if minimal.is_checked():
        minimal.uncheck()
        page.locator('form[action="/settings/calendar"] button').click()
        page.wait_for_url("**/settings?calendar=saved#calendar")
    page.goto(base_url + "/month")
    event_card = page.locator(f'a[href="/external-events/{values["external_event_id"]}"]')
    assert event_card.count() == 1
    _capture_page(page, f"external-event-normal-{width}.png")
    event_card.click()
    assert page.get_by_text("Technical source history").count() == 1
    assert page.get_by_role("heading", name="Racing Browser Track @ Browser Track").count() == 1
    assert page.get_by_text("First trial", exact=True).count() == 0
    assert page.get_by_text("Love Racing", exact=True).count() >= 1
    diagnostics = page.locator("details.source-programme-diagnostics")
    assert diagnostics.count() == 1
    assert diagnostics.get_attribute("open") is None
    assert page.get_by_role("button", name="Refresh programme now").count() == 1
    _assert_no_horizontal_overflow(page)
    _capture_page(page, f"external-event-detail-{width}.png")

    page.goto(base_url + "/admin/online-sources")
    assert page.get_by_text("Unmatched Browser Track").count() >= 1
    assert page.get_by_text("6 observations", exact=False).count() == 1
    assert page.get_by_text("Source identifies a club", exact=False).count() == 1
    assert page.get_by_text("Conflicting observations").count() == 1
    assert page.get_by_role("button", name="Refresh calendar now").count() == 2
    assert page.get_by_text("PARTIAL", exact=True).count() == 1
    assert page.get_by_text("Source health", exact=True).count() == 3
    assert page.get_by_text("Mapping status", exact=True).count() == 3
    assert page.get_by_text("Programme detail", exact=True).count() == 1
    assert page.get_by_text("Actual warnings / errors", exact=True).count() == 3
    assert page.get_by_text("Unmapped identities", exact=True).count() == 3
    assert page.get_by_text("Unmapped observations", exact=True).count() == 3
    suggested_record = page.locator(
        f'details.control-record:has(input[name="external_track_name"][value="{values["track_name"]}"])'
    ).first
    assert browser_errors == []
    assert suggested_record.get_by_text(
        f"Suggested: {values['track_name']}", exact=False
    ).count() == 1
    suggested_record.locator(":scope > summary").click()
    suggested = suggested_record.locator('form[action="/admin/online-sources/map"]')
    if width == 320:
        suggested.locator('select[name="track_id"]').select_option(values["track_id"])
        suggested.get_by_role("button", name="Confirm mapping").click()
        page.wait_for_url("**/admin/online-sources?mapped=1")
        assert page.locator(
            f'form:has(input[name="external_track_name"][value="{values["track_name"]}"])'
        ).count() == 0
    create_record = page.locator(
        'details.control-record:has(input[name="external_track_name"][value="Create Browser Venue"])'
    ).first
    create_record.locator(":scope > summary").click()
    create_record.get_by_text("Create Track & Map", exact=True).first.click()
    create_form = create_record.locator('form[action="/admin/online-sources/create-track-map"]')
    assert create_form.locator('select[name="region_id"]').count() == 1
    if width == 320:
        create_form.locator('select[name="region_id"]').select_option(values["region_id"])
        create_form.get_by_role("button", name="Create Track & Map").click()
        page.wait_for_url("**/admin/online-sources?created_mapped=1")
    _assert_no_horizontal_overflow(page)
    _capture_page(page, f"online-sources-{width}.png")

    structural = page.request.get(base_url + "/admin/structural-master-data.json")
    assert structural.ok
    structural_text = structural.text().lower()
    assert "credential_hash" not in structural_text
    assert "trusted_device" not in structural_text

    page.goto(base_url + "/admin/data-import")
    unique_track = f"Preview Browser {uuid.uuid4().hex[:8]}"
    unique_source = f"Preview Source {uuid.uuid4().hex[:8]}"
    page.locator('textarea[name="pasted_json"]').fill(
        '{"version":"1","tracks":[{"name":"'
        + unique_track
        + '","region":"'
        + values["region_name"]
        + '"}],"external_track_mappings":[{"provider":"LOVE_RACING",'
        + '"external_track_name":"'
        + unique_source
        + '","track":"'
        + unique_track
        + '","region":"'
        + values["region_name"]
        + '"}]}'
    )
    page.get_by_role("button", name="Parse and preview", exact=True).click()
    assert page.get_by_text("Preview summary", exact=True).count() == 1
    assert page.get_by_text("create 1", exact=False).count() >= 1
    assert page.get_by_role("button", name="Import these records").count() == 1
    mapping_details = page.locator("details.control-record").filter(
        has_text="External Track Mappings"
    )
    assert mapping_details.count() == 1
    _assert_no_horizontal_overflow(page)
    _capture_page(page, f"data-import-preview-{width}.png")
    page.get_by_role("button", name="Import these records").click()
    page.wait_for_url("**/admin/data-import?imported=1")
    assert page.get_by_text("Import completed atomically.").count() == 1

    page.goto(base_url + "/admin/data-import")
    page.locator('textarea[name="pasted_json"]').fill(
        '{"version":"1","external_track_mappings":[{"provider":"LOVE_RACING",'
        + '"external_track_name":"'
        + unique_source
        + '","track":"'
        + values["track_name"]
        + '","region":"'
        + values["region_name"]
        + '"}]}'
    )
    page.get_by_role("button", name="Parse and preview", exact=True).click()
    conflict_details = page.locator("details.control-record").filter(
        has_text="External Track Mappings"
    )
    assert conflict_details.get_by_text("conflict 1", exact=False).count() == 1
    conflict_details.locator("summary").click()
    assert conflict_details.get_by_text("Conflict", exact=True).count() == 1
    assert page.get_by_role("button", name="Import these records").count() == 0
    assert page.get_by_text("Resolve conflicts", exact=False).count() == 1
    _assert_no_horizontal_overflow(page)

    page.goto(base_url + "/admin/data-import")
    transition_capture = (
        "Deputy Web Capture\nSchedule Area References\n"
        '[{"areaId":"browser-area","areaName":"Director"}]\n'
        "Extracted Schedule Shift Records\n"
        '[{"shiftId":"browser-transition-' + str(width) + '",'
        '"employeeId":"browser-person-' + str(width) + '",'
        '"employeeName":"Browser Transition Crew",'
        '"locationName":"T- ' + values["track_name"] + '",'
        '"areaId":"browser-area",'
        '"start":"2032-02-01T20:00:00Z",'
        '"end":"2032-02-02T04:00:00Z",'
        '"note":"8 races 1210 | 1630",'
        '"isPublished":true,"isOpen":false}]'
    )
    page.locator('textarea[name="pasted_capture"]').fill(transition_capture)
    page.get_by_role("button", name="Parse and preview transition").click()
    assert page.get_by_role("heading", name="Transition preview").count() == 1
    assert page.get_by_text("Browser Transition Crew: create Person", exact=False).count() == 1
    assert page.get_by_role("button", name="Apply this transition atomically").count() == 1
    _assert_no_horizontal_overflow(page)

    page.goto(base_url + "/settings#calendar")
    minimal = page.locator('input[name="minimal_external_detail"]')
    minimal.check()
    page.locator('form[action="/settings/calendar"] button').click()
    page.wait_for_url("**/settings?calendar=saved#calendar")
    page.goto(base_url + "/month")
    event_href = f'/external-events/{values["external_event_id"]}'
    marker = page.locator(f'.external-marker:has(a[href="{event_href}"])')
    assert marker.count() == 1
    marker.locator("summary").click()
    assert marker.get_attribute("open") is not None
    event_link = marker.locator(f'a[href="{event_href}"]')
    assert event_link.is_visible()
    _assert_no_horizontal_overflow(page)
    _capture_page(page, f"external-event-minimal-{width}.png")
    event_link.click()
    page.wait_for_url(f"**{event_href}")
    context.close()


def _login(page: Page, base_url: str, credentials: tuple[str, str]) -> None:
    page.goto(base_url + "/login")
    page.locator('input[name="email"]').fill(credentials[0])
    page.locator('input[name="credential"]').fill(credentials[1])
    page.locator('button[type="submit"]').click()
    page.wait_for_url("**/month")


def _assert_page(page: Page, url: str) -> None:
    response = page.goto(url)
    assert response and response.status < 400
    assert page.locator("main").is_visible()
    _assert_no_horizontal_overflow(page)


def _assert_no_horizontal_overflow(page: Page) -> None:
    fits_viewport = page.evaluate(
        "document.documentElement.scrollWidth <= window.innerWidth + 1"
    )
    if fits_viewport:
        return

    details = page.evaluate(
        """
        () => {
          const viewportWidth = window.innerWidth;
          const describe = (element) => {
            const rect = element.getBoundingClientRect();
            const style = getComputedStyle(element);
            const classes = Array.from(element.classList)
              .map((name) => `.${CSS.escape(name)}`)
              .join("");
            const selector = `${element.tagName.toLowerCase()}${
              element.id ? `#${CSS.escape(element.id)}` : ""
            }${classes}`;
            return {
              selector,
              left: Math.round(rect.left * 100) / 100,
              right: Math.round(rect.right * 100) / 100,
              width: Math.round(rect.width * 100) / 100,
              scrollWidth: element.scrollWidth,
              clientWidth: element.clientWidth,
              display: style.display,
              minWidth: style.minWidth,
              computedWidth: style.width,
              flex: style.flex,
              gridTemplateColumns: style.gridTemplateColumns,
              whiteSpace: style.whiteSpace,
              overflowX: style.overflowX,
            };
          };
          const offenders = Array.from(document.body.querySelectorAll("*"))
            .filter((element) => {
              const rect = element.getBoundingClientRect();
              const style = getComputedStyle(element);
              const outsideViewport =
                rect.right > viewportWidth + 1 || rect.left < -1;
              const internallyOverflowing =
                element.scrollWidth > element.clientWidth + 1 &&
                !["auto", "scroll"].includes(style.overflowX);
              return outsideViewport || internallyOverflowing;
            })
            .map(describe);
          return {
            url: location.href,
            viewportWidth,
            documentScrollWidth: document.documentElement.scrollWidth,
            bodyScrollWidth: document.body.scrollWidth,
            offenders,
          };
        }
        """
    )
    raise AssertionError(f"horizontal overflow details: {details!r}")


def _assert_picker_geometry(page: Page, input_locator, menu_locator) -> None:  # type: ignore[no-untyped-def]
    input_box = input_locator.bounding_box()
    menu_box = menu_locator.bounding_box()
    assert input_box and menu_box
    viewport = page.evaluate(
        """() => {
          const viewport = window.visualViewport;
          const top = viewport?.offsetTop || 0;
          const left = viewport?.offsetLeft || 0;
          const width = viewport?.width || window.innerWidth;
          const height = viewport?.height || window.innerHeight;
          return {top, bottom: top + height, left, right: left + width};
        }"""
    )
    placement = menu_locator.get_attribute("data-placement")
    menu_top, menu_bottom = menu_box["y"], menu_box["y"] + menu_box["height"]
    menu_left, menu_right = menu_box["x"], menu_box["x"] + menu_box["width"]
    input_top, input_bottom = input_box["y"], input_box["y"] + input_box["height"]
    assert menu_top >= viewport["top"] + 3
    assert menu_bottom <= viewport["bottom"] + 1
    assert menu_left >= viewport["left"] + 3
    assert menu_right <= viewport["right"] + 1
    if placement == "below":
        assert abs(menu_top - input_bottom) <= 8
    elif placement == "above":
        assert abs(input_top - menu_bottom) <= 8
    else:
        assert placement == "sheet"
        assert menu_locator.locator("[data-picker-context]").is_visible()
    assert not (
        menu_top < viewport["top"] + 80
        and input_top > viewport["top"] + 180
        and placement != "sheet"
    )


def _watch_browser_errors(page: Page) -> list[str]:
    errors: list[str] = []

    def capture_console(message) -> None:  # type: ignore[no-untyped-def]
        if message.type == "error":
            errors.append(f"{message.text} @ {message.location}")

    page.on("console", capture_console)
    page.on("pageerror", lambda error: errors.append(str(error)))
    return errors


def _capture_page(page: Page, name: str) -> None:
    if os.environ.get("ONTRACK_CAPTURE_BROWSER_SCREENSHOTS") != "1":
        return
    output = Path("test-results/ui-fidelity")
    output.mkdir(parents=True, exist_ok=True)
    page.evaluate(
        "async () => { await document.fonts.ready; await new Promise(requestAnimationFrame); await new Promise(requestAnimationFrame); }"
    )
    page.screenshot(path=str(output / name), full_page=True)


def _capture_viewport(page: Page, name: str) -> None:
    if os.environ.get("ONTRACK_CAPTURE_BROWSER_SCREENSHOTS") != "1":
        return
    output = Path("test-results/ui-fidelity")
    output.mkdir(parents=True, exist_ok=True)
    page.evaluate(
        "async () => { await document.fonts.ready; await new Promise(requestAnimationFrame); await new Promise(requestAnimationFrame); }"
    )
    page.screenshot(path=str(output / name), full_page=False)


def _capture_locator(page: Page, selector: str, name: str) -> None:
    if os.environ.get("ONTRACK_CAPTURE_BROWSER_SCREENSHOTS") != "1":
        return
    output = Path("test-results/ui-fidelity")
    output.mkdir(parents=True, exist_ok=True)
    page.locator(selector).screenshot(path=str(output / name))


def _select_theme(page: Page, base_url: str, theme: str) -> None:
    page.goto(base_url + "/settings")
    page.locator(".theme-picker-details summary").click()
    page.locator(f'input[name="theme"][value="{theme}"]').check()
    page.locator('form[action="/settings/theme"] button').click()
    page.wait_for_url("**/settings?theme=saved")
    assert page.locator("html").get_attribute("data-theme") == theme


@pytest.mark.parametrize("width", [1280, 430, 375, 320])
def test_builder_picker_tracks_active_field_geometry(browser_site, width: int) -> None:  # type: ignore[no-untyped-def]
    browser, base_url, values = browser_site
    usable_height = 760 if width == 1280 else 540
    context = browser.new_context(
        viewport={"width": width, "height": usable_height}, has_touch=width <= 760
    )
    page = context.new_page()
    errors = _watch_browser_errors(page)
    _login(page, base_url, values["manager"])
    page.goto(base_url + f"/manage/workdays/{values['workday_id']}")

    row = page.locator("[data-assignment-row]").last
    position_picker = row.locator('[data-picker-kind="position"]')
    position_input = position_picker.locator("[data-picker-input]")
    position_menu = position_picker.locator("[data-picker-menu]")
    position_input.scroll_into_view_if_needed()
    page.evaluate("window.scrollBy(0, 90)")
    position_input.click()
    scroll_after_open = page.evaluate("window.scrollY")
    position_input.fill("Sid")
    assert abs(page.evaluate("window.scrollY") - scroll_after_open) <= 1
    assert position_picker.get_by_text("Side 1", exact=True).is_visible()
    assert position_picker.get_by_text("Side 2", exact=True).is_visible()
    _assert_picker_geometry(page, position_input, position_menu)
    _capture_viewport(page, f"builder-position-picker-open-{width}.png")
    with page.expect_response(lambda response: "/crew-picker?" in response.url) as crew_response:
        position_picker.get_by_text("Side 1", exact=True).click()
    response = crew_response.value
    assert response.ok, f"crew picker returned {response.status}: {response.text()}"

    person_picker = row.locator('[data-picker-kind="person"]')
    person_input = person_picker.locator("[data-picker-input]")
    person_menu = person_picker.locator("[data-picker-menu]")
    person_input.click()
    person_input.fill("Browser Crew")
    crew_option = person_picker.locator('[data-picker-option][data-label="Browser Crew Member"]')
    crew_option.wait_for(state="visible")
    _assert_picker_geometry(page, person_input, person_menu)
    _capture_viewport(page, f"builder-person-picker-open-{width}.png")
    page.keyboard.press("Escape")
    assert person_menu.is_hidden()

    page.get_by_role("button", name="Add position").click()
    bottom_row = page.locator("[data-assignment-row]").last
    bottom_position_picker = bottom_row.locator('[data-picker-kind="position"]')
    bottom_position_input = bottom_position_picker.locator("[data-picker-input]")
    bottom_position_menu = bottom_position_picker.locator("[data-picker-menu]")
    bottom_position_input.scroll_into_view_if_needed()
    bottom_position_input.fill("Sid")
    assert bottom_position_picker.get_by_text("Side 1", exact=True).is_visible()
    assert bottom_position_picker.get_by_text("Side 2", exact=True).is_visible()
    _assert_picker_geometry(page, bottom_position_input, bottom_position_menu)
    _capture_viewport(page, f"builder-bottom-position-picker-open-{width}.png")
    _assert_no_horizontal_overflow(page)
    assert not errors
    context.close()


@pytest.mark.parametrize("width", [1280, 430, 375, 320])
def test_leave_management_and_builder_conflicts(browser_site, width: int) -> None:  # type: ignore[no-untyped-def]
    browser, base_url, values = browser_site
    context = browser.new_context(
        viewport={"width": width, "height": 900}, has_touch=width <= 760
    )
    page = context.new_page()
    errors = _watch_browser_errors(page)
    _login(page, base_url, values["manager"])
    page.goto(base_url + f"/manage/leave?region_id={values['region_id']}")
    assert page.get_by_role("heading", name="Leave & unavailability").is_visible()
    assert page.get_by_text("Private browser leave note").is_visible()
    _assert_no_horizontal_overflow(page)
    if width in {1280, 320}:
        _capture_page(page, f"leave-management-{width}.png")

    def choose_position(row, position_id: str) -> None:  # type: ignore[no-untyped-def]
        picker = row.locator('[data-picker-kind="position"]')
        picker.locator("[data-picker-input]").click()
        with page.expect_response(lambda response: "/crew-picker?" in response.url):
            picker.locator(f'[data-picker-option][data-value="{position_id}"]').click()

    def choose_person(row, person_id: str) -> None:  # type: ignore[no-untyped-def]
        picker = row.locator('[data-picker-kind="person"]')
        option = picker.locator(f'[data-picker-option][data-value="{person_id}"]')
        picker.locator("[data-picker-input]").fill(option.get_attribute("data-label") or "")
        option.click()

    page.goto(base_url + f"/manage/workdays/{values['workday_id']}")
    assigned_row = page.locator(
        f'[data-person-value][value="{values["position_aware_person_id"]}"]'
    ).locator("xpath=ancestor::article")
    assert assigned_row.locator("[data-assignment-leave]").is_visible()

    page.get_by_role("button", name="Add position").click()
    leave_only_row = page.locator("[data-assignment-row]").last
    choose_position(leave_only_row, values["head_on_position_id"])
    choose_person(leave_only_row, values["duplicate_person_ids"][0])
    dialog = page.locator("[data-assignment-conflict-dialog]")
    assert dialog.get_by_role("heading", name="Crew member on leave").is_visible()
    assert dialog.get_by_role("button", name="Roster anyway").is_visible()
    page.keyboard.press("Escape")
    assert leave_only_row.locator("[data-person-value]").input_value() == ""
    choose_person(leave_only_row, values["duplicate_person_ids"][0])
    dialog.get_by_role("button", name="Roster anyway").click()
    assert leave_only_row.locator("[data-person-value]").input_value() == values[
        "duplicate_person_ids"
    ][0]

    page.get_by_role("button", name="Add position").click()
    combined_row = page.locator("[data-assignment-row]").last
    choose_position(combined_row, values["director_position_id"])
    choose_person(combined_row, values["position_aware_person_id"])
    assert dialog.get_by_role("heading", name="Crew member conflict").is_visible()
    assert dialog.get_by_role("button", name="Keep both").is_visible()
    dialog.get_by_role("button", name="Cancel").click()
    assert combined_row.locator("[data-person-value]").input_value() == ""
    choose_person(combined_row, values["position_aware_person_id"])
    dialog.get_by_role("button", name="Keep both").click()
    assert page.locator(
        f'[data-person-value][value="{values["position_aware_person_id"]}"]'
    ).count() == 2
    _assert_no_horizontal_overflow(page)

    page.goto(base_url + f"/manage/workdays/{values['workday_id']}")
    page.get_by_role("button", name="Add position").click()
    move_row = page.locator("[data-assignment-row]").last
    choose_position(move_row, values["director_position_id"])
    choose_person(move_row, values["position_aware_person_id"])
    dialog.get_by_role("button", name="Move to this position").click()
    assert move_row.locator("[data-person-value]").input_value() == values[
        "position_aware_person_id"
    ]
    assert page.locator(
        f'[data-person-value][value="{values["position_aware_person_id"]}"]'
    ).count() == 1

    applications = page.locator(".builder-application-panel")
    applications.locator("summary").click()
    leave_application = applications.locator(
        ".builder-application-row", has_text="Unrelated Browser Crew"
    )
    assert leave_application.get_by_text("On leave", exact=False).is_visible()
    assert leave_application.get_by_role("button", name="Select anyway").is_visible()
    assert "Private browser leave note" not in page.content()
    assert not errors
    context.close()


@pytest.mark.parametrize("width", [1280, 430, 375, 320])
def test_key_pages_are_responsive(browser_site, width: int) -> None:  # type: ignore[no-untyped-def]
    browser, base_url, values = browser_site
    with SessionLocal() as db:
        branding = db.get(SystemBranding, 1)
        if branding:
            branding.product_name = "On Track"
            db.commit()
    context = browser.new_context(
        viewport={"width": width, "height": 900}, has_touch=width <= 760
    )
    page = context.new_page()
    errors = _watch_browser_errors(page)
    _assert_page(page, base_url + "/login")
    if width in {1280, 320}:
        _capture_page(page, f"login-{width}.png")
    _login(page, base_url, values["employee"])
    for path in (
        "/month",
        f"/day/{values['workday_id']}",
        "/crew",
        "/settings",
        "/open-positions",
        "/hours",
    ):
        _assert_page(page, base_url + path)
        if width <= 760:
            assert page.locator(".brand > strong:first-child").is_visible()
    page.goto(base_url + f"/day/{values['workday_id']}")
    assert page.locator(".published-roster-card").evaluate(
        "element => getComputedStyle(element).getPropertyValue('--track').trim() === getComputedStyle(document.documentElement).getPropertyValue('--track-01').trim()"
    )
    page.goto(base_url + "/month")
    assert page.locator(".fortnight-total-marker").count() >= 1
    assert page.locator(".fortnight-total-marker strong").first.inner_text().strip()
    assert page.locator(".shift-card.cross-region").count() == 1
    assert page.locator(".shift-card.cross-region").evaluate(
        "element => getComputedStyle(element).getPropertyValue('--track').trim() === getComputedStyle(document.documentElement).getPropertyValue('--track-01').trim()"
    )
    assert page.locator(".site-header.has-month-nav").is_visible()
    assert page.locator(".month-nav").is_visible()
    assert page.locator(".calendar-grid").is_visible()
    assert page.locator(".weekday:not(.weekday-total)").count() == 7
    assert page.locator(".weekday-total").count() == 1
    assert page.locator(".month-head").count() == 0
    assert page.get_by_text("Your authoritative roster").count() == 0
    assert page.get_by_role("link", name="Previous month").is_visible()
    assert page.get_by_role("link", name="Next month").is_visible()
    assert page.locator("[data-roster-nav]").count() == 1
    if width <= 760:
        assert page.locator(".brand > strong:first-child").is_visible()
        assert page.locator(".brand > strong:first-child").inner_text().strip() == "On Track"
    else:
        assert page.locator(".brand-compact").count() == 0
        assert page.locator(".brand > strong:first-child").is_visible()
    calendar_box = page.locator(".calendar-grid").bounding_box()
    upcoming_box = page.locator(".upcoming-strip").bounding_box()
    assert calendar_box and upcoming_box and upcoming_box["y"] > calendar_box["y"]
    if width <= 760:
        assert page.locator(".week-total").first.is_hidden()
        column_count = page.locator(".calendar-grid").evaluate(
            "element => getComputedStyle(element).gridTemplateColumns.split(' ').length"
        )
        assert column_count == 7
        assert page.locator(".day-cell").first.evaluate(
            "element => getComputedStyle(element).minHeight"
        ) == "72px"
        assert page.locator(".upcoming-list").evaluate(
            "element => getComputedStyle(element).gridTemplateColumns.split(' ').length"
        ) == 1
        page.get_by_role("link", name="List view").click()
        page.wait_for_url("**view=list")
        assert page.locator(".roster-list").is_visible()
        page.get_by_role("link", name="Month view").click()
        page.wait_for_url("**view=month")
        next_url = page.locator("[data-roster-nav]").get_attribute("data-next-url")
        page.evaluate(
            """() => {
              const start = new Event("touchstart");
              Object.defineProperty(start, "touches", {value: [{clientX: 280, clientY: 120}]});
              document.dispatchEvent(start);
              const end = new Event("touchend");
              Object.defineProperty(end, "changedTouches", {value: [{clientX: 120, clientY: 125}]});
              document.dispatchEvent(end);
            }"""
        )
        page.wait_for_url(f"**{next_url}")
        previous_url = page.locator("[data-roster-nav]").get_attribute("data-prev-url")
        page.evaluate(
            """() => {
              const start = new Event("touchstart");
              Object.defineProperty(start, "touches", {value: [{clientX: 90, clientY: 120}]});
              document.dispatchEvent(start);
              const end = new Event("touchend");
              Object.defineProperty(end, "changedTouches", {value: [{clientX: 250, clientY: 125}]});
              document.dispatchEvent(end);
            }"""
        )
        page.wait_for_url(f"**{previous_url}")
        unchanged_url = page.url
        for end_x, end_y in ((240, 122), (275, 260)):
            page.evaluate(
                """([endX, endY]) => {
                  const start = new Event("touchstart");
                  Object.defineProperty(start, "touches", {value: [{clientX: 280, clientY: 120}]});
                  document.dispatchEvent(start);
                  const end = new Event("touchend");
                  Object.defineProperty(end, "changedTouches", {value: [{clientX: endX, clientY: endY}]});
                  document.dispatchEvent(end);
                }""",
                [end_x, end_y],
            )
            assert page.url == unchanged_url
    else:
        assert page.locator(".week-total").first.is_visible()
        column_count = page.locator(".calendar-grid").evaluate(
            "element => getComputedStyle(element).gridTemplateColumns.split(' ').length"
        )
        assert column_count == 8
        page.keyboard.press("l")
        page.wait_for_url("**view=list")
        assert page.locator(".roster-list").is_visible()
        page.keyboard.press("m")
        page.wait_for_url("**view=month")
        assert page.locator(".calendar-grid").is_visible()
        original_label = page.locator(".month-nav > strong").inner_text()
        page.evaluate(
            """() => {
              const editor = document.createElement("div");
              editor.contentEditable = "true";
              editor.id = "keyboard-guard";
              document.body.appendChild(editor);
              editor.focus();
            }"""
        )
        guarded_url = page.url
        page.keyboard.press("n")
        assert page.url == guarded_url
        page.locator("#keyboard-guard").evaluate("element => element.remove()")
        page.locator("body").click(position={"x": 1, "y": 1})
        page.keyboard.press("n")
        page.wait_for_function(
            "label => document.querySelector('.month-nav > strong')?.textContent.trim() !== label",
            arg=original_label,
        )
        page.keyboard.press("p")
        page.wait_for_function(
            "label => document.querySelector('.month-nav > strong')?.textContent.trim() === label",
            arg=original_label,
        )
        page.goto(base_url + "/crew")
        page.keyboard.press("l")
        page.wait_for_url("**view=list")
        page.keyboard.press("m")
        page.wait_for_url("**view=month")
    page.goto(base_url + "/settings")
    page.locator(".theme-picker-details summary").click()
    assert page.locator('input[name="theme"]').count() == 20
    swatches = page.locator(".theme-swatch")
    for index in range(swatches.count()):
        rendered = swatches.nth(index).evaluate(
            "element => { const s=getComputedStyle(element); return s.backgroundImage !== 'none' || s.backgroundColor !== 'rgba(0, 0, 0, 0)'; }"
        )
        assert rendered
    target_theme = {1280: "high-contrast", 430: "race-night", 375: "daylight", 320: "jade"}[width]
    page.locator(f'input[name="theme"][value="{target_theme}"]').check()
    page.locator('form[action="/settings/theme"] button').click()
    page.wait_for_url("**/settings?theme=saved")
    assert page.locator("html").get_attribute("data-theme") == target_theme
    for path in ("/settings", "/month", f"/day/{values['workday_id']}", "/help"):
        _assert_page(page, base_url + path)
        assert page.locator("html").get_attribute("data-theme") == target_theme
        if width in {1280, 320}:
            slug = path.strip("/").replace("/", "-") or "home"
            _capture_page(page, f"{slug}-{target_theme}-{width}.png")
    if width == 1280:
        page.goto(base_url + "/month")
        theme_values = page.locator("html").evaluate(
            "element => { const style = getComputedStyle(element); return [style.getPropertyValue('--bg').trim(), style.getPropertyValue('--text').trim(), style.getPropertyValue('--accent').trim()]; }"
        )
        assert [value.lower() for value in theme_values] == ["#000000", "#ffffff", "#ffe45c"]
        card_colours = page.locator(".shift-card").first.evaluate(
            "element => { const style = getComputedStyle(element); return [style.color, style.backgroundColor, style.borderLeftColor]; }"
        )
        assert len(set(card_colours)) == 3
        page.goto(base_url + "/settings")
        assert page.locator("label").first.evaluate(
            "element => getComputedStyle(element).color"
        ) == "rgb(255, 255, 255)"
    assert not errors
    context.close()

    context = browser.new_context(viewport={"width": width, "height": 900})
    page = context.new_page()
    errors = _watch_browser_errors(page)
    _login(page, base_url, values["manager"])
    manager_theme = "daylight" if width == 375 else "race-night"
    _select_theme(page, base_url, manager_theme)
    assert page.locator("html").get_attribute("data-theme") == manager_theme
    for path in (
        "/month",
        "/crew",
        "/manage/workdays/new",
        f"/manage/workdays/{values['workday_id']}",
        f"/manage/workdays/{values['workday_id']}/preview",
        "/manage/crew",
        "/manage/accounts",
        "/admin/online-sources",
        "/manage/catalog",
        "/manage/hours",
    ):
        _assert_page(page, base_url + path)
        if width in {1280, 320}:
            slug = path.strip("/").replace("/", "-") or "home"
            _capture_page(page, f"manager-{slug}-{width}.png")
        if width <= 760:
            assert page.locator(".brand > strong:first-child").is_visible()
    page.goto(base_url + "/manage/crew")
    crew_search = page.locator("[data-live-search-input]")
    crew_search.fill("Browser Roster Manager")
    assert page.locator("[data-live-search-item]:visible").count() == 1
    assert page.get_by_role("heading", name="Browser Roster Manager").is_visible()
    crew_search.fill("")
    assert page.locator("[data-live-search-item]:visible").count() >= 2
    icon_offsets = page.locator(".site-nav .icon-button:has(svg)").evaluate_all(
        "elements => elements.map(element => { const button = element.getBoundingClientRect(); const icon = element.querySelector('svg').getBoundingClientRect(); return [Math.abs((icon.left + icon.width / 2) - (button.left + button.width / 2)), Math.abs((icon.top + icon.height / 2) - (button.top + button.height / 2))]; })"
    )
    assert icon_offsets and all(x <= 1 and y <= 1 for x, y in icon_offsets)
    page.goto(base_url + "/month?year=2040&month=1")
    empty_day = page.locator('[data-empty-build-url="/manage/workdays/new?date=2040-01-15"]')
    assert empty_day.count() == 1
    empty_day.click()
    page.wait_for_url("**/manage/workdays/new?date=2040-01-15")
    assert page.locator('input[name="work_date"]').input_value() == "2040-01-15"
    page.goto(base_url + "/month")
    calendar = page.locator(".calendar-grid")
    travel_card = calendar.locator(".shift-card", has_text="Operations Transit")
    assert travel_card.count() == 1
    assert travel_card.locator(".shift-track", has_text="Operations Transit").count() == 1
    assert travel_card.get_by_text("Travel lead", exact=True).count() == 1
    assert page.locator(".available-shift-dot", has_text="Open").count() == 1
    _capture_page(page, f"month-{width}.png")
    if width == 1280:
        page.goto(base_url + "/month")
        _capture_page(page, "month-race-night-1280.png")
    page.goto(base_url + f"/manage/workdays/{values['workday_id']}")
    _capture_page(page, f"builder-{width}.png")
    assert page.locator('script[src*="/static/builder.js?v="]').count() == 1
    assert page.get_by_role("button", name="Save & Preview").is_visible()
    rows = page.locator("[data-assignment-row]")
    assert rows.count() >= 5
    manager_action = rows.filter(
        has=page.locator('[data-assignment-state][value="MANAGER_ACTION_REQUIRED"]')
    ).first
    assert manager_action.count() == 1
    manager_action_index = manager_action.evaluate(
        "element => [...element.parentElement.children].indexOf(element)"
    )
    manager_action = rows.nth(manager_action_index)
    assert "Needs Manager action" in manager_action.locator(
        '[data-picker-kind="person"] [data-picker-input]'
    ).input_value()
    person_input = rows.first.locator('[data-picker-kind="person"] [data-picker-input]')
    person_input.click()
    person_input.fill("")
    person_picker = rows.first.locator('[data-picker-kind="person"]')
    assert person_picker.get_by_text("Open position", exact=True).is_visible()
    assert person_picker.get_by_text("Unassigned", exact=True).is_visible()
    assert person_picker.locator(
        ".search-picker-group > span"
    ).first.inner_text().casefold().endswith(" crew")
    assert person_picker.get_by_text("Other regions", exact=True).is_visible()
    same_date_messages = person_picker.get_by_text(
        "No position history recorded; also rostered this date", exact=True
    )
    assert same_date_messages.first.is_visible()
    same_date_warning = person_picker.locator(
        '.same-date-warning[title="Already rostered on this date"]'
    ).first
    assert same_date_warning.is_visible()
    assert same_date_warning.inner_text() == "!"
    person_input.fill("Browser Crew")
    assert person_picker.locator(
        '[data-picker-option][data-label="Browser Crew Member"]'
    ).is_visible()
    page.keyboard.press("Escape")
    manager_person_input = manager_action.locator(
        '[data-picker-kind="person"] [data-picker-input]'
    )
    manager_person_input.click()
    manager_person_input.fill("Unassigned")
    page.keyboard.press("ArrowDown")
    page.keyboard.press("Enter")
    assert manager_action.locator("[data-assignment-state]").input_value() == "TBC"
    position_input = rows.first.locator('[data-picker-kind="position"] [data-picker-input]')
    position_input.click()
    position_input.fill("Browser Position")
    page.keyboard.press("ArrowDown")
    page.keyboard.press("Enter")
    rows.first.locator("[data-toggle-advanced]").click()
    assert rows.first.locator('[name="assignment_start_time"]').is_visible()
    assert rows.first.locator("[data-private-check]").is_visible()
    before_add = rows.count()
    page.get_by_role("button", name="Add position").click()
    assert rows.count() == before_add + 1
    if width in {1280, 320}:
        new_row = rows.last
        new_position = new_row.locator('[data-picker-kind="position"] [data-picker-input]')
        new_position.fill("Head On")
        page.keyboard.press("ArrowDown")
        page.keyboard.press("Enter")
        new_person_picker = new_row.locator('[data-picker-kind="person"]')
        new_person_input = new_person_picker.locator("[data-picker-input]")
        original_person_row = page.locator(
            f'[data-assignment-row]:has([data-person-value][value="{values["browser_person_id"]}"])'
        ).first
        original_assignment_id = original_person_row.locator('[name="assignment_id"]').input_value()
        original_person_row = page.locator(
            f'[data-assignment-row]:has([name="assignment_id"][value="{original_assignment_id}"])'
        )
        original_position = original_person_row.locator(
            '[data-picker-kind="position"] [data-picker-input]'
        ).input_value()
        if width == 1280:
            original_person_row.locator("[data-assignment-advanced]").evaluate("element => element.open = true")
            original_person_row.locator("[data-standard-travel-check]").uncheck()
            original_person_row.locator("[data-standard-travel-check]").dispatch_event("change")
            original_vehicle_picker = original_person_row.locator('[data-picker-kind="vehicle"]')
            original_vehicle_picker.locator("[data-picker-input]").fill("")
            original_vehicle_picker.locator(
                '[data-picker-option][data-transport-mode="SELF_TRAVEL"]'
            ).click()
            for name, value in {
                "accommodation_name": "Shared hotel",
                "hotel_to_track_minutes_override": "25",
                "finish_destination_override": "Shared return",
                "return_travel_minutes_override": "90",
            }.items():
                original_person_row.locator(f'input[name="{name}"]').fill(value)
                original_person_row.locator(f'input[name="{name}"]').dispatch_event("change")
        new_person_input.click()
        new_person_input.fill("Browser Crew Member")
        new_person_picker.locator(
            f'[data-picker-option][data-value="{values["browser_person_id"]}"]'
        ).click()
        conflict_dialog = page.locator("[data-assignment-conflict-dialog]")
        assert conflict_dialog.is_visible()
        assert original_position in conflict_dialog.inner_text()
        if width == 1280:
            conflict_dialog.get_by_role("button", name="Cancel").click()
            assert new_row.locator("[data-person-value]").input_value() == ""
            assert original_person_row.locator("[data-person-value]").input_value() == values[
                "browser_person_id"
            ]
            new_person_input.click()
            new_person_input.fill("Browser Crew Member")
            new_person_picker.locator(
                f'[data-picker-option][data-value="{values["browser_person_id"]}"]'
            ).click()
            conflict_dialog.get_by_role("button", name="Keep both").click()
            selected_values = page.locator("[data-person-value]").evaluate_all(
                "nodes => nodes.map(node => node.value)"
            )
            assert selected_values.count(values["browser_person_id"]) == 2
            assert not new_row.locator("[data-standard-travel-check]").is_checked()
            assert new_row.locator("[data-transport-value]").input_value() == "SELF_TRAVEL"
            assert new_row.locator('input[name="accommodation_name"]').input_value() == "Shared hotel"
            assert new_row.locator('input[name="hotel_to_track_minutes_override"]').input_value() == "25"
            assert new_row.locator('input[name="finish_destination_override"]').input_value() == "Shared return"
            assert new_row.locator('input[name="return_travel_minutes_override"]').input_value() == "90"
            vehicle_picker = new_row.locator('[data-picker-kind="vehicle"]')
            vehicle_input = vehicle_picker.locator("[data-picker-input]")
            vehicle_input.fill("")
            vehicle_option = vehicle_picker.locator('[data-picker-option][data-transport-mode="VEHICLE"]').first
            vehicle_option.click()
            assert original_person_row.locator("[data-vehicle-value]").input_value() == new_row.locator(
                "[data-vehicle-value]"
            ).input_value()
            new_row.locator("[data-toggle-advanced]").click()
            new_row.locator('input[name="finish_destination_override"]').fill("Alternative return")
            new_row.locator('input[name="finish_destination_override"]').dispatch_event("change")
            new_row.locator('input[name="return_travel_minutes_override"]').fill("75")
            new_row.locator('input[name="return_travel_minutes_override"]').dispatch_event("change")
            assert original_person_row.locator('input[name="finish_destination_override"]').input_value() == (
                "Alternative return"
            )
            assert original_person_row.locator('input[name="return_travel_minutes_override"]').input_value() == "75"
            new_person_input.click()
            new_person_input.fill("Unassigned")
            new_person_picker.get_by_text("Unassigned", exact=True).click()
        else:
            conflict_dialog.get_by_role("button", name="Move to this position").click()
            assert original_person_row.locator("[data-person-value]").input_value() == ""
            assert original_person_row.locator("[data-assignment-state]").input_value() == "TBC"
            assert original_person_row.locator(
                '[data-picker-kind="position"] [data-picker-input]'
            ).input_value() == original_position
            assert new_row.locator("[data-person-value]").input_value() == values[
                "browser_person_id"
            ]
        new_person_input.click()
        new_person_input.fill("")
        position_aware_option = new_person_picker.locator(
            f'[data-picker-option][data-value="{values["position_aware_person_id"]}"]'
        )
        position_aware_option.wait_for(state="visible")
        assert "Preferred or approved" in position_aware_option.inner_text()
        new_same_date_messages = new_person_picker.get_by_text(
            "No position history recorded; also rostered this date", exact=True
        )
        assert new_same_date_messages.first.is_visible()
        new_person_input.fill("John Smith")
        duplicate_options = new_person_picker.locator(
            '[data-picker-option][data-label="John Smith"]:visible'
        )
        assert duplicate_options.count() == 2
        assert set(duplicate_options.evaluate_all("nodes => nodes.map(node => node.dataset.value)")) == set(
            values["duplicate_person_ids"]
        )
        assert duplicate_options.locator("small").count() >= 2
        page.keyboard.press("Escape")

        new_position.click()
        new_position.fill("Director")
        page.keyboard.press("ArrowDown")
        page.keyboard.press("Enter")
        new_person_input.click()
        new_person_input.fill("")
        position_aware_option.wait_for(state="visible")
        assert "Manager marked unavailable for this position" in position_aware_option.inner_text()
        assert position_aware_option.locator(
            "xpath=ancestor::div[contains(@class, 'search-picker-group')]/span"
        ).inner_text().casefold() == "other regions"
        _capture_page(page, f"new-position-picker-{width}.png")
        page.keyboard.press("Escape")
    page.keyboard.press("Escape")
    rows.last.locator("[data-assignment-advanced]").evaluate("element => element.open = true")
    rows.last.locator("[data-remove-row]").click()
    assert rows.count() == before_add
    applications = page.locator(".builder-application-panel")
    assert applications.is_visible()
    applications.locator("summary").click()
    assert applications.locator(".builder-application-position").count() == 1
    assert applications.locator(
        ".builder-application-row strong", has_text="Browser Crew Member"
    ).is_visible()
    assert applications.get_by_role("button", name="Select", exact=True).is_visible()
    assert applications.get_by_text("also rostered this date", exact=False).is_visible()
    _assert_no_horizontal_overflow(page)
    page.goto(base_url + f"/manage/workdays/{values['workday_id']}/preview")
    assert page.get_by_text("Publication preview", exact=True).is_visible()
    assert page.get_by_text("TBC / action", exact=True).is_visible()
    _capture_page(page, f"preview-{width}.png")
    page.goto(base_url + f"/day/{values['workday_id']}")
    assert page.locator(".detail-card.published-roster-card").is_visible()
    assert page.locator(".crew-roster-head").is_visible()
    assert page.locator(".hero-card").count() == 0
    assert page.locator(".timing-strip").count() == 0
    evidence = page.get_by_text("Raw Race Day Data", exact=False)
    assert evidence.is_visible()
    evidence.click()
    assert page.get_by_text("Canonical facts", exact=True).is_visible()
    _capture_page(page, f"published-day-{width}.png")
    _assert_no_horizontal_overflow(page)
    page.goto(base_url + f"/day/{values['trial_workday_id']}")
    assert page.get_by_text("Trial Day", exact=True).is_visible()
    assert page.get_by_text("First trial", exact=True).is_visible()
    assert page.get_by_text("First race", exact=True).count() == 0
    assert page.get_by_text("Raw Trial Day Data", exact=False).is_visible()
    _capture_page(page, f"published-trial-day-{width}.png")
    _assert_no_horizontal_overflow(page)
    if width > 760:
        page.goto(base_url + f"/manage/workdays/{values['workday_id']}")
        page.locator('details:has(input[name="title"]) > summary').click()
        for selector in ('input[name="title"]', 'textarea[name="day_note"]', 'select[name="track_id"]'):
            page.locator(selector).focus()
            guarded_url = page.url
            page.keyboard.press("n")
            assert page.url == guarded_url
    assert not errors
    context.close()


@pytest.mark.parametrize("width", [1280, 320])
def test_trials_builder_edit_preview_publish_and_blank_row(browser_site, width: int) -> None:  # type: ignore[no-untyped-def]
    browser, base_url, values = browser_site
    context = browser.new_context(viewport={"width": width, "height": 900}, has_touch=width <= 760)
    page = context.new_page()
    errors = _watch_browser_errors(page)
    _login(page, base_url, values["manager"])
    edit_url = base_url + f"/manage/workdays/{values['trial_workday_id']}"
    page.goto(edit_url)
    first_trial = page.locator('input[name="first_trial_time"]')
    if not first_trial.is_visible():
        page.get_by_text("Timing", exact=True).click()
    assert first_trial.is_visible()
    assert first_trial.input_value()
    assert not page.locator('input[name="first_race_time"]').is_visible()
    assert not page.locator('input[name="last_race_time"]').is_visible()
    assert not page.locator('input[name="race_count"]').is_visible()
    updated_time = "10:15" if width == 1280 else "10:30"
    first_trial.fill(updated_time)
    page.get_by_role("button", name="Add position").click()
    blank_row = page.locator("[data-assignment-row]").last
    assert blank_row.locator("[data-position-value]").input_value() == ""
    _capture_page(page, f"trials-builder-{width}.png")
    _assert_no_horizontal_overflow(page)
    page.keyboard.press("Escape")
    invalid = page.locator(".builder-form").evaluate(
        "form => [...form.querySelectorAll(':invalid')].map(field => `${field.name}:${field.value}`)"
    )
    assert invalid == []
    with page.expect_response(
        lambda response: response.request.method == "POST"
        and response.url.endswith(f"/manage/workdays/{values['trial_workday_id']}/draft")
    ) as save_response:
        page.get_by_role("button", name="Save & Preview").click()
    response = save_response.value
    if response.status >= 400:
        pytest.fail(f"Trials draft save returned {response.status}: {response.text()}")
    page.wait_for_url(f"**/manage/workdays/{values['trial_workday_id']}/preview")
    assert page.get_by_text(updated_time, exact=True).first.is_visible()
    _capture_page(page, f"trials-preview-{width}.png")
    with SessionLocal() as db:
        workday = db.get(Workday, uuid.UUID(values["trial_workday_id"]))
        assert not db.scalars(
            select(Assignment).where(
                Assignment.revision_id == workday.current_draft_revision_id,
                Assignment.base_position_id.is_(None),
            )
        ).all()
    page.get_by_role("button", name="Publish roster").click()
    page.wait_for_url(f"**/day/{values['trial_workday_id']}")
    assert str((local_today() + timedelta(days=1)).year) in page.locator("h1").first.inner_text()
    assert page.get_by_text("First trial", exact=True).is_visible()
    assert page.get_by_text(updated_time, exact=True).is_visible()
    assert page.get_by_text("First race", exact=True).count() == 0
    _capture_page(page, f"published-trial-day-corrected-{width}.png")
    _assert_no_horizontal_overflow(page)
    assert not errors
    context.close()


def test_builder_travel_and_deliberate_day_swipe(browser_site) -> None:  # type: ignore[no-untyped-def]
    browser, base_url, values = browser_site
    context = browser.new_context(viewport={"width": 375, "height": 900}, has_touch=True)
    page = context.new_page()
    _login(page, base_url, values["manager"])
    page.goto(base_url + f"/manage/workdays/{values['workday_id']}")
    travel_panel = page.locator(".builder-travel-panel")
    assert travel_panel.locator("summary").is_visible()
    assert not travel_panel.get_attribute("open")
    travel_panel.locator("summary").click()
    assert page.locator('input[name="start_origin"]').is_visible()
    assert page.locator('[data-picker-kind="vehicle"]').first.count() == 1
    assert page.locator("[data-standard-travel-check]").first.count() == 1
    _assert_no_horizontal_overflow(page)
    _capture_page(page, "builder-travel-375.png")

    page.goto(base_url + f"/day/{values['workday_id']}")
    original = page.url
    previous_url = page.locator("[data-day-nav]").get_attribute("data-prev-url")
    assert previous_url and previous_url.startswith("/day/")
    swipe_right = """() => {
      const target = document.body;
      const start = new Touch({identifier: 1, target, clientX: 80, clientY: 400});
      const end = new Touch({identifier: 1, target, clientX: 220, clientY: 405});
      target.dispatchEvent(new TouchEvent('touchstart', {touches: [start], bubbles: true}));
      target.dispatchEvent(new TouchEvent('touchend', {changedTouches: [end], bubbles: true}));
    }"""
    page.evaluate(swipe_right)
    assert page.url == original
    assert page.locator("[data-day-swipe-hint]").is_visible()
    page.evaluate(swipe_right)
    page.wait_for_url(f"**{previous_url}")
    assert page.url == base_url + previous_url
    _assert_no_horizontal_overflow(page)
    context.close()


@pytest.mark.parametrize("width", [1280, 320])
def test_notice_holiday_hours_and_fresh_auth_browser_flows(browser_site, width: int) -> None:  # type: ignore[no-untyped-def]
    browser, base_url, values = browser_site
    with SessionLocal() as db:
        branding = db.get(SystemBranding, 1)
        if branding:
            branding.product_name = "Demo it"
            db.commit()

    manager_context = browser.new_context(viewport={"width": width, "height": 900})
    page = manager_context.new_page()
    errors = _watch_browser_errors(page)
    _login(page, base_url, values["manager"])
    page.goto(base_url + "/month")
    fixture_notice = page.locator(".crew-notice-strip")
    assert fixture_notice.is_visible()
    assert page.get_by_text("Global browser crew notice", exact=True).count() == 0
    assert page.get_by_text(values["expired_notice_text"], exact=True).count() == 0
    notice_box = fixture_notice.bounding_box()
    calendar_box = page.locator(".calendar-grid").bounding_box()
    assert notice_box and calendar_box and notice_box["y"] < calendar_box["y"]
    if width == 320:
        assert notice_box["height"] < 50

    build_id = page.locator("body").get_attribute("data-build-id")
    assert build_id
    shared_asset_urls = page.eval_on_selector_all(
        'link[rel="stylesheet"], script[src]',
        "elements => elements.map(element => element.href || element.src).filter(url => url.includes('/static/'))",
    )
    assert len(shared_asset_urls) == 7
    assert any("/static/track-palette.css?v=" in url for url in shared_asset_urls)
    assert all(f"?v={build_id}" in url for url in shared_asset_urls)

    settings_link = page.locator('a[aria-label="Settings"]')
    gear = settings_link.locator('svg[viewBox="0 0 24 24"]')
    assert gear.is_visible()
    gear_box = gear.bounding_box()
    settings_box = settings_link.bounding_box()
    assert gear_box and settings_box and gear_box["width"] > 0 and gear_box["height"] > 0
    assert gear_box["x"] >= settings_box["x"] and gear_box["y"] >= settings_box["y"]
    assert gear_box["x"] + gear_box["width"] <= settings_box["x"] + settings_box["width"]
    assert gear_box["y"] + gear_box["height"] <= settings_box["y"] + settings_box["height"]
    _capture_locator(page, ".site-header", f"header-settings-gear-{width}.png")

    for product_name in ("Demo it", "On Track", "Trackside Crew"):
        with SessionLocal() as db:
            branding = db.get(SystemBranding, 1)
            assert branding
            branding.product_name = product_name
            db.commit()
        page.reload()
        brand_text = page.locator(".brand > strong")
        assert brand_text.is_visible()
        assert brand_text.inner_text() == product_name
        assert brand_text.evaluate("element => element.scrollWidth <= element.clientWidth + 1")
        _assert_no_horizontal_overflow(page)
    with SessionLocal() as db:
        branding = db.get(SystemBranding, 1)
        assert branding
        branding.product_name = "Demo it"
        db.commit()

    page.goto(base_url + "/settings#notices")
    assert page.get_by_text(values["active_notice_text"], exact=True).count() == 1
    assert page.get_by_text(values["expired_notice_text"], exact=True).count() == 1
    page.locator("#notices > details > summary").click()
    page.locator('#notices select[name="scope"]').select_option("REGION")
    page.locator('#notices select[name="region_id"]').select_option(values["region_id"])
    notice_text = f"Call time moved for browser review {width}"
    page.locator('#notices textarea[name="message"]').fill(notice_text)
    page.locator('#notices button', has_text="Create notice").click()
    page.wait_for_url("**/settings?notice=created*", wait_until="domcontentloaded")
    _select_theme(page, base_url, "race-night")
    page.goto(base_url + "/month")
    notice = page.locator(".crew-notice-strip", has_text=notice_text)
    assert notice.is_visible()
    notice_box = notice.bounding_box()
    calendar_box = page.locator(".calendar-grid").bounding_box()
    assert notice_box and calendar_box and notice_box["y"] < calendar_box["y"]
    _assert_no_horizontal_overflow(page)
    _capture_page(page, f"race-night-personal-month-notice-{width}.png")
    page.goto(base_url + "/crew")
    notice = page.locator(".crew-notice-strip", has_text=notice_text)
    assert notice.is_visible()
    notice_box = notice.bounding_box()
    calendar_box = page.locator(".calendar-grid").bounding_box()
    assert notice_box and calendar_box and notice_box["y"] < calendar_box["y"]
    _capture_page(page, f"race-night-crew-month-notice-{width}.png")

    page.goto(base_url + "/month?year=2026&month=9")
    brand = page.get_by_role("link", name="Current month roster")
    assert brand.is_visible()
    page.get_by_role("link", name="Next month").click()
    page.wait_for_url("**/month?year=2026&month=10*")
    assert page.locator(".month-nav > strong").inner_text() == "October 2026"
    page.get_by_role("link", name="Current month roster").click()
    page.wait_for_url(base_url + "/month")
    assert page.locator(".month-nav > strong").inner_text() == local_today().strftime("%B %Y")
    _capture_page(page, f"brand-current-month-return-{width}.png")
    assert not errors
    manager_context.close()

    admin_context = browser.new_context(viewport={"width": width, "height": 900})
    page = admin_context.new_page()
    errors = _watch_browser_errors(page)
    _login(page, base_url, values["admin"])
    page.goto(base_url + "/settings")
    raw_session = next(
        cookie["value"] for cookie in admin_context.cookies() if cookie["name"] == "ontrack_session"
    )
    with SessionLocal() as db:
        device = db.scalar(
            select(TrustedDevice)
            .where(
                TrustedDevice.token_hash == token_hash(raw_session),
                TrustedDevice.revoked_at.is_(None),
            )
        )
        assert device
        device.primary_authenticated_at = utcnow() - timedelta(hours=1)
        db.commit()
    page.goto(base_url + "/manage/accounts#invitations")
    page.get_by_text("Send a one-time invitation", exact=True).click()
    invitation_form = page.locator('form[action="/admin/invitations"]')
    invitation_form.locator('input[name="display_name"]').fill("Fresh auth candidate")
    invitation_form.locator('input[name="email"]').fill(f"fresh-{width}@example.test")
    invitation_form.locator('select[name="role"]').select_option("EMPLOYEE")
    invitation_form.locator('select[name="region_id"]').select_option(values["region_id"])
    invitation_form.get_by_role("button", name="Create invitation").click()
    page.wait_for_url("**/settings?reauth=required*", wait_until="domcontentloaded")
    assert page.get_by_text("The privileged action will not be replayed automatically.").is_visible()
    page.locator('#reauthenticate input[name="credential"]').fill(values["admin"][1])
    page.locator("#reauthenticate button", has_text="Re-authenticate").click()
    page.wait_for_url("**/manage/accounts", wait_until="domcontentloaded")
    assert page.get_by_role("heading", name="Accounts & access").is_visible()
    assert page.get_by_text("Fresh auth candidate", exact=True).count() == 0
    assert not errors
    admin_context.close()

    employee_context = browser.new_context(viewport={"width": width, "height": 900})
    page = employee_context.new_page()
    errors = _watch_browser_errors(page)
    _login(page, base_url, values["employee"])
    page.goto(base_url + "/month")
    assert page.locator(".timesheet-dot").count() >= 1
    page.goto(base_url + "/settings")
    assert page.locator('input[name="weekly_digest"]').is_checked()
    page.goto(base_url + "/hours")
    assert page.locator(".hours-total strong").inner_text() != "0h 0m"
    page.goto(base_url + "/month?year=2026&month=1")
    holiday = page.locator(".holiday-marker").first
    assert page.locator('.holiday-marker summary[title*="Auckland Anniversary Day"]').count() == 1
    holiday.locator("summary").click()
    assert holiday.locator(".holiday-popover").is_visible()
    _assert_no_horizontal_overflow(page)
    if width in {1280, 320}:
        _capture_page(page, f"holiday-popover-{width}.png")
    assert not errors
    employee_context.close()
    context = browser.new_context(viewport={"width": width, "height": 900})
    page = context.new_page()
    errors = _watch_browser_errors(page)
    _login(page, base_url, values["viewer"])
    _assert_page(page, base_url + f"/day/{values['workday_id']}")
    assert page.get_by_text("Browser private roster detail").is_visible()
    csrf = next(
        cookie["value"] for cookie in context.cookies() if cookie["name"] == "ontrack_csrf"
    )
    response = page.request.post(
        base_url + "/manage/workdays",
        form={
            "region_id": values["region_id"],
            "category": "RACE_DAY",
            "work_date": local_today().isoformat(),
            "csrf_token": csrf,
        },
    )
    assert response.status == 403
    assert not errors
    context.close()

    context = browser.new_context(viewport={"width": width, "height": 900})
    page = context.new_page()
    errors = _watch_browser_errors(page)
    _login(page, base_url, values["admin"])
    for path in ("/admin", "/manage/catalog", "/manage/accounts"):
        _assert_page(page, base_url + path)
    page.goto(base_url + "/admin#branding")
    if width in {1280, 320}:
        _capture_page(page, f"admin-{width}.png")
    configured_name = "Trackside Operations Rostering Portal"
    page.locator('input[name="product_name"]').fill(configured_name)
    page.get_by_role("button", name="Save product name").click()
    page.wait_for_url("**/admin#branding")
    assert page.locator(".brand > strong:first-child").inner_text() == configured_name
    assert configured_name in page.title()
    _assert_no_horizontal_overflow(page)
    assert page.locator(".brand-compact").count() == 0
    assert page.locator(".brand > strong:first-child").is_visible()
    page.goto(base_url + "/manage/catalog")
    assert page.locator(".brand > strong:first-child").inner_text() == configured_name
    assert page.get_by_text("Active Regions", exact=True).is_visible()
    assert page.get_by_role("heading", name="Vehicles", exact=True).is_visible()
    assert page.locator('#vehicles details > summary').filter(has_text="Add vehicle").is_visible()
    _assert_no_horizontal_overflow(page)
    page.goto(base_url + "/manage/audit")
    assert page.get_by_role("heading", name="Audit", exact=True).is_visible()
    assert page.locator('select[name="region_id"]').is_visible()
    assert page.locator('input[name="search"]').is_visible()
    _assert_no_horizontal_overflow(page)
    assert not errors
    context.close()


@pytest.mark.parametrize("width", [1280, 430])
def test_specific_personal_day_renders_from_cache_while_physically_offline(
    browser_site, width: int
) -> None:  # type: ignore[no-untyped-def]
    browser, base_url, values = browser_site
    context = browser.new_context(viewport={"width": width, "height": 900})
    page = context.new_page()
    errors = _watch_browser_errors(page)
    _login(page, base_url, values["employee"])
    page.goto(base_url + "/month")
    page.evaluate("async () => { await navigator.serviceWorker.ready; return true; }")
    page.reload()
    assert page.evaluate("navigator.serviceWorker.controller !== null")
    page.goto(base_url + f"/day/{values['workday_id']}")
    page.evaluate(
        """async (workdayId) => {
          const response = await fetch("/api/day/" + workdayId, {
            headers: {"X-OnTrack-Prefetch": "1"},
          });
          if (!response.ok) throw new Error("personal Day cache warm failed");
          await response.json();
        }""",
        values["workday_id"],
    )
    page.wait_for_function(
        """async (workdayId) => {
          const shellNames = (await caches.keys()).filter((key) => key.startsWith("ontrack-shell-v3-"));
          if (shellNames.length !== 1) return false;
          const shell = await caches.open(shellNames[0]);
          const marker = await shell.match("/__ontrack_active_user");
          if (!marker) return false;
          const namespace = await marker.text();
          const roster = await caches.open("ontrack-roster-" + namespace);
          return Boolean(await roster.match("/api/day/" + workdayId));
        }""",
        arg=values["workday_id"],
        timeout=10_000,
    )
    context.set_offline(True)
    page.goto(
        base_url + f"/day/{values['workday_id']}?offline-test=1",
        wait_until="domcontentloaded",
    )
    assert page.get_by_text("Offline — showing this Day as cached at").is_visible()
    assert page.get_by_text("Browser normal Day note").is_visible()
    assert page.get_by_text("Browser private roster detail").is_visible()
    assert page.get_by_text("07:30", exact=True).is_visible()
    assert page.get_by_text("10", exact=True).is_visible()
    assert page.get_by_text("Unrelated Browser Crew").count() == 0
    assert page.get_by_text("Unrelated private browser detail").count() == 0
    assert page.locator("form").count() == 0
    assert page.get_by_text("Edit private draft").count() == 0
    assert page.get_by_text("I’m not available").count() == 0
    _assert_no_horizontal_overflow(page)
    assert not errors
    context.set_offline(False)
    context.close()


@pytest.mark.parametrize("width", [1280, 320])
def test_unpublished_builder_switches_timing_fields_without_reload(browser_site, width: int) -> None:  # type: ignore[no-untyped-def]
    browser, base_url, values = browser_site
    context = browser.new_context(viewport={"width": width, "height": 900}, has_touch=width <= 760)
    page = context.new_page()
    errors = _watch_browser_errors(page)
    _login(page, base_url, values["manager"])
    page.goto(base_url + "/manage/workdays/new")
    day_type = page.locator("[data-day-type]")
    day_type.select_option("OFFICE_DAY:")
    assert not page.locator('input[name="on_track_time"]').is_visible()
    assert not page.locator('input[name="first_race_time"]').is_visible()
    assert not page.locator('[data-standard-plan-control]').is_visible()
    assert page.locator('[data-standard-plan]').is_disabled()
    page.locator(".builder-travel-panel > summary").click()
    assert page.locator('[data-standard-plan-unavailable]').is_visible()
    day_type.select_option("RACE_DAY:THOROUGHBRED")
    assert page.locator('input[name="on_track_time"]').is_visible()
    assert not page.locator('input[name="first_trial_time"]').is_visible()
    assert page.locator('input[name="first_race_time"]').is_visible()
    assert page.locator('[data-standard-plan-control]').is_visible()
    assert page.locator('[data-standard-plan]').is_enabled()
    day_type.select_option("TRIALS:THOROUGHBRED")
    assert page.locator('input[name="last_trial_time"]').is_visible()
    assert not page.locator('input[name="first_race_time"]').is_visible()
    day_type.select_option("RACE_DAY:HARNESS")
    assert not page.locator('input[name="last_trial_time"]').is_visible()
    assert page.locator('input[name="race_count"]').is_visible()
    _assert_no_horizontal_overflow(page)
    assert not errors
    context.close()


@pytest.mark.parametrize("width", [1280, 430, 375, 320])
def test_race_day_builder_live_timing_and_override_resets(browser_site, width: int) -> None:  # type: ignore[no-untyped-def]
    browser, base_url, values = browser_site
    context = browser.new_context(viewport={"width": width, "height": 1000}, has_touch=width <= 760)
    page = context.new_page()
    errors = _watch_browser_errors(page)
    _login(page, base_url, values["manager"])
    page.goto(base_url + "/manage/workdays/new")
    page.locator('select[name="track_id"]').select_option(values["track_id"])
    first_race = page.locator('input[name="first_race_time"]')
    last_race = page.locator('input[name="last_race_time"]')
    on_track = page.locator('input[name="on_track_time"]')
    start = page.locator('input[name="start_time"]')
    finish = page.locator('input[name="end_time"]')
    track_travel = page.locator('input[name="track_travel_minutes"]')
    return_travel = page.locator('input[name="return_travel_minutes"]')
    pack_up = page.locator('input[name="pack_up_minutes"]')

    assert track_travel.input_value() == "30"
    assert return_travel.input_value() == "30"
    first_race.fill("12:24")
    last_race.fill("16:47")
    page.locator(".builder-travel-panel").evaluate("element => element.open = true")
    pack_up.fill("60")
    return_travel.fill("30")
    assert on_track.input_value() == "10:15"
    assert start.input_value() == "09:45"
    assert finish.input_value() == "18:30"

    on_track.fill("10:00")
    assert page.locator('input[name="on_track_time_is_override"]').input_value() == "1"
    assert start.input_value() == "09:30"
    first_race.fill("13:24")
    assert on_track.input_value() == "10:00"
    page.get_by_role("button", name="Use calculated").nth(1).click()
    assert on_track.input_value() == "11:15"

    start.fill("09:30")
    track_travel.fill("45")
    assert start.input_value() == "09:30"
    page.get_by_role("button", name="Use calculated").first.click()
    assert start.input_value() == "10:30"
    page.get_by_role("button", name="Use Track default").click()
    assert track_travel.input_value() == "30"
    assert start.input_value() == "10:45"

    finish.fill("19:00")
    last_race.fill("17:47")
    assert finish.input_value() == "19:00"
    page.get_by_role("button", name="Use calculated").nth(2).click()
    assert finish.input_value() == "19:30"
    _assert_no_horizontal_overflow(page)
    assert not errors
    context.close()


@pytest.mark.parametrize("width", [1280, 430, 375, 320])
def test_contractor_management_and_personal_surface_is_responsive(
    browser_site, width: int
) -> None:  # type: ignore[no-untyped-def]
    browser, base_url, values = browser_site
    manager_context = browser.new_context(
        viewport={"width": width, "height": 1000}, has_touch=width <= 760
    )
    page = manager_context.new_page()
    errors = _watch_browser_errors(page)
    _login(page, base_url, values["manager"])
    page.goto(base_url + "/manage/accounts#contractor-access")
    section = page.locator("#contractor-access")
    assert section.get_by_role("heading", name="Contractor access").is_visible()
    assert section.get_by_text("Browser Contractor", exact=True).is_visible()
    assert section.get_by_text("Account linked", exact=False).is_visible()
    assert section.get_by_role("button", name="Extend access").is_visible()
    section.get_by_text("Invite a Contractor Person", exact=True).click()
    invite_form = section.locator('form[action*="/contractors/"][action$="/invite"]').first
    assert invite_form.locator('input[name="email"]').is_visible()
    assert invite_form.get_by_role("button", name="Invite Contractor").is_visible()
    _assert_no_horizontal_overflow(page)
    assert not errors
    manager_context.close()

    contractor_context = browser.new_context(
        viewport={"width": width, "height": 900}, has_touch=width <= 760
    )
    page = contractor_context.new_page()
    errors = _watch_browser_errors(page)
    _login(page, base_url, values["contractor"])
    _assert_page(page, base_url + "/month")
    assert page.get_by_text("Contractor browser day", exact=True).count() >= 1
    _assert_page(page, base_url + f"/day/{values['contractor_workday_id']}")
    assert page.get_by_text("Contractor own browser detail", exact=True).is_visible()
    assert page.get_by_text("Hidden from contractor", exact=True).count() == 0
    page.goto(base_url + "/settings")
    assert page.locator('a[href="/hours"]').is_visible()
    assert page.locator('a[href="/crew"]').count() == 0
    assert page.locator('a[href="/manage/accounts"]').count() == 0
    assert page.goto(base_url + "/crew").status == 403
    assert page.goto(base_url + "/manage/accounts").status == 403
    _assert_no_horizontal_overflow(page)
    assert not errors
    contractor_context.close()
