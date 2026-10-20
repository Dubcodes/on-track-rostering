import hashlib
import io
import re
import struct
import uuid
import warnings
import zipfile
import zlib
from calendar import monthrange
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pyotp
import pytest
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

with warnings.catch_warnings():
    warnings.simplefilter("ignore", DeprecationWarning)
    from fastapi.testclient import TestClient

from app.audit.models import AuditEvent, HumanChange
from app.audit.service import record_audit
from app.auth.factors import begin_totp, generate_recovery_codes
from app.auth.policy import actor_for
from app.auth.security import hash_credential, token_hash
from app.branding.models import SystemBranding
from app.branding.service import MAX_FAVICON_BYTES
from app.catalog.models import BasePosition, CrewGroup, PositionPreset, Region, Track, TrackMap, Vehicle
from app.core.config import get_settings
from app.core.database import Base
from app.core.enums import CapabilitySignal, Role
from app.core.themes import THEME_VALUES
from app.core.time import local_today, utcnow
from app.error_reports.models import ErrorReport
from app.external_calendar.http import HTTPResponse, SourceHTTPError
from app.external_calendar.models import (
    ExternalCalendarEvent,
    ExternalEventObservation,
    ExternalTrackMapping,
)
from app.external_calendar.refresh import RefreshResult
from app.hours.service import fortnight_bounds, published_hours
from app.identity.models import (
    CredentialReset,
    Invitation,
    LoginThrottle,
    Person,
    RecoveryCode,
    RoleGrant,
    SignupRequest,
    TotpFactor,
    TrustedDevice,
    User,
    UserPersonLink,
)
from app.main import app
from app.notices.models import OperationalNotice, OperationalNoticeRegion
from app.notifications.models import NotificationEvent
from app.notifications.service import audience_user_ids
from app.rostering.models import (
    Assignment,
    OpenPositionApplication,
    Operation,
    PersonalWorkdayEntry,
    PositionCapability,
    TravelLeg,
    Workday,
    WorkdayRevision,
)
from app.system_settings.models import SystemSettings
from app.track_maps.service import refresh_automatic_map
from app.unavailability.models import PersonUnavailability


def test_new_workday_region_guard_and_initial_change_reason(routed_db) -> None:  # type: ignore[no-untyped-def]
    factory, (region_id, track_id, _position_id, _person_id) = routed_db
    with factory() as db:
        other_region = db.scalar(select(Region).where(Region.id != region_id))
        other_id = other_region.id
        outside_track = Track(name="Outside Track", region_id=other_id, palette_slot=1)
        archived = Track(name="Archived Track", region_id=region_id, lifecycle="ARCHIVED", palette_slot=1)
        db.add_all([outside_track, archived])
        db.commit()
    client = TestClient(app)
    csrf = _login(client, "manager@example.test", "123456")
    with factory() as db:
        counts_before = (
            len(list(db.scalars(select(Workday.id)))),
            len(list(db.scalars(select(WorkdayRevision.id)))),
        )
    create_page = client.get("/manage/workdays/new")
    assert create_page.status_code == 200
    assert "Unsaved Builder" in create_page.text
    assert "Save &amp; Preview" in create_page.text
    assert "Step 1" not in create_page.text
    assert "Outside Track" not in create_page.text
    assert "Archived Track" not in create_page.text
    with factory() as db:
        assert counts_before == (
            len(list(db.scalars(select(Workday.id)))),
            len(list(db.scalars(select(WorkdayRevision.id)))),
        )
    forged = client.post("/manage/workdays", data={
        "region_id": str(region_id), "track_id": str(outside_track.id),
        "work_date": "2026-09-20", "day_type": "RACE_DAY:THOROUGHBRED",
        "csrf_token": csrf,
    })
    assert forged.status_code == 200
    assert "Roster not saved" in forged.text
    assert '"detail"' not in forged.text
    with factory() as db:
        assert counts_before == (
            len(list(db.scalars(select(Workday.id)))),
            len(list(db.scalars(select(WorkdayRevision.id)))),
        )
    created = client.post("/manage/workdays", data={
        "region_id": str(region_id), "track_id": str(track_id),
        "work_date": "2026-09-20", "category": "RACE_DAY", "csrf_token": csrf,
    }, follow_redirects=False)
    assert created.status_code == 303
    url = created.headers["location"]
    assert "Reason for change" not in client.get(url).text
    with factory() as db:
        wd = db.get(Workday, uuid.UUID(url.rsplit("/", 1)[1]))
        version = wd.lock_version
    saved = client.post(url + "/details", data={
        "work_date": "2026-09-20", "track_id": str(track_id), "title": "Friendly times",
        "start_time": "930", "end_time": "1730", "on_track_time": "9:30",
        "first_trial_time": "0930", "first_race_time": "09:30", "last_race_time": "1630",
        "expected_version": version, "csrf_token": csrf,
    }, follow_redirects=False)
    assert saved.status_code == 303
    with factory() as db:
        wd = db.get(Workday, uuid.UUID(url.rsplit("/", 1)[1]))
        draft = db.get(WorkdayRevision, wd.current_draft_revision_id)
        assert draft.start_time == draft.on_track_time == draft.first_trial_time == draft.first_race_time == time(9, 30)
        assert draft.end_time == time(17, 30)
        assert wd.lock_version == version + 1


def test_unified_new_builder_saves_complete_harness_draft_atomically(routed_db) -> None:  # type: ignore[no-untyped-def]
    factory, (region_id, track_id, position_id, person_id) = routed_db
    client = TestClient(app)
    csrf = _login(client, "manager@example.test", "123456")
    picker = client.get(
        "/manage/new-workday/crew-picker",
        params={
            "region_id": str(region_id),
            "work_date": "2026-09-22",
            "position_id": str(position_id),
            "person_id": str(person_id),
        },
    )
    assert picker.status_code == 200
    assert any(
        person["id"] == str(person_id)
        for group in picker.json()["groups"]
        for person in group["people"]
    )
    saved = client.post(
        "/manage/workdays",
        data={
            "region_id": str(region_id),
            "track_id": str(track_id),
            "work_date": "2026-09-22",
            "day_type": "RACE_DAY:HARNESS",
            "title": "Harness programme",
            "start_time": "08:00",
            "end_time": "18:00",
            "assignment_id": "",
            "base_position_id": str(position_id),
            "slot_index": "1",
            "person_id": str(person_id),
            "status": "ASSIGNED",
            "note": "",
            "note_private": "1",
            "assignment_start_time": "",
            "assignment_end_time": "",
            "transport_mode": "UNASSIGNED",
            "vehicle_id": "",
            "custom_transport_text": "",
            "accommodation_name": "",
            "uses_standard_travel": "0",
            "hotel_to_track_minutes_override": "",
            "csrf_token": csrf,
        },
        follow_redirects=False,
    )
    assert saved.status_code == 303
    assert saved.headers["location"].endswith("/preview")
    workday_id = uuid.UUID(saved.headers["location"].split("/")[-2])
    with factory() as db:
        workday = db.get(Workday, workday_id)
        draft = db.get(WorkdayRevision, workday.current_draft_revision_id)
        rows = list(db.scalars(select(Assignment).where(Assignment.revision_id == draft.id)))
        assert (workday.category, workday.racing_discipline) == ("RACE_DAY", "HARNESS")
        assert (draft.title, draft.start_time, draft.end_time) == (
            "Harness programme",
            time(8),
            time(18),
        )
        assert len(rows) == 1 and rows[0].person_id == person_id


def test_unified_new_builder_accepts_unassigned_preset_rows_for_office_day(routed_db) -> None:  # type: ignore[no-untyped-def]
    factory, (region_id, track_id, position_id, _person_id) = routed_db
    client = TestClient(app)
    csrf = _login(client, "manager@example.test", "123456")
    saved = client.post(
        "/manage/workdays",
        data={
            "region_id": str(region_id),
            "track_id": str(track_id),
            "work_date": "2030-01-10",
            "day_type": "OFFICE_DAY:",
            "title": "Browser private draft",
            "assignment_id": ["", ""],
            "base_position_id": [str(position_id), str(position_id)],
            "slot_index": ["1", "2"],
            "person_id": ["", ""],
            "status": ["TBC", "TBC"],
            "note": ["", ""],
            "note_private": ["1", "1"],
            "assignment_start_time": ["", ""],
            "assignment_end_time": ["", ""],
            "transport_mode": ["UNASSIGNED", "UNASSIGNED"],
            "vehicle_id": ["", ""],
            "custom_transport_text": ["", ""],
            "accommodation_name": ["", ""],
            "uses_standard_travel": ["1", "1"],
            "hotel_to_track_minutes_override": ["", ""],
            "travel_departure_time": "12:00",
            "pack_up_minutes": "0",
            "csrf_token": csrf,
        },
        follow_redirects=False,
    )
    assert saved.status_code == 303, saved.text
    assert saved.headers["location"].endswith("/preview")
    with factory() as db:
        workday_id = uuid.UUID(saved.headers["location"].split("/")[-2])
        workday = db.get(Workday, workday_id)
        assert db.get(WorkdayRevision, workday.current_draft_revision_id).pack_up_minutes == 0


def test_track_edit_region_authority_palette_and_history(routed_db) -> None:  # type: ignore[no-untyped-def]
    factory, (region_id, track_id, position_id, person_id) = routed_db
    with factory() as db:
        destination_id = db.scalar(select(Region.id).where(Region.id != region_id))
        db.add(Track(name="Destination occupied", region_id=destination_id, palette_slot=1))
        db.commit()
    wd_id = _publish_rows(factory, region_id=region_id, position_id=position_id, work_date=date(2026, 9, 20), rows=[(person_id, "Amy Crew", "", False)])
    with factory() as db:
        wd = db.get(Workday, wd_id)
        published = db.get(WorkdayRevision, wd.current_published_revision_id)
        published.track_id = track_id
        db.commit()
        before = (wd.region_id, wd.current_published_revision_id, published.work_date, published.track_name_snapshot)
    manager = TestClient(app)
    csrf = _login(manager, "manager@example.test", "123456")
    regional_data = {"name": "Ellerslie", "region_id": str(region_id), "lifecycle": "ACTIVE", "map_reference": "", "default_travel_minutes": "30", "csrf_token": csrf}
    assert manager.post(f"/manage/catalog/tracks/{track_id}", data=regional_data, follow_redirects=False).status_code == 303
    regional_data["default_travel_minutes"] = "-1"
    assert manager.post(f"/manage/catalog/tracks/{track_id}", data=regional_data).status_code == 400
    regional_data["default_travel_minutes"] = "1441"
    assert manager.post(f"/manage/catalog/tracks/{track_id}", data=regional_data).status_code == 400
    data = {"name": "Corrected Ellerslie", "region_id": str(destination_id), "lifecycle": "ACTIVE", "map_reference": "https://example.test/map", "default_travel_minutes": "35", "csrf_token": csrf}
    assert manager.post(f"/manage/catalog/tracks/{track_id}", data=data).status_code == 403
    admin = TestClient(app)
    data["csrf_token"] = _login(admin, "admin@example.test", "99887766")
    assert 'type="color"' not in admin.get("/manage/catalog").text
    assert admin.post(f"/manage/catalog/tracks/{track_id}", data=data, follow_redirects=False).status_code == 303
    with factory() as db:
        track = db.get(Track, track_id)
        assert (track.region_id, track.palette_slot, track.map_reference, track.default_travel_minutes) == (destination_id, 2, "https://example.test/map", 35)
        wd = db.get(Workday, wd_id)
        published = db.get(WorkdayRevision, wd.current_published_revision_id)
        assert before == (wd.region_id, wd.current_published_revision_id, published.work_date, published.track_name_snapshot)
        audit = db.scalar(select(AuditEvent).where(AuditEvent.action == "track.updated"))
        assert audit is not None
        assert audit.detail["previous_default_travel_minutes"] is None
        assert audit.detail["default_travel_minutes"] == 30
    data["name"] = "Corrected Ellerslie again"
    assert admin.post(f"/manage/catalog/tracks/{track_id}", data=data, follow_redirects=False).status_code == 303
    with factory() as db:
        assert db.get(Track, track_id).palette_slot == 2


def test_public_login_and_liveness_routes_render(routed_db) -> None:  # type: ignore[no-untyped-def]
    client = TestClient(app)
    login = client.get("/login")
    assert login.status_code == 200
    assert "Good to see you" in login.text
    assert "default-src 'self'" in login.headers["content-security-policy"]
    assert client.get("/health/live").json() == {"status": "ok"}


def test_shared_asset_urls_follow_the_exact_build_identifier(routed_db, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from app import web

    original = web.get_settings()
    selected_build = {"value": "abc123"}
    monkeypatch.setattr(
        web,
        "get_settings",
        lambda: original.model_copy(update={"build_id": selected_build["value"]}),
    )
    first = TestClient(app).get("/login").text
    for asset in (
        "style.css",
        "redeputy.css",
        "branding.css",
        "app.js",
        "passkeys.js",
        "notifications.js",
    ):
        assert f"/static/{asset}?v=abc123" in first
    assert 'data-build-id="abc123"' in first

    selected_build["value"] = "def456"
    second = TestClient(app).get("/login").text
    assert "/static/redeputy.css?v=def456" in second
    assert "/static/redeputy.css?v=abc123" not in second


def test_asset_version_has_a_stable_local_fallback(routed_db, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from app import web

    settings = web.get_settings().model_copy(update={"build_id": "", "app_version": "0.3.0"})
    monkeypatch.setattr(web, "get_settings", lambda: settings)
    page = TestClient(app).get("/login").text
    assert "/static/style.css?v=0.3.0" in page
    assert 'data-build-id="0.3.0"' in page


def test_login_post_accepts_trusted_proxy_without_forwarded_host(routed_db, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from app.auth import network
    from app.core.config import get_settings

    settings = get_settings().model_copy(update={"trusted_proxy_cidrs": ("192.168.64.0/20",)})
    monkeypatch.setattr(network, "get_settings", lambda: settings)
    client = TestClient(app, base_url="http://testserver", client=("192.168.64.5", 50000))
    response = client.post(
        "/login",
        data={"email": "admin@example.test", "credential": "99887766", "next": "/month"},
        headers={
            "host": "testserver",
            "origin": "https://testserver",
            "x-forwarded-for": "203.0.113.7",
            "x-forwarded-proto": "https",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/month"


def test_admin_input_conflicts_and_invalid_references_are_controlled(routed_db) -> None:  # type: ignore[no-untyped-def]
    factory, (region_id, _track_id, _position_id, person_id) = routed_db
    client = TestClient(app, raise_server_exceptions=False)
    csrf = _login(client, "admin@example.test", "99887766")
    with factory() as db:
        group_id = db.scalar(select(CrewGroup.id).where(CrewGroup.name == "OB Crew"))
        db.add(
            User(
                email="duplicate@example.com",
                display_name="Existing Duplicate",
                credential_hash=hash_credential("123456"),
                credential_kind="pin",
            )
        )
        db.commit()
    assert client.get("/api/month?year=2026&month=13").status_code == 400

    cases = [
        (
            "/manage/catalog/regions",
            {"name": "Northern", "csrf_token": csrf},
            409,
        ),
        (
            "/manage/catalog/tracks",
            {
                "name": "Ellerslie",
                "region_id": str(region_id),

                "csrf_token": csrf,
            },
            409,
        ),
        (
            "/manage/catalog/groups",
            {"name": "OB Crew", "csrf_token": csrf},
            409,
        ),
        (
            "/admin/positions",
            {"name": "CCU", "crew_group_id": str(group_id), "csrf_token": csrf},
            409,
        ),
        (
            "/admin/users",
            {
                "display_name": "Duplicate",
                "email": "duplicate@example.com",
                "credential": "123456",
                "role": "EMPLOYEE",
                "region_id": str(region_id),
                "person_id": "",
                "csrf_token": csrf,
            },
            422,
        ),
        (
            "/admin/users",
            {
                "display_name": "Already Linked",
                "email": "new-linked@example.com",
                "credential": "123456",
                "role": "EMPLOYEE",
                "region_id": str(region_id),
                "person_id": str(person_id),
                "csrf_token": csrf,
            },
            422,
        ),
        (
            "/admin/positions",
            {"name": "Director", "crew_group_id": "not-a-uuid", "csrf_token": csrf},
            400,
        ),
        (
            "/admin/people",
            {
                "display_name": "Invalid Region",
                "email": "",
                "home_region_id": "not-a-uuid",
                "csrf_token": csrf,
            },
            400,
        ),
        (
            "/manage/catalog/tracks",
            {
                "name": "Missing Region Track",
                "region_id": str(uuid.uuid4()),

                "csrf_token": csrf,
            },
            400,
        ),
        (
            "/admin/invitations",
            {
                "email": "invalid-reference@example.com",
                "display_name": "Invalid Reference",
                "role": "EMPLOYEE",
                "region_id": "not-a-uuid",
                "person_id": "",
                "csrf_token": csrf,
            },
            400,
        ),
    ]
    for path, data, expected in cases:
        assert client.post(path, data=data, follow_redirects=False).status_code == expected

    created = client.post(
        "/manage/catalog/regions",
        data={"name": "Central", "csrf_token": csrf},
        follow_redirects=False,
    )
    assert created.status_code == 303


def test_archived_catalog_targets_are_rejected(routed_db) -> None:  # type: ignore[no-untyped-def]
    factory, (region_id, _track_id, _position_id, _person_id) = routed_db
    with factory() as db:
        group = db.scalar(select(CrewGroup).where(CrewGroup.name == "OB Crew"))
        assert group
        group.lifecycle = "ARCHIVED"
        group_id = group.id
        db.commit()
    client = TestClient(app, raise_server_exceptions=False)
    csrf = _login(client, "admin@example.test", "99887766")
    assert client.post(
        "/admin/positions",
        data={"name": "Archived Target", "crew_group_id": str(group_id), "csrf_token": csrf},
        follow_redirects=False,
    ).status_code == 400
    assert client.post(
        "/admin/users",
        data={
            "display_name": "Bad Region",
            "email": "bad-region@example.com",
            "credential": "123456",
            "role": "EMPLOYEE",
            "region_id": str(uuid.uuid4()),
            "person_id": "",
            "csrf_token": csrf,
        },
        follow_redirects=False,
    ).status_code == 422
    invalid_person = client.post(
        "/manage/crew",
        data={
            "display_name": "Bad Region",
            "email": "",
            "region_id": str(uuid.uuid4()),
            "csrf_token": csrf,
        },
        follow_redirects=False,
    )
    assert invalid_person.status_code == 422
    assert "Select an active Region" in invalid_person.text


def test_invitation_secret_uses_fragment_reveal_and_body_activation(routed_db) -> None:  # type: ignore[no-untyped-def]
    factory, (region_id, _track_id, _position_id, _person_id) = routed_db
    admin = TestClient(app)
    csrf = _login(admin, "admin@example.test", "99887766")
    created = admin.post(
        "/admin/invitations",
        data={
            "email": "invitee@example.com",
            "display_name": "Invitee",
            "role": "EMPLOYEE",
            "region_id": str(region_id),
            "person_id": "",
            "csrf_token": csrf,
        },
        follow_redirects=False,
    )
    assert created.status_code == 200
    assert "location" not in created.headers
    origin = re.escape(get_settings().webauthn_origin.rstrip("/"))
    match = re.search(rf'value="{origin}/invite#token=([A-Za-z0-9_-]+)"', created.text)
    assert match
    raw = match.group(1)
    assert raw not in str(created.request.url)
    repeated_create = admin.post(
        "/admin/invitations",
        data={
            "email": "invitee@example.com",
            "display_name": "Invitee",
            "role": "EMPLOYEE",
            "region_id": str(region_id),
            "person_id": "",
            "csrf_token": csrf,
        },
        follow_redirects=False,
    )
    assert repeated_create.status_code == 422
    assert "Invitation not created" in repeated_create.text
    assert 'value="invitee@example.com"' in repeated_create.text
    assert "#token=" not in repeated_create.text
    with factory() as db:
        invitation = db.scalar(select(Invitation).where(Invitation.email == "invitee@example.com"))
        assert invitation and invitation.token_hash == token_hash(raw)
        assert invitation.token_hash != raw
        assert len(list(db.scalars(select(Invitation)))) == 1
        assert all(raw not in str(event.detail) for event in db.scalars(select(AuditEvent)))

    public = TestClient(app)
    page = public.get(f"/invite#token={raw}")
    assert page.status_code == 200
    assert str(page.request.url).endswith("/invite")
    activated = public.post(
        "/invite/activate",
        data={"token": raw, "display_name": "Invitee", "credential": "123456"},
        follow_redirects=False,
    )
    assert activated.status_code == 303
    repeated = public.post(
        "/invite/activate",
        data={"token": raw, "display_name": "Invitee", "credential": "123456"},
    )
    assert repeated.status_code == 400


def test_manager_contractor_invitation_scope_activation_and_limited_surface(routed_db) -> None:  # type: ignore[no-untyped-def]
    factory, (region_id, _track_id, position_id, employee_person_id) = routed_db
    with factory() as db:
        other_region_id = db.scalar(select(Region.id).where(Region.id != region_id))
        contractor = Person(
            display_name="Scoped Contractor",
            email="scoped-contractor@example.com",
            home_region_id=region_id,
        )
        outside = Person(
            display_name="Outside Contractor",
            email="outside-contractor@example.com",
            home_region_id=other_region_id,
        )
        db.add_all([contractor, outside])
        db.commit()
        contractor_id, outside_id = contractor.id, outside.id

    manager = TestClient(app)
    csrf = _login(manager, "manager@example.test", "123456")
    outside_attempt = manager.post(
        f"/manage/accounts/contractors/{outside_id}/invite",
        data={"email": "outside-contractor@example.com", "csrf_token": csrf},
        follow_redirects=False,
    )
    assert outside_attempt.status_code == 403
    created = manager.post(
        f"/manage/accounts/contractors/{contractor_id}/invite",
        data={"email": "scoped-contractor@example.com", "csrf_token": csrf},
        follow_redirects=False,
    )
    assert created.status_code == 200
    origin = re.escape(get_settings().webauthn_origin.rstrip("/"))
    match = re.search(rf'value="{origin}/invite#token=([A-Za-z0-9_-]+)"', created.text)
    assert match
    raw = match.group(1)

    public = TestClient(app)
    activated = public.post(
        "/invite/activate",
        data={"token": raw, "display_name": "Scoped Contractor", "credential": "246810"},
        follow_redirects=False,
    )
    assert activated.status_code == 303
    assert public.post(
        "/invite/activate",
        data={"token": raw, "display_name": "Scoped Contractor", "credential": "246810"},
    ).status_code == 400
    with factory() as db:
        account = db.scalar(select(User).where(User.email == "scoped-contractor@example.com"))
        assert account and account.contractor_access_expires_at is not None
        link = db.get(UserPersonLink, account.id)
        assert link and link.person_id == contractor_id
    crew_management = manager.get(f"/manage/crew?region_id={region_id}")
    assert "Scoped Contractor" in crew_management.text
    assert "Contractor" in crew_management.text and "Access until" in crew_management.text

    own_day = _publish_rows(
        factory,
        region_id=region_id,
        position_id=position_id,
        work_date=local_today() + timedelta(days=3),
        rows=[
            (contractor_id, "Scoped Contractor", "Own detail", True),
            (employee_person_id, "Amy Crew", "Unrelated detail", True),
        ],
    )
    unrelated_day = _publish_rows(
        factory,
        region_id=region_id,
        position_id=position_id,
        work_date=local_today() + timedelta(days=4),
        rows=[(employee_person_id, "Amy Crew", "Unrelated only", True)],
    )
    contractor_client = TestClient(app)
    _login(contractor_client, "scoped-contractor@example.com", "246810")
    month = contractor_client.get("/month")
    assert month.status_code == 200 and f'/day/{own_day}' in month.text
    assert f'/day/{unrelated_day}' not in month.text
    day = contractor_client.get(f"/day/{own_day}")
    assert "Scoped Contractor" in day.text and "Own detail" in day.text
    assert "Amy Crew" not in day.text and "Unrelated detail" not in day.text
    assert contractor_client.get("/crew").status_code == 403
    assert contractor_client.get("/manage/accounts").status_code == 403
    assert contractor_client.get("/manage/crew").status_code == 403
    assert contractor_client.get("/admin").status_code == 403
    settings = contractor_client.get("/settings")
    assert 'href="/hours"' in settings.text
    assert 'href="/crew"' not in settings.text
    assert 'href="/manage/workdays/new"' not in settings.text

    with factory() as db:
        account = db.scalar(select(User).where(User.email == "scoped-contractor@example.com"))
        account.contractor_manual_extension_at = utcnow() - timedelta(days=60)
        account.contractor_access_expires_at = utcnow() - timedelta(seconds=1)
        db.commit()
        before = account.contractor_access_expires_at
    assert contractor_client.get("/month", follow_redirects=False).status_code == 303
    assert contractor_client.post(
        "/login",
        data={
            "email": "scoped-contractor@example.com",
            "credential": "246810",
            "next": "/month",
        },
        follow_redirects=False,
    ).status_code == 400
    extended = manager.post(
        f"/manage/accounts/contractors/{account.id}/extend",
        data={"csrf_token": csrf},
        follow_redirects=False,
    )
    assert extended.status_code == 303
    with factory() as db:
        refreshed = db.get(User, account.id)
        assert refreshed.contractor_access_expires_at.date() >= before.date()
        assert refreshed.status == "ACTIVE"
        assert db.get(Person, contractor_id) is not None


def test_public_signup_is_persisted_admin_only_operational_setting(routed_db) -> None:  # type: ignore[no-untyped-def]
    factory, (region_id, _track_id, _position_id, _person_id) = routed_db
    public = TestClient(app)
    closed_page = public.get("/signup")
    assert "Public account requests are currently closed" in closed_page.text
    assert public.post(
        "/signup",
        data={
            "display_name": "Candidate",
            "email": "candidate@example.com",
            "requested_region_id": str(region_id),
        },
    ).status_code == 404

    manager = TestClient(app)
    manager_csrf = _login(manager, "manager@example.test", "123456")
    assert manager.post(
        "/admin/system-settings",
        data={
            "public_signup_enabled": "true",
            "contractor_inactivity_days": "30",
            "fortnight_anchor": "2026-08-31",
            "csrf_token": manager_csrf,
        },
    ).status_code == 403

    admin = TestClient(app)
    admin_csrf = _login(admin, "admin@example.test", "99887766")
    toggled = admin.post(
        "/admin/system-settings",
        data={
            "public_signup_enabled": "true",
            "contractor_inactivity_days": "45",
            "fortnight_anchor": "2026-08-31",
            "csrf_token": admin_csrf,
        },
        follow_redirects=False,
    )
    assert toggled.status_code == 303
    assert "Public account requests are currently closed" not in public.get("/signup").text
    assert public.post(
        "/signup",
        data={
            "display_name": "Invalid Region",
            "email": "invalid-region@example.com",
            "requested_region_id": "not-a-uuid",
        },
    ).status_code == 400
    submitted = public.post(
        "/signup",
        data={
            "display_name": "Candidate",
            "email": "candidate@example.com",
            "requested_region_id": str(region_id),
        },
    )
    assert submitted.status_code == 200
    with factory() as db:
        settings = db.get(SystemSettings, 1)
        assert settings and settings.public_signup_enabled is True
        assert settings.contractor_inactivity_days == 45
        assert settings.fortnight_anchor == date(2026, 8, 31)
        signup_id = db.scalar(
            select(SignupRequest.id).where(SignupRequest.email == "candidate@example.com")
        )
    approved = manager.post(
        f"/manage/accounts/signup-requests/{signup_id}/approve",
        data={
            "role": "EMPLOYEE",
            "region_id": str(region_id),
            "person_action": "create",
            "person_id": "",
            "csrf_token": manager_csrf,
        },
        follow_redirects=False,
    )
    assert approved.status_code == 200
    assert "location" not in approved.headers
    origin = re.escape(get_settings().webauthn_origin.rstrip("/"))
    assert re.search(rf'value="{origin}/invite#token=[A-Za-z0-9_-]+"', approved.text)


def test_fortnight_anchor_is_admin_only_and_changes_hours_grouping(routed_db) -> None:  # type: ignore[no-untyped-def]
    factory, _ = routed_db
    with factory() as db:
        workday_count = len(list(db.scalars(select(Workday.id))))

    manager = TestClient(app)
    manager_csrf = _login(manager, "manager@example.test", "123456")
    denied = manager.post(
        "/admin/system-settings",
        data={
            "contractor_inactivity_days": "30",
            "fortnight_anchor": "2026-09-07",
            "csrf_token": manager_csrf,
        },
    )
    assert denied.status_code == 403

    admin = TestClient(app)
    admin_csrf = _login(admin, "admin@example.test", "99887766")
    updated = admin.post(
        "/admin/system-settings",
        data={
            "contractor_inactivity_days": "30",
            "fortnight_anchor": "2026-09-07",
            "csrf_token": admin_csrf,
        },
        follow_redirects=False,
    )
    assert updated.status_code == 303
    hours = admin.get("/manage/hours")
    expected_start, expected_end = fortnight_bounds(
        today=local_today(), anchor=date(2026, 9, 7)
    )
    assert f"Fortnight {expected_start.strftime('%d %b')} – {expected_end.strftime('%d %b')}" in hours.text
    previous_start, _ = fortnight_bounds(
        -1, today=local_today(), anchor=date(2026, 9, 7)
    )
    assert previous_start.strftime("%d %b") in admin.get("/manage/hours?offset=-1").text
    with factory() as db:
        settings = db.get(SystemSettings, 1)
        assert settings.fortnight_anchor == date(2026, 9, 7)
        assert len(list(db.scalars(select(Workday.id)))) == workday_count
        audit = db.scalar(
            select(AuditEvent)
            .where(AuditEvent.action == "system_settings.updated")
            .order_by(AuditEvent.occurred_at.desc())
        )
        assert audit.detail["fortnight_anchor"] == "2026-09-07"
        assert audit.detail["previous_fortnight_anchor"] == "2026-08-31"


def test_roster_presets_are_region_scoped_and_builder_receives_ids(routed_db) -> None:  # type: ignore[no-untyped-def]
    factory, (region_id, _track_id, position_id, _person_id) = routed_db
    with factory() as db:
        other_region = db.scalar(select(Region).where(Region.id != region_id))
        other_region_id = other_region.id

    manager = TestClient(app)
    manager_csrf = _login(manager, "manager@example.test", "123456")
    saved = manager.post(
        f"/manage/catalog/regions/{region_id}/presets/THOROUGHBRED",
        data={"position_ids": str(position_id), "csrf_token": manager_csrf},
        follow_redirects=False,
    )
    assert saved.status_code == 303
    assert manager.post(
        f"/manage/catalog/regions/{other_region_id}/presets/HARNESS",
        data={"position_ids": str(position_id), "csrf_token": manager_csrf},
    ).status_code == 403
    page = manager.get("/manage/workdays/new")
    assert page.status_code == 200
    assert "data-position-presets" in page.text
    assert str(position_id) in page.text

    submanager = TestClient(app)
    submanager_csrf = _login(submanager, "submanager@example.test", "445566")
    assert submanager.post(
        f"/manage/catalog/regions/{region_id}/presets/TRIALS",
        data={"position_ids": str(position_id), "csrf_token": submanager_csrf},
    ).status_code == 403

    admin = TestClient(app)
    admin_csrf = _login(admin, "admin@example.test", "99887766")
    assert admin.post(
        f"/manage/catalog/regions/{other_region_id}/presets/HARNESS",
        data={"position_ids": str(position_id), "csrf_token": admin_csrf},
        follow_redirects=False,
    ).status_code == 303
    with factory() as db:
        rows = list(db.scalars(select(PositionPreset)))
        assert {(row.region_id, row.preset_key) for row in rows} == {
            (region_id, "THOROUGHBRED"),
            (other_region_id, "HARNESS"),
        }


def test_settings_fresh_auth_uses_distinct_account_and_address_throttles(routed_db) -> None:  # type: ignore[no-untyped-def]
    factory, _ = routed_db
    employee = TestClient(app)
    csrf = _login(employee, "amy@example.test", "654321")
    for _ in range(5):
        assert employee.post(
            "/settings/reauthenticate",
            data={"credential": "000000", "csrf_token": csrf},
        ).status_code == 400
    assert employee.post(
        "/settings/reauthenticate",
        data={"credential": "654321", "csrf_token": csrf},
    ).status_code == 400
    separate_login = TestClient(app).post(
        "/login",
        data={"email": "manager@example.test", "credential": "123456", "next": "/month"},
        follow_redirects=False,
    )
    assert separate_login.status_code == 303
    with factory() as db:
        rows = list(db.scalars(select(LoginThrottle)))
        assert len(rows) == 2 and all(row.blocked_until for row in rows)
        for row in rows:
            row.blocked_until = utcnow() - timedelta(seconds=1)
        db.commit()
    success = employee.post(
        "/settings/reauthenticate",
        data={"credential": "654321", "csrf_token": csrf},
        follow_redirects=False,
    )
    assert success.status_code == 303
    with factory() as db:
        assert list(db.scalars(select(LoginThrottle))) == []


@pytest.fixture
def routed_db(monkeypatch):  # type: ignore[no-untyped-def]
    engine = create_engine(
        "sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )

    @event.listens_for(engine, "connect")
    def foreign_keys(connection, _record):  # type: ignore[no-untyped-def]
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    import app.auth.middleware as middleware_module
    import app.core.database as database_module

    monkeypatch.setattr(middleware_module, "SessionLocal", factory)
    monkeypatch.setattr(database_module, "SessionLocal", factory)
    with factory() as db:
        region = Region(name="Northern")
        other_region = Region(name="Southern")
        group = CrewGroup(name="OB Crew")
        db.add_all([region, other_region, group])
        db.flush()
        track = Track(name="Ellerslie", region_id=region.id, palette_slot=1)
        position = BasePosition(name="CCU", crew_group_id=group.id)
        person = Person(display_name="Amy Crew", email="amy@example.test", home_region_id=region.id)
        manager = User(
            email="manager@example.test",
            display_name="Manager",
            credential_hash=hash_credential("123456"),
            credential_kind="pin",
        )
        employee = User(
            email="amy@example.test",
            display_name="Amy",
            credential_hash=hash_credential("654321"),
            credential_kind="pin",
        )
        viewer = User(
            email="viewer@example.test",
            display_name="Viewer",
            credential_hash=hash_credential("112233"),
            credential_kind="pin",
        )
        admin = User(
            email="admin@example.test",
            display_name="Admin",
            credential_hash=hash_credential("99887766"),
            credential_kind="pin",
        )
        submanager = User(
            email="submanager@example.test",
            display_name="Submanager",
            credential_hash=hash_credential("445566"),
            credential_kind="pin",
        )
        outside_user = User(
            email="private-south@example.test",
            display_name="Private South Account",
            credential_hash=hash_credential("778899"),
            credential_kind="pin",
        )
        outside_person = Person(
            display_name="South Crew",
            email="private-south@example.test",
            home_region_id=other_region.id,
        )
        db.add_all(
            [
                track,
                position,
                person,
                manager,
                employee,
                viewer,
                admin,
                submanager,
                outside_user,
                outside_person,
            ]
        )
        db.flush()
        db.add_all(
            [
                UserPersonLink(user_id=employee.id, person_id=person.id),
                RoleGrant(user_id=manager.id, role=Role.MANAGER.value, region_id=region.id),
                RoleGrant(user_id=employee.id, role=Role.EMPLOYEE.value, region_id=region.id),
                RoleGrant(user_id=viewer.id, role=Role.VIEWER.value, region_id=region.id),
                RoleGrant(user_id=admin.id, role=Role.ADMIN.value, region_id=None),
                RoleGrant(user_id=submanager.id, role=Role.SUB_MANAGER.value, region_id=region.id),
                UserPersonLink(user_id=outside_user.id, person_id=outside_person.id),
            ]
        )
        db.commit()
        ids = region.id, track.id, position.id, person.id
    yield factory, ids
    engine.dispose()


def _login(client: TestClient, email: str, pin: str) -> str:
    response = client.post(
        "/login", data={"email": email, "credential": pin, "next": "/month"}, follow_redirects=False
    )
    assert response.status_code == 303
    csrf = client.cookies.get("ontrack_csrf")
    assert csrf
    return csrf


def _png_bytes(width: int, height: int, *, red: int = 18) -> bytes:
    def chunk(name: bytes, content: bytes) -> bytes:
        checksum = zlib.crc32(name + content) & 0xFFFFFFFF
        return struct.pack(">I", len(content)) + name + content + struct.pack(">I", checksum)

    header = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    scanline = b"\x00" + bytes((red, 59, 53, 255)) * width
    pixels = zlib.compress(scanline * height)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", pixels) + chunk(b"IEND", b"")


def _jpeg_bytes(width: int, height: int) -> bytes:
    frame = (
        b"\x08"
        + height.to_bytes(2, "big")
        + width.to_bytes(2, "big")
        + b"\x03\x01\x11\x00\x02\x11\x00\x03\x11\x00"
    )
    return b"\xff\xd8\xff\xc0" + (len(frame) + 2).to_bytes(2, "big") + frame + b"\xff\xd9"


def test_published_day_falls_back_when_map_metadata_points_to_missing_file(
    routed_db, monkeypatch, tmp_path
) -> None:  # type: ignore[no-untyped-def]
    factory, (region_id, track_id, position_id, person_id) = routed_db
    monkeypatch.setattr("app.track_maps.service._directory", lambda: tmp_path.resolve())
    with factory() as db:
        manager = db.scalar(select(User).where(User.email == "manager@example.test"))
        person = db.get(Person, person_id)
        workday = Workday(region_id=region_id, created_by_user_id=manager.id)
        db.add(workday)
        db.flush()
        revision = WorkdayRevision(
            workday_id=workday.id,
            revision_number=1,
            state="PUBLISHED",
            work_date=date(2026, 11, 8),
            track_id=track_id,
            track_name_snapshot="Ellerslie",
            title="Missing map day",
            created_by_user_id=manager.id,
            published_by_user_id=manager.id,
            published_at=utcnow(),
        )
        db.add(revision)
        db.flush()
        db.add_all(
            [
                Assignment(
                    revision_id=revision.id,
                    base_position_id=position_id,
                    display_name_snapshot="CCU",
                    person_id=person.id,
                    person_name_snapshot=person.display_name,
                    status="ASSIGNED",
                ),
                TrackMap(
                    track_id=track_id,
                    automatic_file_name="missing.png",
                    automatic_content_type="image/png",
                    automatic_width=640,
                    automatic_height=480,
                    automatic_bytes=100,
                    automatic_status="AVAILABLE",
                ),
            ]
        )
        workday.current_published_revision_id = revision.id
        db.commit()
        workday_id = workday.id
    employee = TestClient(app)
    _login(employee, "amy@example.test", "654321")
    page = employee.get(f"/day/{workday_id}")
    assert page.status_code == 200
    assert "Map unavailable." in page.text
    assert f'/track-maps/{track_id}' not in page.text
    manager = TestClient(app)
    _login(manager, "manager@example.test", "123456")
    catalog = manager.get("/manage/catalog")
    assert "Automatic map file unavailable." in catalog.text
    assert f'src="/track-maps/{track_id}"' not in catalog.text
    with factory() as db:
        row = db.get(TrackMap, track_id)
        row.has_manual_override = True
        row.manual_file_name = "missing-manual.png"
        row.manual_content_type = "image/png"
        row.manual_width = 640
        row.manual_height = 480
        row.manual_bytes = 100
        db.commit()
    catalog = manager.get("/manage/catalog")
    assert "Map file unavailable. Upload a replacement" in catalog.text
    assert f'src="/track-maps/{track_id}"' not in catalog.text


@pytest.mark.parametrize("track_name", ["Ellerslie", "Cambridge Synthetic", "Te Aroha"])
def test_official_map_refresh_persists_serves_and_renders_on_published_day(
    routed_db, monkeypatch, tmp_path, track_name: str
) -> None:  # type: ignore[no-untyped-def]
    factory, (region_id, track_id, position_id, person_id) = routed_db
    monkeypatch.setattr("app.track_maps.service._directory", lambda: tmp_path.resolve())
    content = (
        _png_bytes(720, 540) if track_name == "Ellerslie" else _jpeg_bytes(720, 540)
    )

    class OfficialFallbackClient:
        def get(self, url: str, *, accept: str) -> HTTPResponse:
            if accept == "text/html":
                raise SourceHTTPError("Course HTML challenge.")
            content_type = "image/png" if track_name == "Ellerslie" else "image/jpeg"
            return HTTPResponse(url=url, status=200, content_type=content_type, body=content)

    with factory() as db:
        track = db.get(Track, track_id)
        track.name = track_name
        manager = db.scalar(select(User).where(User.email == "manager@example.test"))
        person = db.get(Person, person_id)
        refreshed = refresh_automatic_map(db, track, OfficialFallbackClient())  # type: ignore[arg-type]
        assert refreshed.automatic_status == "AVAILABLE"
        workday = Workday(region_id=region_id, created_by_user_id=manager.id)
        db.add(workday)
        db.flush()
        revision = WorkdayRevision(
            workday_id=workday.id,
            revision_number=1,
            state="PUBLISHED",
            work_date=date(2026, 11, 9),
            track_id=track_id,
            track_name_snapshot=track_name,
            title=f"{track_name} race day",
            created_by_user_id=manager.id,
            published_by_user_id=manager.id,
            published_at=utcnow(),
        )
        db.add(revision)
        db.flush()
        db.add(
            Assignment(
                revision_id=revision.id,
                base_position_id=position_id,
                display_name_snapshot="CCU",
                person_id=person.id,
                person_name_snapshot=person.display_name,
                status="ASSIGNED",
            )
        )
        workday.current_published_revision_id = revision.id
        db.commit()
        workday_id = workday.id

    employee = TestClient(app)
    _login(employee, "amy@example.test", "654321")
    image = employee.get(f"/track-maps/{track_id}")
    assert image.status_code == 200 and image.content == content
    day = employee.get(f"/day/{workday_id}")
    assert day.status_code == 200
    assert f'src="/track-maps/{track_id}"' in day.text
    assert "pinch to zoom and drag to pan" in day.text


def test_admin_can_create_regionless_person_but_manager_cannot(routed_db) -> None:  # type: ignore[no-untyped-def]
    factory, _ids = routed_db
    admin = TestClient(app)
    admin_csrf = _login(admin, "admin@example.test", "99887766")
    created = admin.post(
        "/manage/crew",
        data={
            "display_name": "Region Pending",
            "email": "pending@example.com",
            "region_id": "",
            "csrf_token": admin_csrf,
        },
        follow_redirects=False,
    )
    assert created.status_code == 303, created.text
    assert created.headers["location"].startswith("/manage/crew/")
    assert created.headers["location"].endswith("?created=1#profile")
    with factory() as db:
        person = db.scalar(select(Person).where(Person.email == "pending@example.com"))
        assert person is not None and person.home_region_id is None
        person_id = person.id
    queue = admin.get("/manage/crew")
    assert f'/manage/crew/{person_id}' in queue.text
    assert "Needs Region" in queue.text

    manager = TestClient(app)
    manager_csrf = _login(manager, "manager@example.test", "123456")
    denied = manager.post(
        "/manage/crew",
        data={
            "display_name": "Manager Regionless",
            "email": "manager-regionless@example.com",
            "region_id": "",
            "csrf_token": manager_csrf,
        },
    )
    assert denied.status_code == 422
    assert "Person not added" in denied.text
    with factory() as db:
        assert db.scalar(
            select(Person).where(Person.email == "manager-regionless@example.com")
        ) is None


def test_admin_credential_reset_route_and_public_completion_are_bounded(routed_db) -> None:  # type: ignore[no-untyped-def]
    factory, _ = routed_db
    anonymous = TestClient(app)
    page = anonymous.get("/credential-reset")
    assert page.status_code == 200
    assert page.headers["cache-control"] == "no-store"
    assert 'name="email"' not in page.text
    assert 'name="token" value=""' in page.text
    assert "history.replaceState" in Path("app/static/credential-reset.js").read_text()

    with factory() as db:
        viewer_id = db.scalar(select(User.id).where(User.email == "viewer@example.test"))
        admin_id = db.scalar(select(User.id).where(User.email == "admin@example.test"))

    for email, credential in (
        ("manager@example.test", "123456"),
        ("submanager@example.test", "445566"),
        ("viewer@example.test", "112233"),
        ("amy@example.test", "654321"),
    ):
        unauthorized = TestClient(app)
        csrf = _login(unauthorized, email, credential)
        denied = unauthorized.post(
            f"/admin/users/{viewer_id}/credential-reset",
            data={"csrf_token": csrf},
        )
        assert denied.status_code == 403

    admin = TestClient(app)
    admin_csrf = _login(admin, "admin@example.test", "99887766")
    assert admin.post(
        f"/admin/users/{viewer_id}/credential-reset",
        data={"csrf_token": "invalid"},
    ).status_code == 403
    assert admin.post(
        f"/admin/users/{admin_id}/credential-reset",
        data={"csrf_token": admin_csrf},
    ).status_code == 400

    issued = admin.post(
        f"/admin/users/{viewer_id}/credential-reset",
        data={"csrf_token": admin_csrf},
    )
    assert issued.status_code == 200
    assert issued.headers["cache-control"] == "no-store"
    origin = re.escape(get_settings().webauthn_origin.rstrip("/"))
    match = re.search(
        rf'value="({origin}/credential-reset#token=([^"&]+))"', issued.text
    )
    assert match
    raw = match.group(2)
    assert issued.text.count(raw) == 1
    assert "expires in 60 minutes" in issued.text
    assert "Deliver this link privately" in issued.text
    with factory() as db:
        reset = db.scalar(select(CredentialReset).where(CredentialReset.user_id == viewer_id))
        event = db.scalar(
            select(AuditEvent).where(AuditEvent.action == "user.credential_reset.created")
        )
        assert reset.token_hash == token_hash(raw) and raw not in reset.token_hash
        assert event.actor_user_id == admin_id and raw not in str(event.detail)

    mismatch = anonymous.post(
        "/credential-reset/complete",
        data={"token": raw, "credential": "667788", "confirmation": "667799"},
    )
    assert mismatch.status_code == 400 and "did not match" in mismatch.text
    completed = anonymous.post(
        "/credential-reset/complete",
        data={"token": raw, "credential": "667788", "confirmation": "667788"},
        follow_redirects=False,
    )
    assert completed.status_code == 303
    assert completed.headers["location"] == "/login?credential=reset"
    reused = anonymous.post(
        "/credential-reset/complete",
        data={"token": raw, "credential": "778899", "confirmation": "778899"},
    )
    assert reused.status_code == 400
    assert "invalid, expired, or already used" in reused.text
    assert anonymous.post(
        "/login",
        data={"email": "viewer@example.test", "credential": "112233", "next": "/month"},
    ).status_code == 400
    assert anonymous.post(
        "/login",
        data={"email": "viewer@example.test", "credential": "667788", "next": "/month"},
        follow_redirects=False,
    ).status_code == 303


def test_admin_credential_reset_requires_fresh_auth_and_active_target(routed_db) -> None:  # type: ignore[no-untyped-def]
    factory, _ = routed_db
    admin = TestClient(app)
    csrf = _login(admin, "admin@example.test", "99887766")
    raw_session = admin.cookies.get("ontrack_session")
    with factory() as db:
        target = db.scalar(select(User).where(User.email == "viewer@example.test"))
        target.status = "DISABLED"
        device = db.scalar(
            select(TrustedDevice).where(TrustedDevice.token_hash == token_hash(raw_session))
        )
        device.primary_authenticated_at = utcnow() - timedelta(hours=1)
        db.commit()
        target_id = target.id
    stale = admin.post(
        f"/admin/users/{target_id}/credential-reset",
        data={"csrf_token": csrf},
        follow_redirects=False,
    )
    assert stale.status_code == 403 and "Recent authentication is required" in stale.text

    fresh_admin = TestClient(app)
    csrf = _login(fresh_admin, "admin@example.test", "99887766")
    disabled = fresh_admin.post(
        f"/admin/users/{target_id}/credential-reset",
        data={"csrf_token": csrf},
    )
    assert disabled.status_code == 409


def test_recovery_code_completes_mfa_login_once_without_audit_disclosure(
    routed_db, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    factory, _ = routed_db
    from app.auth import factors as factor_module

    settings = factor_module.get_settings().model_copy(update={"mfa_required_admin": True})
    monkeypatch.setattr(factor_module, "get_settings", lambda: settings)
    with factory() as db:
        admin = db.scalar(select(User).where(User.email == "admin@example.test"))
        db.add(
            TotpFactor(
                user_id=admin.id,
                encrypted_secret=b"recovery-login-does-not-read-this",
                confirmed_at=utcnow(),
            )
        )
        code = generate_recovery_codes(db, admin.id)[0]
        db.commit()

    client = TestClient(app)
    primary = client.post(
        "/login",
        data={"email": "admin@example.test", "credential": "99887766", "next": "/admin"},
        follow_redirects=False,
    )
    assert primary.status_code == 303
    challenge_id = parse_qs(urlsplit(primary.headers["location"]).query)["challenge_id"][0]
    recovered = client.post(
        "/login/totp",
        data={"challenge_id": challenge_id, "recovery_code": code, "next": "/admin"},
        follow_redirects=False,
    )
    assert recovered.status_code == 303
    assert recovered.headers["location"] == "/admin"
    with factory() as db:
        row = db.scalar(select(RecoveryCode).where(RecoveryCode.user_id == admin.id))
        event = db.scalar(
            select(AuditEvent).where(AuditEvent.action == "mfa.recovery_code_used")
        )
        assert row.used_at is not None
        assert event is not None and code not in str(event.detail)

    second_primary = client.post(
        "/login",
        data={"email": "admin@example.test", "credential": "99887766", "next": "/admin"},
        follow_redirects=False,
    )
    second_challenge = parse_qs(urlsplit(second_primary.headers["location"]).query)[
        "challenge_id"
    ][0]
    reused = client.post(
        "/login/totp",
        data={"challenge_id": second_challenge, "recovery_code": code, "next": "/admin"},
    )
    assert reused.status_code == 400
    assert "Authentication code was not accepted." in reused.text
    assert code not in reused.text


def test_leave_management_picker_preview_privacy_and_authorization(routed_db) -> None:  # type: ignore[no-untyped-def]
    factory, (region_id, _track_id, position_id, person_id) = routed_db
    workday_id = _publish_rows(
        factory,
        region_id=region_id,
        position_id=position_id,
        work_date=date(2026, 10, 14),
        rows=[
            (person_id, "Amy Crew", "CCU", False),
            (person_id, "Amy Crew", "Director", False),
        ],
    )
    manager = TestClient(app)
    csrf = _login(manager, "manager@example.test", "123456")
    response = manager.post(
        "/manage/leave",
        data={
            "person_id": str(person_id),
            "start_date": "2026-10-12",
            "end_date": "2026-10-16",
            "note": "Private manager context",
            "csrf_token": csrf,
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    page = manager.get(f"/manage/leave?region_id={region_id}")
    assert page.status_code == 200
    assert page.text.count(f'/manage/workdays/{workday_id}') == 1
    assert "Private manager context" in page.text
    with factory() as db:
        workday = db.get(Workday, workday_id)
        for inactive_status in ("CANCELLED", "ABANDONED"):
            workday.status = inactive_status
            db.commit()
            inactive_page = manager.get(f"/manage/leave?region_id={region_id}")
            assert f'/manage/workdays/{workday_id}' not in inactive_page.text
        workday.status = "SCHEDULED"
        db.commit()

    builder = manager.get(f"/manage/workdays/{workday_id}")
    assert "On leave · 12–16 Oct" in builder.text

    existing = manager.get(
        f"/manage/workdays/{workday_id}/crew-picker",
        params={"position_id": str(position_id)},
    )
    person_payload = next(
        person
        for group in existing.json()["groups"]
        for person in group["people"]
        if person["id"] == str(person_id)
    )
    assert person_payload["on_leave"] is True
    assert person_payload["leave_start"] == "2026-10-12"
    assert "Private manager context" not in str(person_payload)
    new_picker = manager.get(
        "/manage/new-workday/crew-picker",
        params={
            "region_id": str(region_id),
            "work_date": "2026-10-14",
            "position_id": str(position_id),
        },
    )
    assert any(
        person["on_leave"]
        for group in new_picker.json()["groups"]
        for person in group["people"]
        if person["id"] == str(person_id)
    )
    preview = manager.get(f"/manage/workdays/{workday_id}/preview")
    assert "Amy Crew is on leave on this Workday." in preview.text
    submanager_builder = TestClient(app)
    _login(submanager_builder, "submanager@example.test", "445566")
    submanager_page = submanager_builder.get(f"/manage/workdays/{workday_id}")
    assert "On leave · 12–16 Oct" in submanager_page.text
    assert "Private manager context" not in submanager_page.text
    employee_day = TestClient(app)
    _login(employee_day, "amy@example.test", "654321")
    personal_payload = employee_day.get(f"/api/day/{workday_id}").json()
    assert "Private manager context" not in str(personal_payload)
    assert "leave" not in str(personal_payload).casefold()

    overlap = manager.post(
        "/manage/leave",
        data={
            "person_id": str(person_id),
            "start_date": "2026-10-16",
            "end_date": "2026-10-18",
            "csrf_token": csrf,
        },
        follow_redirects=True,
    )
    assert "already has active leave overlapping" in overlap.text

    with factory() as db:
        outside_person = db.scalar(select(Person).where(Person.display_name == "South Crew"))
    denied = manager.post(
        "/manage/leave",
        data={
            "person_id": str(outside_person.id),
            "start_date": "2026-10-12",
            "end_date": "2026-10-16",
            "csrf_token": csrf,
        },
    )
    assert denied.status_code == 403
    admin = TestClient(app)
    admin_csrf = _login(admin, "admin@example.test", "99887766")
    assert admin.post(
        "/manage/leave",
        data={
            "person_id": str(outside_person.id),
            "start_date": "2026-11-01",
            "end_date": "2026-11-02",
            "csrf_token": admin_csrf,
        },
        follow_redirects=False,
    ).status_code == 303
    with factory() as db:
        contractor = User(
            email="leave-contractor@example.test",
            display_name="Leave Contractor",
            credential_hash=hash_credential("667788"),
            credential_kind="pin",
        )
        db.add(contractor)
        db.flush()
        db.add(
            RoleGrant(
                user_id=contractor.id,
                role=Role.CONTRACTOR.value,
                region_id=region_id,
            )
        )
        db.commit()
    for email, pin in (
        ("submanager@example.test", "445566"),
        ("viewer@example.test", "112233"),
        ("amy@example.test", "654321"),
        ("leave-contractor@example.test", "667788"),
    ):
        client = TestClient(app)
        unauthorized_csrf = _login(client, email, pin)
        assert client.post(
            "/manage/leave",
            data={
                "person_id": str(person_id),
                "start_date": "2026-11-01",
                "end_date": "2026-11-02",
                "csrf_token": unauthorized_csrf,
            },
        ).status_code == 403

    with factory() as db:
        leave = db.scalar(select(PersonUnavailability))
        assert leave and leave.cancelled_at is None
        audit = db.scalar(
            select(AuditEvent).where(
                AuditEvent.action == "person_unavailability.created"
            )
        )
        assert audit and "Private manager context" not in str(audit.detail)
        leave_id = leave.id
    cancelled = manager.post(
        f"/manage/leave/{leave_id}/cancel",
        data={"csrf_token": csrf},
        follow_redirects=False,
    )
    assert cancelled.status_code == 303
    with factory() as db:
        assert db.get(PersonUnavailability, leave_id).cancelled_at is not None
    cancelled_picker = manager.get(
        f"/manage/workdays/{workday_id}/crew-picker",
        params={"position_id": str(position_id)},
    )
    assert not any(
        person["on_leave"]
        for group in cancelled_picker.json()["groups"]
        for person in group["people"]
        if person["id"] == str(person_id)
    )


def test_employee_personal_timing_and_making_own_way_are_stable_and_notify_manager(
    routed_db,
) -> None:  # type: ignore[no-untyped-def]
    factory, (region_id, _track_id, position_id, person_id) = routed_db
    with factory() as db:
        manager = db.scalar(select(User).where(User.email == "manager@example.test"))
        person = db.get(Person, person_id)
        workday = Workday(
            region_id=region_id,
            category="RACE_DAY",
            created_by_user_id=manager.id,
        )
        db.add(workday)
        db.flush()
        revision = WorkdayRevision(
            workday_id=workday.id,
            revision_number=1,
            state="PUBLISHED",
            work_date=date(2026, 11, 2),
            track_name_snapshot="Ellerslie",
            title="Race Day",
            start_time=time(8),
            end_time=time(18),
            standard_travel_enabled=True,
            created_by_user_id=manager.id,
            published_by_user_id=manager.id,
            published_at=utcnow(),
        )
        db.add(revision)
        db.flush()
        db.add(
            Assignment(
                revision_id=revision.id,
                base_position_id=position_id,
                display_name_snapshot="CCU",
                person_id=person.id,
                person_name_snapshot=person.display_name,
                status="ASSIGNED",
            )
        )
        workday.current_published_revision_id = revision.id
        db.commit()
        workday_id = workday.id
    client = TestClient(app)
    csrf = _login(client, "amy@example.test", "654321")
    response = client.post(
        f"/day/{workday_id}/personal",
        data={
            "csrf_token": csrf,
            "note": "Private employee note",
            "personal_start_time": "08:30",
            "personal_finish_time": "17:45",
            "last_race_time_changed": "1",
            "finished_back_at_office": "1",
            "standard_travel_opt_out": "1",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    day = client.get(f"/day/{workday_id}")
    assert "08:30" in day.text and "17:45" in day.text
    api_day = client.get(f"/api/day/{workday_id}")
    assert api_day.status_code == 200
    payload = api_day.json()["workday"]
    assert payload["start"] == "08:30:00" and payload["end"] == "17:45:00"
    assert "last_trial" in payload
    with factory() as db:
        entry = db.scalar(
            select(PersonalWorkdayEntry).where(
                PersonalWorkdayEntry.workday_id == workday_id,
                PersonalWorkdayEntry.person_id == person_id,
            )
        )
        assert entry.note == "Private employee note"
        assert (entry.start_time, entry.end_time) == (time(8, 30), time(17, 45))
        assert entry.standard_travel_opt_out is True
        event = db.scalar(
            select(NotificationEvent).where(NotificationEvent.workday_id == workday_id)
        )
        assert event.event_type == "MANAGER_ACTION_REQUIRED"
        assert event.payload["person"] == "Amy Crew"
        assert event.payload["making_own_way"] is True
        assert event.payload["date"] == "2026-11-02"
        assert event.payload["url"] == f"/day/{workday_id}"
        assert "making their own way" in event.payload["message"]
        assignment = db.scalar(
            select(Assignment).where(
                Assignment.revision_id == workday.current_published_revision_id,
                Assignment.person_id == person_id,
            )
        )
        assignment.uses_standard_travel = False
        db.commit()
    day = client.get(f"/day/{workday_id}")
    assert 'name="standard_travel_opt_out"' not in day.text
    response = client.post(
        f"/day/{workday_id}/personal",
        data={
            "csrf_token": csrf,
            "note": "Private employee note",
            "standard_travel_opt_out": "1",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    with factory() as db:
        entry = db.scalar(
            select(PersonalWorkdayEntry).where(
                PersonalWorkdayEntry.workday_id == workday_id,
                PersonalWorkdayEntry.person_id == person_id,
            )
        )
        assert entry.standard_travel_opt_out is False
        workday = db.get(Workday, workday_id)
        workday.current_published_revision_id = None
        db.commit()
        assert db.get(PersonalWorkdayEntry, entry.id).note == "Private employee note"


def test_admin_data_export_is_authorized_and_secret_free(routed_db) -> None:  # type: ignore[no-untyped-def]
    admin = TestClient(app)
    _login(admin, "admin@example.test", "99887766")
    response = admin.get("/admin/download-data")
    assert response.status_code == 200
    assert response.headers["content-disposition"].startswith(
        'attachment; filename="ontrack-data-'
    )
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        names = set(archive.namelist())
        contents = b"".join(archive.read(name) for name in names)
    assert {"accounts.csv", "workday_revisions.csv", "audit_events.csv"} <= names
    assert b"credential_hash" not in contents

    employee = TestClient(app)
    _login(employee, "amy@example.test", "654321")
    assert employee.get("/admin/download-data").status_code == 403


def _publish_rows(
    factory,
    *,
    region_id: uuid.UUID,
    position_id: uuid.UUID,
    work_date: date,
    rows: list[tuple[uuid.UUID | None, str, str, bool]],
    day_note: str = "",
) -> uuid.UUID:
    with factory() as db:
        manager_id = db.scalar(select(User.id).where(User.email == "manager@example.test"))
        workday = Workday(region_id=region_id, created_by_user_id=manager_id)
        db.add(workday)
        db.flush()
        revision = WorkdayRevision(
            workday_id=workday.id,
            revision_number=1,
            state="PUBLISHED",
            work_date=work_date,
            title="Published privacy day",
            track_name_snapshot="Ellerslie",

            start_time=time(8),
            end_time=time(17),
            day_note=day_note,
            published_at=utcnow(),
            created_by_user_id=manager_id,
        )
        db.add(revision)
        db.flush()
        for index, (person_id, person_name, note, note_private) in enumerate(rows, start=1):
            db.add(
                Assignment(
                    revision_id=revision.id,
                    base_position_id=position_id,
                    slot_index=index,
                    display_name_snapshot=f"CCU {index}",
                    person_id=person_id,
                    person_name_snapshot=person_name,
                    status="ASSIGNED" if person_id else "OPEN",
                    note=note,
                    note_private=note_private,
                )
            )
        workday.current_published_revision_id = revision.id
        db.commit()
        return workday.id


def test_published_workday_reschedule_and_person_response_workflow(routed_db) -> None:  # type: ignore[no-untyped-def]
    factory, (region_id, _track_id, position_id, person_id) = routed_db
    original_date = date(2026, 10, 20)
    replacement_date = date(2026, 10, 22)
    with factory() as db:
        contractor_person = Person(
            display_name="Route Contractor",
            email="route-contractor@example.test",
            home_region_id=region_id,
        )
        contractor = User(
            email="route-contractor@example.test",
            display_name="Route Contractor",
            credential_hash=hash_credential("246810"),
            credential_kind="pin",
        )
        db.add_all([contractor_person, contractor])
        db.flush()
        db.add_all(
            [
                UserPersonLink(user_id=contractor.id, person_id=contractor_person.id),
                RoleGrant(
                    user_id=contractor.id,
                    role=Role.CONTRACTOR.value,
                    region_id=region_id,
                ),
            ]
        )
        db.commit()
        contractor_person_id = contractor_person.id
    workday_id = _publish_rows(
        factory,
        region_id=region_id,
        position_id=position_id,
        work_date=original_date,
        rows=[
            (person_id, "Amy Crew", "First role", False),
            (person_id, "Amy Crew", "Second role", False),
            (contractor_person_id, "Route Contractor", "Contract role", False),
        ],
    )
    with factory() as db:
        source = db.get(Workday, workday_id)
        source_revision_id = source.current_published_revision_id
        private_draft = WorkdayRevision(
            workday_id=source.id,
            revision_number=2,
            state="DRAFT",
            based_on_revision_id=source_revision_id,
            work_date=original_date,
            created_by_user_id=source.created_by_user_id,
        )
        db.add(private_draft)
        db.flush()
        source.current_draft_revision_id = private_draft.id
        db.commit()
        private_draft_id = private_draft.id

    manager = TestClient(app)
    manager_csrf = _login(manager, "manager@example.test", "123456")
    manager_page = manager.get(f"/day/{workday_id}")
    assert "Move / reschedule this published Workday" in manager_page.text
    missing_confirmation = manager.post(
        f"/manage/workdays/{workday_id}/reschedule",
        data={"new_date": replacement_date.isoformat(), "csrf_token": manager_csrf},
    )
    assert missing_confirmation.status_code == 400
    moved = manager.post(
        f"/manage/workdays/{workday_id}/reschedule",
        data={
            "new_date": replacement_date.isoformat(),
            "confirm_move": "true",
            "csrf_token": manager_csrf,
        },
        follow_redirects=False,
    )
    assert moved.status_code == 303
    replacement_id = uuid.UUID(moved.headers["location"].split("/")[2].split("?")[0])

    with factory() as db:
        source = db.get(Workday, workday_id)
        source_revision = db.get(WorkdayRevision, source.current_published_revision_id)
        replacement = db.get(Workday, replacement_id)
        replacement_revision = db.get(
            WorkdayRevision, replacement.current_published_revision_id
        )
        copied = list(
            db.scalars(
                select(Assignment).where(Assignment.revision_id == replacement_revision.id)
            )
        )
        source_slots = set(
            db.scalars(
                select(Assignment.slot_key).where(Assignment.revision_id == source_revision.id)
            )
        )
        event = db.get(
            NotificationEvent,
            f"roster-published:{replacement.id}:{replacement_revision.id}",
        )
        assert source.status == "ABANDONED"
        assert source.current_draft_revision_id is None
        assert db.get(WorkdayRevision, private_draft_id) is not None
        assert source_revision.work_date == original_date
        assert replacement.rescheduled_from_workday_id == source.id
        assert replacement.status == "SCHEDULED"
        assert replacement_revision.state == "PUBLISHED"
        assert replacement_revision.work_date == replacement_date
        assert replacement_revision.based_on_revision_id is None
        assert len(copied) == 3 and {row.slot_key for row in copied} == source_slots
        assert event is not None
        assert event.payload["url"] == f"/day/{replacement.id}"
        assert "moved from 20 Oct 2026 to 22 Oct 2026" in event.payload["summary"]

    employee = TestClient(app)
    employee_csrf = _login(employee, "amy@example.test", "654321")
    replacement_page = employee.get(f"/day/{replacement_id}")
    assert "Moved from" in replacement_page.text
    assert "Can you work the replacement date?" in replacement_page.text
    assert replacement_page.text.count("Please confirm whether you are available") == 1
    accepted = employee.post(
        f"/day/{replacement_id}/replacement-response",
        data={"response": "ACCEPTED", "csrf_token": employee_csrf},
        follow_redirects=False,
    )
    assert accepted.status_code == 303
    assert "currently marked <strong>Available</strong>" in employee.get(
        f"/day/{replacement_id}"
    ).text
    declined = employee.post(
        f"/day/{replacement_id}/replacement-response",
        data={"response": "DECLINED", "csrf_token": employee_csrf},
        follow_redirects=False,
    )
    assert declined.status_code == 303
    unrelated = TestClient(app)
    unrelated_csrf = _login(unrelated, "private-south@example.test", "778899")
    denied = unrelated.post(
        f"/day/{replacement_id}/replacement-response",
        data={"response": "ACCEPTED", "csrf_token": unrelated_csrf},
    )
    assert denied.status_code == 404
    with factory() as db:
        entry = db.scalar(
            select(PersonalWorkdayEntry).where(
                PersonalWorkdayEntry.workday_id == replacement_id,
                PersonalWorkdayEntry.person_id == person_id,
            )
        )
        replacement = db.get(Workday, replacement_id)
        rows = list(
            db.scalars(
                select(Assignment).where(
                    Assignment.revision_id == replacement.current_published_revision_id
                )
            )
        )
        manager_notice = db.scalar(
            select(NotificationEvent).where(
                NotificationEvent.event_type == "MANAGER_ACTION_REQUIRED",
                NotificationEvent.workday_id == replacement_id,
            )
        )
        assert entry.replacement_response == "DECLINED"
        assert entry.replacement_responded_at is not None
        assert len(rows) == 3
        assert sum(row.person_id == person_id for row in rows) == 2
        assert sum(row.person_id == contractor_person_id for row in rows) == 1
        assert manager_notice.payload["person"] == "Amy Crew"
        assert "private" not in str(manager_notice.payload).lower()
    manager_replacement = manager.get(f"/day/{replacement_id}")
    assert manager_replacement.text.count("Replacement: Declined") == 2
    assert manager_replacement.text.count("Replacement: Pending") == 1

    contractor_client = TestClient(app)
    contractor_csrf = _login(contractor_client, "route-contractor@example.test", "246810")
    contractor_page = contractor_client.get(f"/day/{replacement_id}")
    assert "Can you work the replacement date?" in contractor_page.text
    contractor_response = contractor_client.post(
        f"/day/{replacement_id}/replacement-response",
        data={"response": "ACCEPTED", "csrf_token": contractor_csrf},
        follow_redirects=False,
    )
    assert contractor_response.status_code == 303

    duplicate = manager.post(
        f"/manage/workdays/{workday_id}/reschedule",
        data={
            "new_date": date(2026, 10, 23).isoformat(),
            "confirm_move": "true",
            "csrf_token": manager_csrf,
        },
    )
    assert duplicate.status_code == 409
    reinstate = manager.post(
        f"/manage/workdays/{workday_id}/status",
        data={"status": "SCHEDULED", "csrf_token": manager_csrf},
    )
    assert reinstate.status_code == 409
    chained = manager.post(
        f"/manage/workdays/{replacement_id}/reschedule",
        data={
            "new_date": date(2026, 10, 24).isoformat(),
            "confirm_move": "true",
            "csrf_token": manager_csrf,
        },
        follow_redirects=False,
    )
    assert chained.status_code == 303
    chained_id = uuid.UUID(chained.headers["location"].split("/")[2].split("?")[0])
    with factory() as db:
        assert db.get(Workday, replacement_id).status == "ABANDONED"
        assert db.get(Workday, chained_id).rescheduled_from_workday_id == replacement_id
        assert db.scalar(
            select(PersonalWorkdayEntry).where(
                PersonalWorkdayEntry.workday_id == chained_id
            )
        ) is None
    stale_response = employee.post(
        f"/day/{replacement_id}/replacement-response",
        data={"response": "ACCEPTED", "csrf_token": employee_csrf},
    )
    assert stale_response.status_code == 404


def test_reschedule_rebuilds_travel_skips_applications_and_does_not_double_hours(
    routed_db,
) -> None:  # type: ignore[no-untyped-def]
    factory, (region_id, _track_id, position_id, person_id) = routed_db
    workday_id = _publish_rows(
        factory,
        region_id=region_id,
        position_id=position_id,
        work_date=date(2026, 11, 12),
        rows=[(person_id, "Amy Crew", "", False), (None, "", "", False)],
    )
    with factory() as db:
        source = db.get(Workday, workday_id)
        published = db.get(WorkdayRevision, source.current_published_revision_id)
        published.standard_travel_enabled = True
        published.on_track_time = time(9)
        published.last_race_time = time(16)
        published.travel_departure_time = time(12)
        published.travel_to_hotel_minutes = 120
        published.hotel_to_track_minutes = 30
        published.return_travel_minutes = 120
        published.start_origin = "Auckland"
        published.default_hotel = "Track Hotel"
        published.finish_destination = "Auckland"
        old_operation = Operation(
            name="Old operation",
            region_id=region_id,
            starts_on=date(2026, 11, 11),
            ends_on=date(2026, 11, 12),
        )
        db.add(old_operation)
        db.flush()
        source.operation_id = old_operation.id
        db.add(
            TravelLeg(
                operation_id=old_operation.id,
                travel_date=date(2026, 11, 11),
                origin="Auckland",
                destination="Track Hotel",
            )
        )
        old_travel = Workday(
            region_id=region_id,
            operation_id=old_operation.id,
            generated_from_workday_id=source.id,
            category="TRAVEL_DAY",
            created_by_user_id=source.created_by_user_id,
        )
        db.add(old_travel)
        db.flush()
        old_travel_revision = WorkdayRevision(
            workday_id=old_travel.id,
            revision_number=1,
            state="PUBLISHED",
            work_date=date(2026, 11, 11),
            created_by_user_id=source.created_by_user_id,
        )
        db.add(old_travel_revision)
        db.flush()
        old_travel.current_published_revision_id = old_travel_revision.id
        open_row = db.scalar(
            select(Assignment).where(
                Assignment.revision_id == published.id,
                Assignment.status == "OPEN",
            )
        )
        db.add(
            OpenPositionApplication(
                revision_id=published.id,
                slot_key=open_row.slot_key,
                person_id=person_id,
            )
        )
        db.commit()
        old_operation_id = old_operation.id
        old_travel_id = old_travel.id

    manager = TestClient(app)
    csrf = _login(manager, "manager@example.test", "123456")
    moved = manager.post(
        f"/manage/workdays/{workday_id}/reschedule",
        data={
            "new_date": "2026-11-14",
            "confirm_move": "true",
            "csrf_token": csrf,
        },
        follow_redirects=False,
    )
    assert moved.status_code == 303, moved.text
    replacement_id = uuid.UUID(moved.headers["location"].split("/")[2].split("?")[0])
    with factory() as db:
        replacement = db.get(Workday, replacement_id)
        new_travel = db.scalar(
            select(Workday).where(Workday.generated_from_workday_id == replacement.id)
        )
        assert db.get(Workday, old_travel_id).status == "ABANDONED"
        assert db.get(Operation, old_operation_id) is not None
        assert list(
            db.scalars(select(TravelLeg).where(TravelLeg.operation_id == old_operation_id))
        )
        assert replacement.operation_id != old_operation_id
        assert new_travel is not None and new_travel.status == "SCHEDULED"
        assert new_travel.current_published_revision_id is not None
        move_events = list(
            db.scalars(
                select(NotificationEvent).where(
                    NotificationEvent.event_type == "ROSTER_PUBLISHED",
                    NotificationEvent.workday_id.in_([replacement.id, new_travel.id]),
                )
            )
        )
        assert len(move_events) == 1 and move_events[0].workday_id == replacement.id
        assert list(
            db.scalars(
                select(TravelLeg).where(TravelLeg.operation_id == replacement.operation_id)
            )
        )
        assert not list(
            db.scalars(
                select(OpenPositionApplication).join(
                    WorkdayRevision,
                    WorkdayRevision.id == OpenPositionApplication.revision_id,
                ).where(WorkdayRevision.workday_id == replacement.id)
            )
        )
        employee = db.scalar(select(User).where(User.email == "amy@example.test"))
        actor = actor_for(db, employee)
        rows = published_hours(
            db,
            actor=actor,
            start=date(2026, 11, 1),
            end=date(2026, 11, 30),
            management=False,
        )
        race_rows = [row for row in rows if row["category"] == "Race Day"]
        assert len(race_rows) == 1 and race_rows[0]["date"] == date(2026, 11, 14)
        assert all(row["date"] != date(2026, 11, 12) for row in rows)


def test_manager_publish_employee_visibility_and_route_authorization(routed_db) -> None:  # type: ignore[no-untyped-def]
    factory, (region_id, track_id, position_id, person_id) = routed_db
    work_date = local_today() + timedelta(days=7)
    manager_client = TestClient(app)
    csrf = _login(manager_client, "manager@example.test", "123456")
    created = manager_client.post(
        "/manage/workdays",
        data={
            "region_id": str(region_id),
            "category": "RACE_DAY",
            "work_date": work_date.isoformat(),
            "track_id": str(track_id),
            "title": "Ellerslie Race Day",
            "csrf_token": csrf,
        },
        follow_redirects=False,
    )
    assert created.status_code == 303
    workday_path = created.headers["location"]
    workday_id = uuid.UUID(workday_path.rsplit("/", 1)[-1])
    assert manager_client.get(workday_path).status_code == 200
    with factory() as db:
        expected_version = db.get(Workday, workday_id).lock_version
    assigned = manager_client.post(
        f"{workday_path}/assignments",
        data={
            "base_position_id": str(position_id),
            "slot_index": "2",
            "person_id": str(person_id),
            "status": "ASSIGNED",
            "note": "Private transport",
            "note_private": "true",
            "expected_version": str(expected_version),
            "csrf_token": csrf,
        },
        follow_redirects=False,
    )
    assert assigned.status_code == 303
    assert "First publication" in manager_client.get(f"{workday_path}/preview").text
    with factory() as db:
        workday = db.get(Workday, workday_id)
        draft_id = workday.current_draft_revision_id
        expected_version = workday.lock_version
    published = manager_client.post(
        f"{workday_path}/publish",
        data={
            "draft_id": str(draft_id),
            "expected_version": str(expected_version),
            "csrf_token": csrf,
        },
        follow_redirects=False,
    )
    assert published.status_code == 303

    employee_client = TestClient(app)
    _login(employee_client, "amy@example.test", "654321")
    month = employee_client.get(f"/month?year={work_date.year}&month={work_date.month}")
    assert month.status_code == 200
    assert "Ellerslie" in month.text and "CCU 2" in month.text
    day = employee_client.get(f"/day/{workday_id}")
    assert "Private transport" in day.text
    assert "'unsafe-inline'" not in day.headers["content-security-policy"]
    assert 'data-presentation="track-01"' in day.text
    day_payload = employee_client.get(f"/api/day/{workday_id}").json()
    assert "cached_at" not in day_payload
    assert day_payload["workday"]["revision_id"]
    assert day_payload["workday"]["revision_number"] == 1
    crew = employee_client.get(
        f"/crew?region_id={region_id}&year={work_date.year}&month={work_date.month}"
    )
    assert crew.status_code == 200 and "Ellerslie" in crew.text
    assert employee_client.get("/manage/workdays/new").status_code == 403
    assert manager_client.get("/admin").status_code == 403
    viewer_client = TestClient(app)
    _login(viewer_client, "viewer@example.test", "112233")
    viewer_day = viewer_client.get(f"/day/{workday_id}")
    assert viewer_day.status_code == 200
    assert "Ellerslie Race Day" in viewer_day.text
    assert "Private transport" not in viewer_day.text
    assert 'href="/manage"' not in viewer_day.text
    assert 'href="/manage/hours"' not in viewer_day.text
    assert viewer_client.get("/manage/hours").status_code == 403
    assert "Edit private draft" not in viewer_day.text
    assert viewer_client.get(workday_path).status_code == 403

    # A later draft cannot leak into the employee read path.
    assert manager_client.get(workday_path).status_code == 200
    with factory() as db:
        workday = db.get(Workday, workday_id)
        draft = db.get(WorkdayRevision, workday.current_draft_revision_id)
        draft.track_name_snapshot = "DRAFT LEAK SENTINEL"
        db.commit()
    assert "DRAFT LEAK SENTINEL" not in employee_client.get(
        f"/month?year={work_date.year}&month={work_date.month}"
    ).text
    with factory() as db:
        workday = db.get(Workday, workday_id)
        published_assignment = db.scalar(
            select(Assignment).where(
                Assignment.revision_id == workday.current_published_revision_id,
                Assignment.person_id == person_id,
            )
        )
        slot_key = published_assignment.slot_key
    assert viewer_client.get(f"/day/{workday_id}/assignments/{slot_key}/decline").status_code == 404
    confirmation = employee_client.get(f"/day/{workday_id}/assignments/{slot_key}/decline")
    assert confirmation.status_code == 200 and "Manager attention" in confirmation.text
    declined = employee_client.post(
        f"/day/{workday_id}/assignments/{slot_key}/decline",
        data={"confirm": "yes", "csrf_token": employee_client.cookies.get("ontrack_csrf")},
        follow_redirects=False,
    )
    assert declined.status_code == 303
    with factory() as db:
        workday = db.get(Workday, workday_id)
        current = db.scalar(
            select(Assignment).where(
                Assignment.revision_id == workday.current_published_revision_id,
                Assignment.slot_key == slot_key,
            )
        )
        assert current.person_id is None and current.status == "MANAGER_ACTION_REQUIRED"
        assert workday.current_draft_revision_id is None

    admin_client = TestClient(app)
    admin_csrf = _login(admin_client, "admin@example.test", "99887766")
    assert admin_client.get("/admin").status_code == 200
    with factory() as db:
        viewer_id = db.scalar(select(User.id).where(User.email == "viewer@example.test"))
    disabled = admin_client.post(
        f"/admin/users/{viewer_id}/status",
        data={"account_status": "DISABLED", "csrf_token": admin_csrf},
        follow_redirects=False,
    )
    assert disabled.status_code == 303
    assert viewer_client.get("/month", follow_redirects=False).status_code == 303


def test_stale_builder_post_returns_409_and_preserves_newer_details(routed_db) -> None:  # type: ignore[no-untyped-def]
    factory, (region_id, track_id, _position_id, _person_id) = routed_db
    client = TestClient(app)
    csrf = _login(client, "manager@example.test", "123456")
    response = client.post(
        "/manage/workdays",
        data={
            "region_id": str(region_id),
            "category": "RACE_DAY",
            "work_date": "2026-10-01",
            "track_id": str(track_id),
            "title": "Initial",
            "csrf_token": csrf,
        },
        follow_redirects=False,
    )
    workday_id = uuid.UUID(response.headers["location"].rsplit("/", 1)[-1])
    with factory() as db:
        expected_version = db.get(Workday, workday_id).lock_version
    form = {
        "work_date": "2026-10-02",
        "track_id": str(track_id),
        "title": "Manager A",
        "expected_version": str(expected_version),
        "csrf_token": csrf,
    }
    assert client.post(f"/manage/workdays/{workday_id}/details", data=form).status_code == 200
    stale = client.post(
        f"/manage/workdays/{workday_id}/details",
        data={**form, "title": "Manager B stale"},
    )
    assert stale.status_code == 409
    assert "changed by someone else" in stale.text
    with factory() as db:
        workday = db.get(Workday, workday_id)
        draft = db.get(WorkdayRevision, workday.current_draft_revision_id)
        assert draft.title == "Manager A"


def test_combined_builder_save_updates_rows_once_then_previews(routed_db) -> None:  # type: ignore[no-untyped-def]
    factory, (region_id, track_id, position_id, person_id) = routed_db
    manager = TestClient(app)
    csrf = _login(manager, "manager@example.test", "123456")
    created = manager.post(
        "/manage/workdays",
        data={
            "region_id": str(region_id),
            "category": "RACE_DAY",
            "work_date": "2026-10-19",
            "track_id": str(track_id),
            "title": "Combined save",
            "csrf_token": csrf,
        },
        follow_redirects=False,
    )
    workday_id = uuid.UUID(created.headers["location"].rsplit("/", 1)[1])
    with factory() as db:
        workday = db.get(Workday, workday_id)
        draft_id = workday.current_draft_revision_id
        version = workday.lock_version
    data = {
        "expected_version": str(version),
        "csrf_token": csrf,
        "work_date": "2026-10-20",
        "track_id": str(track_id),
        "title": "Combined save updated",
        "start_time": "0730",
        "end_time": "18:00",
        "on_track_time": "08:30",
        "first_trial_time": "09:00",
        "first_race_time": "10:00",
        "last_race_time": "17:00",
        "race_count": "8",
        "day_note": "Operational note",
        "assignment_id": "",
        "base_position_id": str(position_id),
        "slot_index": "2",
        "person_id": str(person_id),
        "status": "ASSIGNED",
        "note": "Private row note",
        "note_private": "1",
        "assignment_start_time": "08:00",
        "assignment_end_time": "17:30",
    }
    saved = manager.post(
        f"/manage/workdays/{workday_id}/draft", data=data, follow_redirects=False
    )
    assert saved.status_code == 303
    assert saved.headers["location"] == f"/manage/workdays/{workday_id}/preview"
    preview = manager.get(saved.headers["location"])
    assert "Publication preview" in preview.text
    assert "Combined save updated" in preview.text
    assert "CCU 2" in preview.text and "Amy Crew" in preview.text
    with factory() as db:
        workday = db.get(Workday, workday_id)
        row = db.scalar(select(Assignment).where(Assignment.revision_id == draft_id))
        assert workday.lock_version == version + 1
        assert (row.display_name_snapshot, row.person_id, row.note_private) == (
            "CCU 2",
            person_id,
            True,
        )
        current_version = workday.lock_version
        assignment_id = row.id
    inconsistent = manager.post(
        f"/manage/workdays/{workday_id}/draft",
        data={
            **data,
            "expected_version": str(current_version),
            "assignment_id": [str(assignment_id), ""],
            "base_position_id": [str(position_id), str(position_id)],
            "slot_index": ["2", "3"],
            "person_id": [str(person_id), str(person_id)],
            "status": ["ASSIGNED", "ASSIGNED"],
            "note": ["Private row note", "Second position"],
            "note_private": ["1", "1"],
            "assignment_start_time": ["08:00", "09:00"],
            "assignment_end_time": ["17:30", "16:30"],
            "transport_mode": ["UNASSIGNED", "UNASSIGNED"],
            "vehicle_id": ["", ""],
            "custom_transport_text": ["", ""],
            "accommodation_name": ["", ""],
            "uses_standard_travel": ["1", "1"],
            "hotel_to_track_minutes_override": ["", ""],
            "finish_destination_override": ["Home", "Elsewhere"],
            "return_travel_minutes_override": ["60", "60"],
        },
        follow_redirects=False,
    )
    assert inconsistent.status_code == 400
    assert "Roster not saved" in inconsistent.text
    assert "Use one consistent travel plan for each person" in inconsistent.text
    assert '"detail"' not in inconsistent.text
    stale = manager.post(
        f"/manage/workdays/{workday_id}/draft", data=data, follow_redirects=False
    )
    assert stale.status_code == 409


def test_office_day_builder_uses_category_appropriate_fields_and_warnings(routed_db) -> None:  # type: ignore[no-untyped-def]
    _factory, (region_id, _track_id, _position_id, _person_id) = routed_db
    manager = TestClient(app)
    csrf = _login(manager, "manager@example.test", "123456")
    created = manager.post(
        "/manage/workdays",
        data={
            "region_id": str(region_id),
            "category": "OFFICE_DAY",
            "work_date": "2026-10-20",
            "track_id": "",
            "title": "Office planning",
            "csrf_token": csrf,
        },
        follow_redirects=False,
    )
    edit_url = created.headers["location"]
    builder = manager.get(edit_url)
    assert "Office planning" in builder.text
    assert 'name="first_race_time"' in builder.text and 'name="race_count"' in builder.text
    preview = manager.get(edit_url + "/preview")
    assert "Race Day timing is incomplete" not in preview.text


def test_race_day_builder_keeps_trials_optional_until_data_exists(routed_db) -> None:  # type: ignore[no-untyped-def]
    factory, (region_id, track_id, _position_id, _person_id) = routed_db
    manager = TestClient(app)
    csrf = _login(manager, "manager@example.test", "123456")
    created = manager.post(
        "/manage/workdays",
        data={
            "region_id": str(region_id),
            "category": "RACE_DAY",
            "work_date": "2026-10-21",
            "track_id": str(track_id),
            "title": "Optional trials",
            "csrf_token": csrf,
        },
        follow_redirects=False,
    )
    edit_url = created.headers["location"]
    workday_id = uuid.UUID(edit_url.rsplit("/", 1)[1])

    page = manager.get(edit_url).text
    trial_options = re.search(r'<details class="builder-trial-options"[^>]*>', page)
    assert trial_options and " open" not in trial_options.group(0)
    assert 'data-primary="0"' in trial_options.group(0)
    assert "Trials (optional)" in page
    assert "Apply preset" in page and "Apply defaults" not in page
    assert "Recommended positions" not in page
    assert "Canonical positions and crew" not in page
    assert "One atomic private-draft update" not in page
    assert "Slot number" not in page
    assert 'name="slot_index"' in page and 'type="hidden" name="slot_index"' in page
    assert 'data-random-fill' in page
    assert 'aria-label="Randomly fill unassigned Positions' in page

    with factory() as db:
        workday = db.get(Workday, workday_id)
        draft = db.get(WorkdayRevision, workday.current_draft_revision_id)
        draft.first_trial_time = time(9, 15)
        db.commit()
    page_with_trials = manager.get(edit_url).text
    trial_options = re.search(
        r'<details class="builder-trial-options"[^>]*>', page_with_trials
    )
    assert trial_options and " open" in trial_options.group(0)


def test_trials_builder_renders_switchable_timing_fields_and_clears_race_fields(routed_db) -> None:  # type: ignore[no-untyped-def]
    factory, (region_id, track_id, _position_id, _person_id) = routed_db
    manager = TestClient(app)
    csrf = _login(manager, "manager@example.test", "123456")
    created = manager.post(
        "/manage/workdays",
        data={
            "region_id": str(region_id),
            "category": "TRIALS",
            "work_date": "2026-10-21",
            "track_id": str(track_id),
            "title": "Trials timing",
            "csrf_token": csrf,
        },
        follow_redirects=False,
    )
    edit_url = created.headers["location"]
    workday_id = uuid.UUID(edit_url.rsplit("/", 1)[1])
    with factory() as db:
        workday = db.get(Workday, workday_id)
        draft = db.get(WorkdayRevision, workday.current_draft_revision_id)
        draft.on_track_time = time(8, 30)
        draft.first_trial_time = time(9, 15)
        draft.last_trial_time = time(11, 30)
        draft.first_race_time = time(12, 45)
        version = workday.lock_version
        db.commit()

    builder = manager.get(edit_url)
    trial_options = re.search(r'<details class="builder-trial-options"[^>]*>', builder.text)
    assert trial_options and " open" in trial_options.group(0)
    assert 'data-primary="1"' in trial_options.group(0)
    assert 'name="on_track_time" value="08:30"' in builder.text
    assert 'name="first_trial_time" value="09:15"' in builder.text
    assert 'name="first_race_time" value="12:45"' in builder.text
    assert 'name="last_race_time"' in builder.text
    assert 'name="race_count"' in builder.text
    saved = manager.post(
        edit_url + "/draft",
        data={
            "expected_version": str(version),
            "csrf_token": csrf,
            "work_date": "2026-10-21",
            "track_id": str(track_id),
            "title": "Trials timing",
            "start_time": "07:30",
            "on_track_time": "08:30",
            "first_trial_time": "09:45",
            "last_trial_time": "11:30",
            "end_time": "14:00",
            "day_note": "",
            "change_reason": "",
        },
        follow_redirects=False,
    )
    assert saved.status_code == 303
    preview = manager.get(saved.headers["location"])
    assert "09:45" in preview.text
    with factory() as db:
        workday = db.get(Workday, workday_id)
        draft = db.get(WorkdayRevision, workday.current_draft_revision_id)
        assert draft.first_trial_time == time(9, 45)
        assert draft.first_race_time is None
        assert draft.last_trial_time == time(11, 30)
        version = workday.lock_version
    switched = manager.post(
        edit_url + "/draft",
        data={
            "expected_version": str(version), "csrf_token": csrf,
            "work_date": "2026-10-21", "track_id": str(track_id),
            "day_type": "RACE_DAY:THOROUGHBRED", "title": "Race timing",
            "start_time": "07:30", "on_track_time": "08:30",
            "first_trial_time": "09:45", "last_trial_time": "11:30",
            "first_race_time": "12:00", "last_race_time": "16:00",
            "race_count": "8", "end_time": "18:00", "day_note": "",
            "change_reason": "",
        },
        follow_redirects=False,
    )
    assert switched.status_code == 303
    with factory() as db:
        workday = db.get(Workday, workday_id)
        draft = db.get(WorkdayRevision, workday.current_draft_revision_id)
        assert draft.last_trial_time == time(11, 30)
        assert draft.first_trial_time == time(9, 45)
        assert draft.first_race_time == time(12)
        version = workday.lock_version
    switched = manager.post(
        edit_url + "/draft",
        data={
            "expected_version": str(version), "csrf_token": csrf,
            "work_date": "2026-10-21", "track_id": str(track_id),
            "day_type": "OFFICE_DAY:", "title": "Office timing",
            "start_time": "07:30", "on_track_time": "08:30",
            "first_trial_time": "09:45", "last_trial_time": "11:30",
            "first_race_time": "12:00", "last_race_time": "16:00",
            "race_count": "8", "end_time": "18:00",
            "standard_travel_enabled": "1", "start_origin": "Office",
            "finish_destination": "Office", "day_note": "",
            "change_reason": "",
        },
        follow_redirects=False,
    )
    assert switched.status_code == 303
    with factory() as db:
        workday = db.get(Workday, workday_id)
        draft = db.get(WorkdayRevision, workday.current_draft_revision_id)
        assert workday.category == "OFFICE_DAY"
        assert draft.standard_travel_enabled is False
        assert (draft.on_track_time, draft.first_trial_time, draft.last_trial_time) == (
            None, None, None,
        )
        assert (draft.first_race_time, draft.last_race_time, draft.race_count) == (
            None, None, None,
        )
        assert (draft.start_origin, draft.finish_destination) == ("Office", "Office")


def test_position_aware_crew_picker_endpoint_keeps_duplicate_ids_distinct(routed_db) -> None:  # type: ignore[no-untyped-def]
    factory, (region_id, track_id, _position_id, _person_id) = routed_db
    manager = TestClient(app)
    csrf = _login(manager, "manager@example.test", "123456")
    created = manager.post(
        "/manage/workdays",
        data={
            "region_id": str(region_id),
            "category": "TRIALS",
            "work_date": "2026-10-22",
            "track_id": str(track_id),
            "title": "Picker endpoint",
            "csrf_token": csrf,
        },
        follow_redirects=False,
    )
    workday_id = uuid.UUID(created.headers["location"].rsplit("/", 1)[1])
    with factory() as db:
        manager_id = db.scalar(select(User.id).where(User.email == "manager@example.test"))
        position = BasePosition(name="Head On")
        duplicate_a = Person(display_name="John Smith", home_region_id=region_id)
        duplicate_b = Person(display_name="John Smith", home_region_id=region_id)
        db.add_all([position, duplicate_a, duplicate_b])
        db.flush()
        db.add(
            PositionCapability(
                person_id=duplicate_a.id,
                base_position_id=position.id,
                signal=CapabilitySignal.MANAGER_ALLOW.value,
                changed_by_user_id=manager_id,
            )
        )
        db.commit()
        position_id = position.id

    response = manager.get(
        f"/manage/workdays/{workday_id}/crew-picker?position_id={position_id}"
    )
    assert response.status_code == 200
    johns = [
        person
        for group in response.json()["groups"]
        for person in group["people"]
        if person["label"] == "John Smith"
    ]
    assert len(johns) == 2
    assert len({person["id"] for person in johns}) == 2
    assert len({person["context"] for person in johns}) == 2
    capable = next(person for person in johns if person["id"] == str(duplicate_a.id))
    unknown = next(person for person in johns if person["id"] == str(duplicate_b.id))
    assert capable["eligible"] is True
    assert unknown["eligible"] is False
    local_groups = [group for group in response.json()["groups"] if group["local"]]
    assert len(local_groups) == 1
    assert {person["id"] for person in local_groups[0]["people"]}.issuperset(
        {str(duplicate_a.id), str(duplicate_b.id)}
    )

    employee = TestClient(app)
    _login(employee, "amy@example.test", "654321")
    assert employee.get(
        f"/manage/workdays/{workday_id}/crew-picker?position_id={position_id}"
    ).status_code == 403


def test_builder_groups_relevant_crew_before_other_active_people(routed_db) -> None:  # type: ignore[no-untyped-def]
    factory, (region_id, track_id, position_id, _person_id) = routed_db
    manager = TestClient(app)
    csrf = _login(manager, "manager@example.test", "123456")
    created = manager.post(
        "/manage/workdays",
        data={
            "region_id": str(region_id),
            "category": "RACE_DAY",
            "work_date": "2026-10-22",
            "track_id": str(track_id),
            "title": "Grouped builder",
            "csrf_token": csrf,
        },
        follow_redirects=False,
    )
    edit_url = created.headers["location"]
    manager.get(edit_url)
    workday_id = uuid.UUID(edit_url.rsplit("/", 1)[1])
    with factory() as db:
        version = db.get(Workday, workday_id).lock_version
    added = manager.post(
        edit_url + "/assignments",
        data={
            "base_position_id": str(position_id),
            "slot_index": "1",
            "person_id": "",
            "status": "TBC",
            "note": "",
            "assignment_start_time": "",
            "assignment_end_time": "",
            "expected_version": str(version),
            "csrf_token": csrf,
        },
        follow_redirects=False,
    )
    assert added.status_code == 303
    html = manager.get(edit_url).text
    assert '<div class="search-picker-group" data-local="1">' in html
    assert "<span>Northern crew</span>" in html
    assert '<div class="search-picker-group" data-local="0">' in html
    assert "<span>Other regions</span>" in html
    assert html.index("Northern crew") < html.index("Amy Crew")
    assert html.index("Amy Crew") < html.index("Other regions")
    assert "No position history recorded" in html
    assert "Move up" not in html and "Move down" not in html


def test_historical_self_decline_is_denied_without_changing_publication(routed_db) -> None:  # type: ignore[no-untyped-def]
    factory, (region_id, _track_id, position_id, person_id) = routed_db
    workday_id = _publish_rows(
        factory,
        region_id=region_id,
        position_id=position_id,
        work_date=local_today() - timedelta(days=1),
        rows=[(person_id, "Amy Crew", "Historical hours sentinel", True)],
    )
    with factory() as db:
        workday = db.get(Workday, workday_id)
        original_revision_id = workday.current_published_revision_id
        assignment = db.scalar(
            select(Assignment).where(Assignment.revision_id == original_revision_id)
        )
        slot_key = assignment.slot_key
    employee = TestClient(app)
    csrf = _login(employee, "amy@example.test", "654321")
    assert employee.get(
        f"/day/{workday_id}/assignments/{slot_key}/decline"
    ).status_code == 403
    blocked = employee.post(
        f"/day/{workday_id}/assignments/{slot_key}/decline",
        data={"confirm": "yes", "csrf_token": csrf},
        follow_redirects=False,
    )
    assert blocked.status_code == 403
    with factory() as db:
        workday = db.get(Workday, workday_id)
        assignment = db.scalar(
            select(Assignment).where(Assignment.revision_id == original_revision_id)
        )
        assert workday.current_published_revision_id == original_revision_id
        assert assignment.person_id == person_id and assignment.status == "ASSIGNED"


def test_contractor_online_and_offline_day_rows_are_personal(routed_db) -> None:  # type: ignore[no-untyped-def]
    factory, (region_id, _track_id, position_id, employee_person_id) = routed_db
    with factory() as db:
        contractor_id = db.scalar(select(Person.id).where(Person.email == "private-south@example.test"))
        third = Person(display_name="Casey Other", home_region_id=region_id)
        db.add(third)
        db.commit()
        third_id = third.id
    workday_id = _publish_rows(
        factory,
        region_id=region_id,
        position_id=position_id,
        work_date=date(2026, 10, 8),
        day_note="Normal operations note",
        rows=[
            (contractor_id, "South Crew", "Contractor private note", True),
            (employee_person_id, "Amy Crew", "Amy private note", True),
            (third_id, "Casey Other", "Public row note", False),
        ],
    )
    with factory() as db:
        workday = db.get(Workday, workday_id)
        manager_id = db.scalar(select(User.id).where(User.email == "manager@example.test"))
        db.add(
            HumanChange(
                workday_id=workday.id,
                revision_id=workday.current_published_revision_id,
                actor_user_id=manager_id,
                summary="Casey Other moved to a private role",
            )
        )
        db.commit()
    contractor = TestClient(app)
    _login(contractor, "private-south@example.test", "778899")
    day = contractor.get(f"/day/{workday_id}")
    assert day.status_code == 200
    assert "Normal operations note" in day.text
    assert "South Crew" in day.text and "Contractor private note" in day.text
    assert "Amy Crew" not in day.text and "Casey Other" not in day.text
    assert "Casey Other moved to a private role" not in day.text
    contractor_api = contractor.get(f"/api/day/{workday_id}").json()
    assert contractor_api["offline_cacheable"] is True
    assert contractor_api["workday"]["category"] == "RACE_DAY"
    assert {
        "on_track",
        "first_trial",
        "first_race",
        "last_race",
        "race_count",
    } <= contractor_api["workday"].keys()
    assert "cached_at" not in contractor_api
    assert [row["person"] for row in contractor_api["workday"]["assignments"]] == ["South Crew"]

    employee = TestClient(app)
    _login(employee, "amy@example.test", "654321")
    online = employee.get(f"/day/{workday_id}")
    assert "South Crew" in online.text and "Amy Crew" in online.text and "Casey Other" in online.text
    assert "Casey Other moved to a private role" in online.text
    employee_api = employee.get(f"/api/day/{workday_id}").json()
    assert [row["person"] for row in employee_api["workday"]["assignments"]] == ["Amy Crew"]
    assert employee_api["workday"]["assignments"][0]["note"] == "Amy private note"

    manager = TestClient(app)
    _login(manager, "manager@example.test", "123456")
    manager_api = manager.get(f"/api/day/{workday_id}").json()
    assert manager_api["offline_cacheable"] is False
    assert manager_api["workday"]["assignments"] == []


def test_linked_day_and_external_event_share_safe_source_evidence(routed_db) -> None:  # type: ignore[no-untyped-def]
    factory, (region_id, track_id, position_id, person_id) = routed_db
    manual_workday_id = _publish_rows(
        factory,
        region_id=region_id,
        position_id=position_id,
        work_date=date(2026, 10, 24),
        rows=[(person_id, "Amy Crew", "", True)],
    )
    workday_id = _publish_rows(
        factory,
        region_id=region_id,
        position_id=position_id,
        work_date=date(2026, 10, 25),
        rows=[(person_id, "Amy Crew", "", True)],
    )
    with factory() as db:
        event_row = ExternalCalendarEvent(
            event_date=date(2026, 10, 25),
            track_id=track_id,
            external_track_name="Ellerslie",
            discipline="THOROUGHBRED",
            event_kind="RACE",
            status="SCHEDULED",
            first_race_time=time(12, 30),
            race_count=8,
            field_provenance={"first_race_time": ["LOVE_RACING"]},
            presentation_provider="LOVE_RACING",
        )
        db.add(event_row)
        db.flush()
        db.add(
            ExternalEventObservation(
                event_id=event_row.id,
                provider="LOVE_RACING",
                provider_event_id="safe-source-1",
                payload_hash="safe-source-hash",
                source_track_name="Ellerslie",
                parsed_facts={"first_race_time": "12:30", "race_count": 8},
                raw_payload={"access_token": "SECRET-SENTINEL", "meeting": "Ellerslie"},
                mapping_state="MAPPED",
                reconciliation_state="MATCHED",
            )
        )
        db.get(Workday, workday_id).external_event_id = event_row.id
        db.commit()
        event_id = event_row.id

    employee = TestClient(app)
    _login(employee, "amy@example.test", "654321")
    day = employee.get(f"/day/{workday_id}")
    external = employee.get(f"/external-events/{event_id}")
    for response in (day, external):
        assert response.status_code == 200
        assert "Canonical facts" in response.text
        assert "Love Racing" in response.text
        assert "SECRET-SENTINEL" not in response.text
    assert "Raw Race Day Data" in day.text
    assert "Technical source history" in external.text
    manual_day = employee.get(f"/day/{manual_workday_id}")
    assert "Raw Race Day Data" not in manual_day.text
    assert "Saturday 24 October 2026" in manual_day.text


def test_race_programme_health_presentation_and_manual_retry_authorization(
    routed_db, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    factory, (_region_id, track_id, _position_id, _person_id) = routed_db
    with factory() as db:
        event = ExternalCalendarEvent(
            event_date=date(2026, 10, 9),
            track_id=track_id,
            external_track_name="Taupo",
            discipline="THOROUGHBRED",
            event_kind="RACE",
            status="SCHEDULED",
            meeting_name="Racing Taupo @ Taupo",
            programme_status="PARTIAL",
            field_provenance={"programme_status": ["LOVE_RACING"]},
            first_race_time=time(12, 34),
            last_race_time=time(17, 24),
            race_count=9,
            detail_checked_at=datetime(2026, 10, 8, 7, 5, tzinfo=UTC),
            next_detail_due_at=datetime(2026, 10, 8, 9, 5, tzinfo=UTC),
            detail_failure_count=1,
            latest_detail_error="Source response exceeded the configured size limit.",
        )
        db.add(event)
        db.flush()
        db.add(
            ExternalEventObservation(
                event_id=event.id,
                provider="LOVE_RACING",
                provider_event_id="55962",
                payload_hash="taupo-programme-observation",
                source_track_name="Taupo",
                parsed_facts={},
                raw_payload={},
                mapping_state="MAPPED",
                reconciliation_state="MATCHED",
            )
        )
        db.add_all(
            [
                ExternalEventObservation(
                    event_id=event.id,
                    provider="LOVE_RACING",
                    provider_event_id="55962",
                    payload_hash="taupo-programme-observation-duplicate-1",
                    source_track_name="Taupo",
                    parsed_facts={},
                    raw_payload={},
                    mapping_state="MAPPED",
                    reconciliation_state="MATCHED",
                ),
                ExternalEventObservation(
                    event_id=event.id,
                    provider="LOVE_RACING",
                    provider_event_id="55962",
                    payload_hash="taupo-programme-observation-duplicate-2",
                    source_track_name="Taupo",
                    parsed_facts={},
                    raw_payload={},
                    mapping_state="MAPPED",
                    reconciliation_state="MATCHED",
                ),
            ]
        )
        db.commit()
        event_id = event.id

    manager = TestClient(app)
    manager_csrf = _login(manager, "manager@example.test", "123456")
    detail = manager.get(f"/external-events/{event_id}")
    assert detail.status_code == 200
    assert "Racing Taupo @ Taupo" in detail.text
    assert "First race" in detail.text and "12:34" in detail.text
    assert "First trial" not in detail.text
    assert "Love Racing</strong> · Programme partial" in detail.text
    assert "Technical source history" in detail.text
    assert '<details class="source-programme-diagnostics">' in detail.text
    assert "Last detail check" in detail.text and "Next automatic retry" in detail.text
    assert "Source response exceeded the configured size limit." in detail.text
    assert "Refresh programme now" not in detail.text

    monkeypatch.setattr("app.external_calendar.routes.local_today", lambda: date(2026, 10, 8))
    sources = manager.get("/admin/online-sources")
    assert sources.status_code == 200
    assert "Programme detail" in sources.text
    assert "Failed upcoming" in sources.text
    assert "Programme detail warning" in sources.text
    programme_detail = sources.text.split("<h3>Programme detail</h3>", 1)[1].split("</dl>", 1)[0]
    assert "<dt>Partial / Awaiting</dt><dd>1</dd>" in programme_detail

    assert manager.post(
        f"/external-events/{event_id}/refresh-programme",
        data={"csrf_token": manager_csrf},
    ).status_code == 404


def test_online_source_status_is_regional_but_controls_are_admin_only(
    routed_db, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    manager = TestClient(app)
    manager_csrf = _login(manager, "manager@example.test", "123456")
    manager_page = manager.get("/admin/online-sources")
    assert manager_page.status_code == 200
    assert "Mapping status" in manager_page.text
    assert "/admin/online-sources/LOVE_RACING/toggle" not in manager_page.text
    assert "Download source mapping template" not in manager_page.text
    assert manager.post(
        "/admin/online-sources/LOVE_RACING/toggle",
        data={"csrf_token": manager_csrf},
    ).status_code == 403

    admin = TestClient(app)
    admin_csrf = _login(admin, "admin@example.test", "99887766")
    enabled = admin.post(
        "/admin/online-sources/LOVE_RACING/toggle",
        data={"csrf_token": admin_csrf},
        follow_redirects=False,
    )
    assert enabled.status_code == 303

    def fixture_refresh(_db, provider, *, actor_user_id):  # type: ignore[no-untyped-def]
        assert actor_user_id is not None
        return RefreshResult(
            provider,
            "PARTIAL",
            observations=5,
            created=2,
            matched=1,
            enriched=1,
            unresolved=1,
            warnings=["One venue needs mapping."],
            components={"calendar": "OK", "trials": "PARTIAL"},
        )

    monkeypatch.setattr("app.external_calendar.routes.refresh_provider", fixture_refresh)
    refreshed = admin.post(
        "/admin/online-sources/LOVE_RACING/refresh",
        data={"csrf_token": admin_csrf},
    )
    assert refreshed.status_code == 200
    assert "Love Racing partial" in refreshed.text
    assert "5 observations" in refreshed.text
    assert "One venue needs mapping." in refreshed.text


def test_grouped_source_setup_create_track_map_and_safe_exports(routed_db) -> None:  # type: ignore[no-untyped-def]
    factory, (region_id, _track_id, _position_id, _person_id) = routed_db
    with factory() as db:
        for index in range(3):
            db.add(
                ExternalEventObservation(
                    provider="LOVE_RACING",
                    provider_event_id=f"tau-{index}",
                    payload_hash=f"tau-{index}",
                    source_track_name="Tauherenikau",
                    parsed_facts={
                        "event_date": f"2026-10-{index + 1:02d}",
                        "discipline": "THOROUGHBRED",
                        "event_kind": "RACE" if index < 2 else "TRIAL",
                    },
                    raw_payload={},
                )
            )
        db.add(
            ExternalEventObservation(
                provider="HRNZ",
                provider_event_id="club-only",
                payload_hash="club-only",
                source_track_name="Auckland Trotting Club Inc",
                parsed_facts={
                    "event_date": "2026-10-04",
                    "discipline": "HARNESS",
                    "event_kind": "RACE",
                    "venue_confidence": "CLUB_ONLY",
                },
                raw_payload={},
            )
        )
        db.commit()

    manager = TestClient(app)
    manager_csrf = _login(manager, "manager@example.test", "123456")
    assert manager.post(
        "/admin/online-sources/create-track-map",
        data={
            "provider": "LOVE_RACING",
            "external_track_name": "Tauherenikau",
            "region_id": str(region_id),
            "track_name": "Tauherenikau",
            "csrf_token": manager_csrf,
        },
    ).status_code == 403

    admin = TestClient(app)
    admin_csrf = _login(admin, "admin@example.test", "99887766")
    page = admin.get("/admin/online-sources")
    assert page.status_code == 200
    assert page.text.count("Tauherenikau") >= 1
    assert "3 observations" in page.text
    assert "Race + Trial" in page.text
    assert "Source identifies a club" in page.text
    assert admin.post(
        "/admin/online-sources/create-track-map",
        data={
            "provider": "LOVE_RACING",
            "external_track_name": "Tauherenikau",
            "region_id": str(region_id),
            "track_name": "Tauherenikau",
            "csrf_token": "invalid",
        },
    ).status_code == 403
    created = admin.post(
        "/admin/online-sources/create-track-map",
        data={
            "provider": "LOVE_RACING",
            "external_track_name": "Tauherenikau",
            "region_id": str(region_id),
            "track_name": "Tauherenikau",
            "csrf_token": admin_csrf,
        },
        follow_redirects=False,
    )
    assert created.status_code == 303
    with factory() as db:
        track = db.scalar(select(Track).where(Track.name == "Tauherenikau"))
        assert track is not None and 1 <= track.palette_slot <= 20
        mapping = db.scalar(
            select(ExternalTrackMapping).where(
                ExternalTrackMapping.external_track_name == "Tauherenikau"
            )
        )
        assert mapping.track_id == track.id
        assert all(
            row.event_id is not None
            for row in db.scalars(
                select(ExternalEventObservation).where(
                    ExternalEventObservation.source_track_name == "Tauherenikau"
                )
            )
        )
    blocked_club = admin.post(
        "/admin/online-sources/create-track-map",
        data={
            "provider": "HRNZ",
            "external_track_name": "Auckland Trotting Club Inc",
            "region_id": str(region_id),
            "track_name": "Auckland Trotting Club",
            "csrf_token": admin_csrf,
        },
    )
    assert blocked_club.status_code == 409

    structural = admin.get("/admin/structural-master-data.json")
    assert structural.status_code == 200
    payload = structural.json()
    assert set(payload) == {
        "version",
        "regions",
        "tracks",
        "crew_groups",
        "positions",
        "external_track_mappings",
    }
    serialized = structural.text.casefold()
    assert "credential_hash" not in serialized and "password" not in serialized
    template = admin.get("/admin/online-sources/source-inventory.json")
    assert template.status_code == 200
    assert template.json()["format"] == "ontrack-source-mapping-template"
    assert "raw_payload" not in template.text


def test_create_track_and_map_rolls_back_when_mapping_fails(routed_db, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    factory, (region_id, _track_id, _position_id, _person_id) = routed_db
    with factory() as db:
        db.add(
            ExternalEventObservation(
                provider="LOVE_RACING",
                provider_event_id="rollback-venue",
                payload_hash="rollback-venue",
                source_track_name="Rollback Raceway",
                parsed_facts={
                    "event_date": "2026-10-09",
                    "discipline": "THOROUGHBRED",
                    "event_kind": "RACE",
                },
                raw_payload={},
            )
        )
        db.commit()

    def fail_mapping(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        raise ValueError("Deliberate reconciliation failure.")

    monkeypatch.setattr("app.external_calendar.routes.confirm_track_mapping", fail_mapping)
    admin = TestClient(app)
    csrf = _login(admin, "admin@example.test", "99887766")
    response = admin.post(
        "/admin/online-sources/create-track-map",
        data={
            "provider": "LOVE_RACING",
            "external_track_name": "Rollback Raceway",
            "region_id": str(region_id),
            "track_name": "Rollback Raceway",
            "csrf_token": csrf,
        },
    )
    assert response.status_code == 409
    with factory() as db:
        assert db.scalar(select(Track).where(Track.name == "Rollback Raceway")) is None


def test_online_source_track_creation_rejects_cross_region_duplicate(routed_db) -> None:  # type: ignore[no-untyped-def]
    factory, (_region_id, _track_id, _position_id, _person_id) = routed_db
    with factory() as db:
        other_region = db.scalar(select(Region).where(Region.name == "Southern"))
        assert other_region is not None
        db.add(
            ExternalEventObservation(
                provider="LOVE_RACING",
                provider_event_id="duplicate-ellerslie",
                payload_hash="duplicate-ellerslie",
                source_track_name="Duplicate Ellerslie Source",
                parsed_facts={
                    "event_date": "2026-10-10",
                    "discipline": "THOROUGHBRED",
                    "event_kind": "RACE",
                },
                raw_payload={},
            )
        )
        db.commit()
        other_region_id = other_region.id

    admin = TestClient(app)
    csrf = _login(admin, "admin@example.test", "99887766")
    response = admin.post(
        "/admin/online-sources/create-track",
        data={
            "provider": "LOVE_RACING",
            "external_track_name": "Duplicate Ellerslie Source",
            "region_id": str(other_region_id),
            "track_name": "  ELLERSLIE  ",
            "csrf_token": csrf,
        },
    )
    assert response.status_code == 409
    assert "already exists" in response.text
    with factory() as db:
        assert len(list(db.scalars(select(Track).where(Track.name == "Ellerslie")))) == 1


def test_catalog_track_creation_rejects_normalized_duplicate(routed_db) -> None:  # type: ignore[no-untyped-def]
    factory, (region_id, _track_id, _position_id, _person_id) = routed_db
    manager = TestClient(app)
    csrf = _login(manager, "manager@example.test", "123456")
    response = manager.post(
        "/manage/catalog/tracks",
        data={"region_id": str(region_id), "name": "  ELLERSLIE  ", "csrf_token": csrf},
    )
    assert response.status_code == 409
    with factory() as db:
        other_region = db.scalar(select(Region).where(Region.id != region_id))
        other_region_id = other_region.id
    admin = TestClient(app)
    admin_csrf = _login(admin, "admin@example.test", "99887766")
    allowed = admin.post(
        "/manage/catalog/tracks",
        data={
            "region_id": str(other_region_id),
            "name": "  ELLERSLIE  ",
            "csrf_token": admin_csrf,
        },
        follow_redirects=False,
    )
    assert allowed.status_code == 303
    with factory() as db:
        created = db.scalar(
            select(Track).where(Track.region_id == other_region_id, Track.name == "ELLERSLIE")
        )
        assert created is not None and 1 <= created.palette_slot <= 20


def test_admin_catalog_archive_restore_and_remove_unused_guards(routed_db) -> None:  # type: ignore[no-untyped-def]
    factory, (region_id, track_id, _position_id, _person_id) = routed_db
    with factory() as db:
        admin_user = db.scalar(select(User).where(User.email == "admin@example.test"))
        db.add(
            ExternalTrackMapping(
                provider="LOVE_RACING",
                external_track_key="referenced-track",
                external_track_name="Referenced Track",
                track_id=track_id,
                confirmed_by_user_id=admin_user.id,
            )
        )
        historical_workday = Workday(
            region_id=region_id,
            category="RACE_DAY",
            created_by_user_id=admin_user.id,
        )
        db.add(historical_workday)
        db.flush()
        revision = WorkdayRevision(
            workday_id=historical_workday.id,
            revision_number=1,
            state="PUBLISHED",
            work_date=date(2026, 10, 1),
            track_id=track_id,
            track_name_snapshot="Ellerslie historical snapshot",
            title="Historical day",
            created_by_user_id=admin_user.id,
            published_by_user_id=admin_user.id,
        )
        db.add(revision)
        db.flush()
        historical_workday.current_published_revision_id = revision.id
        spare_region = Region(name="Setup Mistake")
        db.add(spare_region)
        db.flush()
        spare_track = Track(name="Temporary Track", region_id=spare_region.id, palette_slot=1)
        db.add(spare_track)
        db.commit()
        spare_region_id, spare_track_id = spare_region.id, spare_track.id

    admin = TestClient(app)
    csrf = _login(admin, "admin@example.test", "99887766")
    admin_page = admin.get("/admin")
    assert admin_page.status_code == 200
    assert 'href="/manage/catalog"' in admin_page.text
    assert f'/manage/catalog/regions/{spare_region_id}' not in admin_page.text
    catalog_page = admin.get("/manage/catalog")
    assert f'/manage/catalog/regions/{spare_region_id}' in catalog_page.text
    assert f'/manage/catalog/tracks/{spare_track_id}' in catalog_page.text

    archived_track = admin.post(
        f"/manage/catalog/tracks/{spare_track_id}/lifecycle",
        data={"lifecycle": "ARCHIVED", "csrf_token": csrf},
        follow_redirects=False,
    )
    assert archived_track.status_code == 303
    archived_region = admin.post(
        f"/manage/catalog/regions/{spare_region_id}/lifecycle",
        data={"lifecycle": "ARCHIVED", "csrf_token": csrf},
        follow_redirects=False,
    )
    assert archived_region.status_code == 303
    new_workday = admin.get("/manage/workdays/new")
    assert "Temporary Track" not in new_workday.text
    assert "Setup Mistake" not in new_workday.text
    online_sources = admin.get("/admin/online-sources")
    assert f'value="{spare_track_id}"' not in online_sources.text
    assert f'value="{spare_region_id}"' not in online_sources.text
    catalog = admin.get("/manage/catalog")
    assert "Archived Regions" in catalog.text and "Archived Tracks" in catalog.text

    assert admin.post(
        f"/manage/catalog/regions/{spare_region_id}/lifecycle",
        data={"lifecycle": "ACTIVE", "csrf_token": csrf},
        follow_redirects=False,
    ).status_code == 303
    assert admin.post(
        f"/manage/catalog/tracks/{spare_track_id}/lifecycle",
        data={"lifecycle": "ACTIVE", "csrf_token": csrf},
        follow_redirects=False,
    ).status_code == 303

    assert admin.post(
        f"/manage/catalog/tracks/{spare_track_id}/remove-unused",
        data={"confirm_remove": "yes", "csrf_token": csrf},
        follow_redirects=False,
    ).status_code == 303
    assert admin.post(
        f"/manage/catalog/regions/{spare_region_id}/remove-unused",
        data={"confirm_remove": "yes", "csrf_token": csrf},
        follow_redirects=False,
    ).status_code == 303
    with factory() as db:
        assert db.get(Track, spare_track_id) is None
        assert db.get(Region, spare_region_id) is None

    with factory() as db:
        snapshot_names = list(
            db.scalars(
                select(WorkdayRevision.track_name_snapshot).where(
                    WorkdayRevision.track_id == track_id
                )
            )
        )
    assert snapshot_names
    assert admin.post(
        f"/manage/catalog/tracks/{track_id}/lifecycle",
        data={"lifecycle": "ARCHIVED", "csrf_token": csrf},
        follow_redirects=False,
    ).status_code == 303
    assert f'value="{track_id}"' not in admin.get("/manage/workdays/new").text
    with factory() as db:
        assert list(
            db.scalars(
                select(WorkdayRevision.track_name_snapshot).where(
                    WorkdayRevision.track_id == track_id
                )
            )
        ) == snapshot_names
    assert admin.post(
        f"/manage/catalog/tracks/{track_id}/lifecycle",
        data={"lifecycle": "ACTIVE", "csrf_token": csrf},
        follow_redirects=False,
    ).status_code == 303

    referenced_track = admin.post(
        f"/manage/catalog/tracks/{track_id}/remove-unused",
        data={"confirm_remove": "yes", "csrf_token": csrf},
        follow_redirects=False,
    )
    assert referenced_track.status_code == 409
    assert "Archive it instead" in referenced_track.text
    referenced_region = admin.post(
        f"/manage/catalog/regions/{region_id}/remove-unused",
        data={"confirm_remove": "yes", "csrf_token": csrf},
        follow_redirects=False,
    )
    assert referenced_region.status_code == 409
    assert "Archive it instead" in referenced_region.text


@pytest.mark.parametrize(
    ("context_key", "heading"),
    [
        ("month", "Your Month"),
        ("builder", "Build a roster"),
        ("settings", "Settings"),
        ("admin", "Administration"),
        ("catalog", "Master data"),
        ("online-sources", "Online Sources"),
        ("data-import", "Data Import"),
        ("management", "Management"),
        ("crew_management", "Manage Crew"),
        ("unknown", "Help"),
    ],
)
def test_contextual_help_content_and_authorized_links(routed_db, context_key, heading) -> None:  # type: ignore[no-untyped-def]
    admin = TestClient(app)
    _login(admin, "admin@example.test", "99887766")
    response = admin.get(f"/help?context_key={context_key}")
    assert response.status_code == 200 and heading in response.text

    employee = TestClient(app)
    _login(employee, "amy@example.test", "654321")
    help_home = employee.get("/help").text
    assert "Check your published Month and Day roster" in help_home
    assert "Apply for a suitable Open Position" in help_home
    assert "Build rosters and manage" not in help_home
    assert "Administration for global setup" not in help_home


def test_help_home_content_is_role_aware_and_uses_real_symbols(routed_db) -> None:  # type: ignore[no-untyped-def]
    factory, (region_id, _track_id, _position_id, _person_id) = routed_db
    with factory() as db:
        contractor = User(
            email="help-contractor@example.test",
            display_name="Help Contractor",
            credential_hash=hash_credential("246810"),
            credential_kind="pin",
            contractor_manual_extension_at=utcnow(),
            contractor_access_expires_at=utcnow() + timedelta(days=30),
        )
        contractor_person = Person(
            display_name="Help Contractor",
            email=contractor.email,
            home_region_id=region_id,
        )
        db.add_all([contractor, contractor_person])
        db.flush()
        db.add_all(
            [
                UserPersonLink(user_id=contractor.id, person_id=contractor_person.id),
                RoleGrant(
                    user_id=contractor.id,
                    role=Role.CONTRACTOR.value,
                    region_id=region_id,
                ),
            ]
        )
        db.commit()

    expectations = (
        (
            "help-contractor@example.test",
            "246810",
            (),
            ("Open Position", "regional Crew calendar", "Build rosters", "Administration"),
        ),
        (
            "viewer@example.test",
            "112233",
            ("View the regional Crew calendar",),
            ("Build rosters", "Administration", "Open Position"),
        ),
        (
            "submanager@example.test",
            "445566",
            ("Build rosters and manage",),
            ("Administration for global setup",),
        ),
        (
            "manager@example.test",
            "123456",
            ("Build rosters and manage",),
            ("Administration for global setup",),
        ),
        (
            "admin@example.test",
            "99887766",
            ("Administration for global setup, online sources and imports",),
            (),
        ),
    )
    for email, credential, present, absent in expectations:
        client = TestClient(app)
        _login(client, email, credential)
        page = client.get("/help").text
        assert all(text in page for text in present)
        assert all(text not in page for text in absent)
        assert "Related workspaces" not in page
        assert 'class="help-legend"' in page
        assert "<svg" in page
        assert 'class="track-dot help-external-dot"' in page


def test_rollout_management_surfaces_are_compact_and_user_facing(routed_db) -> None:  # type: ignore[no-untyped-def]
    _factory, (region_id, _track_id, _position_id, person_id) = routed_db
    manager = TestClient(app)
    _login(manager, "manager@example.test", "123456")

    management = manager.get("/manage").text
    assert "Operational crew notices" in management
    assert '<details open>' in management
    assert "More tools" not in management
    assert management.index("Build") < management.index("Crew") < management.index("Team hours")

    detail = manager.get(f"/manage/crew/{person_id}").text
    assert detail.count('data-capability-input') == 1
    assert 'name="position_id" data-capability-value' in detail
    assert "Exact Position" not in detail
    assert "Position convergence" not in detail

    builder = manager.get(f"/manage/workdays/new?region_id={region_id}").text
    assert re.search(r'name="truck_leaves_early"[^>]*checked', builder)
    assert "Borrowed / other Region" in builder

    hours = manager.get("/manage/hours").text
    assert 'class="hours-filter-bar"' in hours
    assert 'name="q"' in hours and 'name="group_id"' in hours
    assert "No People match these filters" in manager.get(
        "/manage/hours?q=no-such-person-on-track"
    ).text

    help_page = manager.get("/help").text
    assert "What the symbols mean" in help_page
    assert "Add to Home Screen" in help_page
    assert "Report an error" in help_page
    assert "app-footer" not in help_page

    admin = TestClient(app)
    _login(admin, "admin@example.test", "99887766")
    admin_page = admin.get("/admin").text
    assert "Application status" in admin_page
    assert "Build" in admin_page and "Scheduler" in admin_page


def test_upcoming_feed_is_today_when_rostered_plus_three_across_months(
    routed_db, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    factory, (region_id, _track_id, position_id, person_id) = routed_db
    dates = [date(2026, 9, 30), date(2026, 10, 2), date(2026, 10, 5), date(2026, 10, 11), date(2026, 10, 20)]
    for work_date in dates:
        _publish_rows(
            factory,
            region_id=region_id,
            position_id=position_id,
            work_date=work_date,
            rows=[(person_id, "Amy Crew", "", True)],
        )
    from app.employee import routes as employee_routes

    monkeypatch.setattr(employee_routes, "local_today", lambda: date(2026, 9, 30))
    client = TestClient(app)
    _login(client, "amy@example.test", "654321")
    today_payload = client.get("/api/upcoming-work").json()
    assert [row["date"] for row in today_payload["days"]] == [
        value.isoformat() for value in dates[:4]
    ]

    monkeypatch.setattr(employee_routes, "local_today", lambda: date(2026, 10, 1))
    future_payload = client.get("/api/upcoming-work").json()
    assert [row["date"] for row in future_payload["days"]] == [
        value.isoformat() for value in dates[1:4]
    ]


def test_regional_directory_catalog_authority_theme_and_upcoming_cross_month(routed_db) -> None:  # type: ignore[no-untyped-def]
    factory, (region_id, _track_id, position_id, person_id) = routed_db
    manager_client = TestClient(app)
    manager_csrf = _login(manager_client, "manager@example.test", "123456")
    accounts = manager_client.get("/manage/accounts")
    assert accounts.status_code == 200
    assert "private-south@example.test" not in accounts.text
    catalog = manager_client.get("/manage/catalog")
    assert catalog.status_code == 200 and "Ellerslie" in catalog.text
    created_track = manager_client.post(
        "/manage/catalog/tracks",
        data={
            "region_id": str(region_id),
            "name": "Pukekohe",

            "csrf_token": manager_csrf,
        },
        follow_redirects=False,
    )
    assert created_track.status_code == 303

    submanager_client = TestClient(app)
    _login(submanager_client, "submanager@example.test", "445566")
    assert submanager_client.get("/manage/workdays/new").status_code == 200
    assert submanager_client.get("/manage/catalog").status_code == 403
    assert submanager_client.get("/manage/accounts").status_code == 403

    viewer_client = TestClient(app)
    _login(viewer_client, "viewer@example.test", "112233")
    assert viewer_client.get("/manage/catalog").status_code == 403
    assert viewer_client.get("/manage/accounts").status_code == 403

    admin_client = TestClient(app)
    admin_csrf = _login(admin_client, "admin@example.test", "99887766")
    with factory() as db:
        group_id = db.scalar(select(CrewGroup.id).where(CrewGroup.name == "OB Crew"))
    region_update = admin_client.post(
        f"/manage/catalog/regions/{region_id}",
        data={
            "name": "Northern Operations",
            "lifecycle": "ACTIVE",
            "decline_policy": "MANAGER_REVIEW",
            "lead_minutes_race_day": "90",
            "statutory_holiday_region": "Auckland",
            "csrf_token": admin_csrf,
        },
        follow_redirects=False,
    )
    assert region_update.status_code == 303
    position_update = admin_client.post(
        f"/manage/catalog/positions/{position_id}",
        data={
            "name": "CCU Operator",
            "crew_group_id": str(group_id),
            "lifecycle": "ACTIVE",
            "csrf_token": admin_csrf,
        },
        follow_redirects=False,
    )
    assert position_update.status_code == 303

    employee_client = TestClient(app)
    employee_csrf = _login(employee_client, "amy@example.test", "654321")
    changed_theme = employee_client.post(
        "/settings/theme",
        data={"theme": "moss", "csrf_token": employee_csrf},
        follow_redirects=False,
    )
    assert changed_theme.status_code == 303
    assert '<html lang="en" data-theme="moss">' in employee_client.get("/settings").text

    today = local_today()
    boundary = today.replace(day=monthrange(today.year, today.month)[1])
    if boundary < today:
        boundary = today
    dates = [boundary + timedelta(days=offset) for offset in range(5)]
    with factory() as db:
        user_id = db.scalar(select(User.id).where(User.email == "manager@example.test"))
        other_region_id = db.scalar(select(Region.id).where(Region.name == "Southern"))
        for index, work_date in enumerate(dates, start=1):
            workday = Workday(
                region_id=other_region_id if index == 1 else region_id,
                created_by_user_id=user_id,
            )
            db.add(workday)
            db.flush()
            revision = WorkdayRevision(
                workday_id=workday.id,
                revision_number=1,
                state="PUBLISHED",
                work_date=work_date,
                title=f"Boundary {index}",
                track_name_snapshot="South Track" if index == 1 else "North Track",

                start_time=time(8),
                end_time=time(17),
                published_at=utcnow(),
                created_by_user_id=user_id,
            )
            db.add(revision)
            db.flush()
            db.add(
                Assignment(
                    revision_id=revision.id,
                    base_position_id=position_id,
                    display_name_snapshot="CCU",
                    person_id=person_id,
                    person_name_snapshot="Amy Crew",
                    status="ASSIGNED",
                )
            )
            workday.current_published_revision_id = revision.id
        db.commit()
    upcoming = employee_client.get("/api/upcoming-work")
    assert upcoming.status_code == 200
    payload = upcoming.json()
    expected_dates = dates[:4] if dates[0] == today else dates[:3]
    assert len(payload["days"]) == len(expected_dates)
    assert [row["date"] for row in payload["days"]] == [
        value.isoformat() for value in expected_dates
    ]
    assert dates[0].month != dates[1].month
    assert "cached_at" not in payload
    cross_region_month = employee_client.get(
        f"/month?year={dates[0].year}&month={dates[0].month}"
    )
    assert "cross-region" in cross_region_month.text
    assert 'data-presentation="unconfirmed"' in cross_region_month.text
    assert "Your rostered week" not in cross_region_month.text
    assert 'class="weekday weekday-total">Week</div>' in cross_region_month.text
    visible_week_days = min(len(dates), 7 - dates[0].weekday())
    assert f"{visible_week_days * 9}h 0m" in cross_region_month.text
    january = employee_client.get("/month?year=2026&month=1")
    assert 'title="Auckland Anniversary Day"' in january.text


def test_month_contract_and_all_redeputy_themes(routed_db) -> None:  # type: ignore[no-untyped-def]
    factory, _ = routed_db
    client = TestClient(app)
    csrf = _login(client, "amy@example.test", "654321")
    with factory() as db:
        user = db.scalar(select(User).where(User.email == "amy@example.test"))
        user.theme = "trackside"
        new_user = User(
            email="new-theme-default@example.test",
            display_name="New theme default",
            credential_hash="not-a-real-login-hash",
        )
        db.add(new_user)
        db.commit()
        assert new_user.theme == "aurora"
    month = client.get("/month?year=2026&month=9&view=month")
    assert month.status_code == 200
    html = month.text
    assert '<html lang="en" data-theme="jade">' in html
    assert '<header class="site-header has-month-nav authenticated">' in html
    assert html.index('aria-label="Month navigation"') < html.index('class="calendar-grid"')
    assert html.count('class="weekday"') == 7
    assert 'class="weekday weekday-total">Week</div>' in html
    assert html.count('class="week-total"') >= 4
    assert html.index('class="upcoming-strip"') > html.index('class="calendar-grid"')
    assert "month-head" not in html
    assert "Your authoritative roster" not in html
    assert ">Today<" not in html
    assert 'data-roster-nav' in html

    roster_list = client.get("/month?year=2026&month=9&view=list")
    assert roster_list.status_code == 200
    assert 'class="roster-list"' in roster_list.text
    assert 'aria-label="Month view"' in roster_list.text
    assert 'class="calendar-grid"' not in roster_list.text
    assert 'href="/month?year=2026&amp;month=8&amp;view=list"' in roster_list.text
    assert 'href="/month?year=2026&amp;month=10&amp;view=list"' in roster_list.text
    next_list = client.get("/month?year=2026&month=10&view=list")
    assert next_list.status_code == 200
    assert 'class="roster-list"' in next_list.text
    assert 'href="/month?year=2026&amp;month=9&amp;view=list"' in next_list.text

    assert len(THEME_VALUES) == 20
    settings = client.get("/settings")
    assert settings.status_code == 200
    assert "Jade dark" in settings.text
    assert 'name="theme" value="jade" checked' in settings.text
    theme_css = Path("app/static/redeputy.css").read_text(encoding="utf-8")
    for theme in THEME_VALUES:
        assert f'name="theme" value="{theme}"' in settings.text
        if theme != "jade":
            assert f'data-theme="{theme}"' in theme_css
        changed = client.post(
            "/settings/theme",
            data={"theme": theme, "csrf_token": csrf},
            follow_redirects=False,
        )
        assert changed.status_code == 303
    assert theme_css.startswith(":root {") and "--accent: #33c4a5;" in theme_css
    legacy = client.post(
        "/settings/theme",
        data={"theme": "trackside", "csrf_token": csrf},
        follow_redirects=False,
    )
    assert legacy.status_code == 303
    with factory() as db:
        assert db.scalar(select(User.theme).where(User.email == "amy@example.test")) == "jade"


def test_passkey_options_and_totp_login_flow(routed_db) -> None:  # type: ignore[no-untyped-def]
    factory, _ = routed_db
    employee_client = TestClient(app)
    csrf = _login(employee_client, "amy@example.test", "654321")
    options = employee_client.post(
        "/settings/passkeys/options", data={"csrf_token": csrf}
    )
    assert options.status_code == 200
    assert options.json()["challenge_id"]
    assert options.json()["rp"]["id"] == "localhost"

    with factory() as db:
        manager = db.scalar(select(User).where(User.email == "manager@example.test"))
        factor, secret, _ = begin_totp(db, manager)
        factor.confirmed_at = utcnow()
        db.commit()

    manager_client = TestClient(app)
    first = manager_client.post(
        "/login",
        data={"email": "manager@example.test", "credential": "123456", "next": "/month"},
        follow_redirects=False,
    )
    assert first.status_code == 303 and first.headers["location"].startswith("/login/totp?")
    query = parse_qs(urlsplit(first.headers["location"]).query)
    challenge_id = query["challenge_id"][0]
    assert manager_client.get(first.headers["location"]).status_code == 200
    completed = manager_client.post(
        "/login/totp",
        data={
            "challenge_id": challenge_id,
            "code": pyotp.TOTP(secret).now(),
            "next": "/month",
        },
        follow_redirects=False,
    )
    assert completed.status_code == 303
    assert manager_client.cookies.get("ontrack_session")


def test_totp_confirmation_shows_recovery_codes_once_and_lifecycle_is_fresh_auth(
    routed_db,
) -> None:  # type: ignore[no-untyped-def]
    factory, _ = routed_db
    client = TestClient(app)
    csrf = _login(client, "admin@example.test", "99887766")
    begun = client.post("/settings/totp/begin", data={"csrf_token": csrf})
    assert begun.status_code == 200
    secret_match = re.search(r"Manual key: <code>([A-Z2-7]+)</code>", begun.text)
    assert secret_match
    confirmed = client.post(
        "/settings/totp/confirm",
        data={"csrf_token": csrf, "code": pyotp.TOTP(secret_match.group(1)).now()},
    )
    assert confirmed.status_code == 200
    assert confirmed.headers["cache-control"] == "no-store"
    codes = re.findall(r"<code>([0-9A-F-]{19})</code>", confirmed.text)
    assert len(codes) == 10
    with factory() as db:
        admin = db.scalar(select(User).where(User.email == "admin@example.test"))
        original_hashes = set(
            db.scalars(select(RecoveryCode.code_hash).where(RecoveryCode.user_id == admin.id))
        )
        assert len(original_hashes) == 10
        assert not any(code in original_hashes for code in codes)

    settings_page = client.get("/settings")
    assert all(code not in settings_page.text for code in codes)
    assert "10 remaining" in settings_page.text
    regenerated = client.post(
        "/settings/totp/recovery-codes", data={"csrf_token": csrf}
    )
    assert regenerated.status_code == 200
    replacement_codes = re.findall(r"<code>([0-9A-F-]{19})</code>", regenerated.text)
    assert len(replacement_codes) == 10
    with factory() as db:
        admin = db.scalar(select(User).where(User.email == "admin@example.test"))
        replacement_hashes = set(
            db.scalars(select(RecoveryCode.code_hash).where(RecoveryCode.user_id == admin.id))
        )
        assert len(replacement_hashes) == 10
        assert replacement_hashes.isdisjoint(original_hashes)

    removed = client.post(
        "/settings/totp/recovery-codes/remove",
        data={"csrf_token": csrf},
        follow_redirects=False,
    )
    assert removed.status_code == 303
    with factory() as db:
        admin = db.scalar(select(User).where(User.email == "admin@example.test"))
        assert not list(
            db.scalars(select(RecoveryCode).where(RecoveryCode.user_id == admin.id))
        )
    regenerated = client.post(
        "/settings/totp/recovery-codes", data={"csrf_token": csrf}
    )
    assert regenerated.status_code == 200
    disabled = client.post(
        "/settings/totp/disable",
        data={"csrf_token": csrf},
        follow_redirects=False,
    )
    assert disabled.status_code == 303
    with factory() as db:
        admin = db.scalar(select(User).where(User.email == "admin@example.test"))
        assert not list(
            db.scalars(select(RecoveryCode).where(RecoveryCode.user_id == admin.id))
        )


def test_global_product_branding_is_persistent_admin_only_and_escaped(routed_db) -> None:  # type: ignore[no-untyped-def]
    factory, _ = routed_db
    default_page = TestClient(app).get("/login")
    assert '<strong>On Track</strong>' in default_page.text
    assert "<title>Sign in · On Track</title>" in default_page.text

    admin = TestClient(app)
    admin_csrf = _login(admin, "admin@example.test", "99887766")
    changed = admin.post(
        "/admin/branding",
        data={"product_name": 'Track & "Crew"', "csrf_token": admin_csrf},
        follow_redirects=False,
    )
    assert changed.status_code == 303

    admin_page = admin.get("/admin")
    assert '<strong>Track &amp; &#34;Crew&#34;</strong>' in admin_page.text
    assert "<title>Administration · Track &amp; &#34;Crew&#34;</title>" in admin_page.text
    assert '<strong>On Track</strong>' not in admin_page.text

    reloaded_login = TestClient(app).get("/login")
    assert '<strong>Track &amp; &#34;Crew&#34;</strong>' in reloaded_login.text
    assert "<title>Sign in · Track &amp; &#34;Crew&#34;</title>" in reloaded_login.text
    assert '<strong>On Track</strong>' not in reloaded_login.text

    manifest = TestClient(app).get("/manifest.webmanifest")
    assert manifest.status_code == 200
    assert manifest.headers["content-type"].startswith("application/manifest+json")
    assert manifest.headers["cache-control"] == "no-store"
    assert manifest.json()["name"] == 'Track & "Crew"'
    assert manifest.json()["short_name"] == 'Track & "Crew"'
    assert manifest.json()["background_color"] == "#0b1020"
    assert manifest.json()["theme_color"] == "#0b1020"

    employee = TestClient(app)
    employee_csrf = _login(employee, "amy@example.test", "654321")
    assert employee.get("/api/upcoming-work").json()["product_name"] == 'Track & "Crew"'
    options = employee.post(
        "/settings/passkeys/options", data={"csrf_token": employee_csrf}
    ).json()
    assert options["rp"]["name"] == 'Track & "Crew"'

    with factory() as db:
        stored = db.get(SystemBranding, 1)
        assert stored and stored.product_name == 'Track & "Crew"'
        assert stored.updated_by_user_id is not None

    rejected = admin.post(
        "/admin/branding",
        data={"product_name": "<script>alert(1)</script>", "csrf_token": admin_csrf},
    )
    assert rejected.status_code == 400

    for email, credential in (
        ("manager@example.test", "123456"),
        ("submanager@example.test", "445566"),
        ("viewer@example.test", "112233"),
        ("amy@example.test", "654321"),
        ("private-south@example.test", "778899"),
    ):
        client = TestClient(app)
        csrf = _login(client, email, credential)
        response = client.post(
            "/admin/branding",
            data={"product_name": "Forbidden rename", "csrf_token": csrf},
            follow_redirects=False,
        )
        assert response.status_code == 403


def test_configurable_favicon_is_bounded_persistent_audited_and_public(
    routed_db, monkeypatch, tmp_path: Path
) -> None:  # type: ignore[no-untyped-def]
    factory, _ = routed_db
    import app.branding.service as branding_service

    monkeypatch.setattr(
        branding_service,
        "get_settings",
        lambda: SimpleNamespace(data_dir=tmp_path),
    )
    favicon_path = tmp_path / "branding" / "favicon.png"

    anonymous = TestClient(app)
    default_icon = anonymous.get("/branding/favicon")
    assert default_icon.status_code == 200
    assert default_icon.headers["content-type"].startswith("image/svg+xml")
    assert default_icon.headers["cache-control"] == "no-cache, max-age=0, must-revalidate"
    assert default_icon.headers["x-content-type-options"] == "nosniff"
    assert b"<svg" in default_icon.content
    assert anonymous.get(
        "/branding/favicon", headers={"If-None-Match": default_icon.headers["etag"]}
    ).status_code == 304
    login_page = anonymous.get("/login")
    assert '<link rel="icon" href="/branding/favicon">' in login_page.text
    default_manifest = anonymous.get("/manifest.webmanifest")
    assert default_manifest.headers["cache-control"] == "no-store"
    assert default_manifest.json()["icons"] == [
        {
            "src": "/branding/favicon",
            "sizes": "any",
            "type": "image/svg+xml",
            "purpose": "any",
        }
    ]

    admin = TestClient(app)
    csrf = _login(admin, "admin@example.test", "99887766")
    assert "Built-in default" in admin.get("/admin#branding").text
    assert admin.post(
        "/admin/branding/favicon",
        data={"csrf_token": "wrong"},
        files={"image": ("icon.png", _png_bytes(64, 64), "image/png")},
    ).status_code == 403

    validation_cases = (
        (("icon.jpg", b"not an image", "image/jpeg"), "PNG image"),
        (("fake.png", b"not a png", "image/png"), "PNG image"),
        (
            ("large.png", b"x" * (MAX_FAVICON_BYTES + 1), "image/png"),
            "2 MB or smaller",
        ),
        (("wide.png", _png_bytes(64, 32), "image/png"), "must be square"),
        (("small.png", _png_bytes(16, 16), "image/png"), "at least 32×32"),
        (("huge.png", _png_bytes(1025, 1025), "image/png"), "no larger than 1024×1024"),
        (("corrupt.png", b"\x89PNG\r\n\x1a\nIHDR", "image/png"), "uncorrupted PNG"),
        (("empty.png", b"", "image/png"), "Choose a PNG image"),
    )
    for upload, message in validation_cases:
        rejected = admin.post(
            "/admin/branding/favicon",
            data={"csrf_token": csrf},
            files={"image": upload},
        )
        assert rejected.status_code == 400
        assert message in rejected.json()["detail"]
        assert not favicon_path.exists()

    manager = TestClient(app)
    manager_csrf = _login(manager, "manager@example.test", "123456")
    assert manager.post(
        "/admin/branding/favicon",
        data={"csrf_token": manager_csrf},
        files={"image": ("manager.png", _png_bytes(64, 64), "image/png")},
    ).status_code == 403
    assert manager.post(
        "/admin/branding/favicon/reset",
        data={"csrf_token": manager_csrf},
    ).status_code == 403

    first_png = _png_bytes(64, 64)
    uploaded = admin.post(
        "/admin/branding/favicon",
        data={"csrf_token": csrf},
        files={"image": ("../../outside.png", first_png, "text/plain")},
        follow_redirects=False,
    )
    assert uploaded.status_code == 303
    assert uploaded.headers["location"] == "/admin#branding"
    assert favicon_path.read_bytes() == first_png
    assert list(favicon_path.parent.glob(".favicon-*.tmp")) == []
    assert not (tmp_path / "outside.png").exists()

    custom_icon = anonymous.get("/branding/favicon")
    assert custom_icon.status_code == 200
    assert custom_icon.headers["content-type"].startswith("image/png")
    assert custom_icon.content == first_png
    custom_manifest = anonymous.get("/manifest.webmanifest").json()
    assert custom_manifest["name"] == "On Track"
    assert custom_manifest["icons"] == [
        {
            "src": "/branding/favicon",
            "sizes": "64x64",
            "type": "image/png",
            "purpose": "any",
        }
    ]
    branding_page = admin.get("/admin#branding")
    assert "Custom PNG · 64×64" in branding_page.text
    assert 'action="/admin/branding/favicon/reset"' in branding_page.text

    second_png = _png_bytes(128, 128, red=85)
    replaced = admin.post(
        "/admin/branding/favicon",
        data={"csrf_token": csrf},
        files={"image": ("replacement.png", second_png, "image/png")},
        follow_redirects=False,
    )
    assert replaced.status_code == 303
    assert favicon_path.read_bytes() == second_png
    assert list(favicon_path.parent.iterdir()) == [favicon_path]

    assert admin.post(
        "/admin/branding/favicon/reset",
        data={"csrf_token": "wrong"},
    ).status_code == 403
    reset = admin.post(
        "/admin/branding/favicon/reset",
        data={"csrf_token": csrf},
        follow_redirects=False,
    )
    assert reset.status_code == 303
    assert not favicon_path.exists()
    assert admin.get("/branding/favicon").headers["content-type"].startswith("image/svg+xml")
    repeated = admin.post(
        "/admin/branding/favicon/reset",
        data={"csrf_token": csrf},
        follow_redirects=False,
    )
    assert repeated.status_code == 303
    assert not favicon_path.exists()

    with factory() as db:
        uploads = list(
            db.scalars(
                select(AuditEvent).where(
                    AuditEvent.action == "system_branding.favicon_uploaded"
                )
            )
        )
        resets = list(
            db.scalars(
                select(AuditEvent).where(AuditEvent.action == "system_branding.favicon_reset")
            )
        )
        assert len(uploads) == 2
        assert uploads[-1].detail == {
            "width": 128,
            "height": 128,
            "bytes": len(second_png),
            "sha256": hashlib.sha256(second_png).hexdigest(),
        }
        assert [event.detail["had_custom_icon"] for event in resets] == [True, False]


def test_management_workspaces_consolidate_controls_and_primary_region(routed_db) -> None:  # type: ignore[no-untyped-def]
    factory, (region_id, _track_id, position_id, person_id) = routed_db
    admin = TestClient(app)
    admin_csrf = _login(admin, "admin@example.test", "99887766")

    admin_html = admin.get("/admin").text
    assert 'href="/manage/catalog"' in admin_html
    assert 'href="/manage/accounts"' in admin_html
    assert 'action="/admin/users"' not in admin_html
    assert 'action="/manage/catalog/positions"' not in admin_html

    accounts_html = admin.get("/manage/accounts").text
    assert 'action="/admin/users"' in accounts_html
    assert 'action="/admin/invitations"' in accounts_html
    catalog_html = admin.get("/manage/catalog").text
    assert 'action="/manage/catalog/positions"' in catalog_html
    created = admin.post(
        "/manage/catalog/positions",
        data={"name": "Vision Operator", "crew_group_id": "", "csrf_token": admin_csrf},
        follow_redirects=False,
    )
    assert created.status_code == 303
    duplicate = admin.post(
        "/manage/catalog/positions",
        data={"name": "Vision Operator", "crew_group_id": "", "csrf_token": admin_csrf},
    )
    assert duplicate.status_code == 409
    alias_created = admin.post(
        "/manage/catalog/positions",
        data={"name": "Dir", "crew_group_id": "", "csrf_token": admin_csrf},
        follow_redirects=False,
    )
    assert alias_created.status_code == 303
    alias_conflict = admin.post(
        "/manage/catalog/positions",
        data={"name": "Director", "crew_group_id": "", "csrf_token": admin_csrf},
    )
    assert alias_conflict.status_code == 409
    numbered_ccu_conflict = admin.post(
        "/manage/catalog/positions",
        data={"name": "CCU1", "crew_group_id": "", "csrf_token": admin_csrf},
    )
    assert numbered_ccu_conflict.status_code == 409

    manager = TestClient(app)
    manager_csrf = _login(manager, "manager@example.test", "123456")
    manager_accounts = manager.get("/manage/accounts").text
    assert 'action="/admin/users"' not in manager_accounts
    assert 'action="/admin/invitations"' not in manager_accounts
    manager_settings = manager.get("/settings").text
    assert 'href="/crew"' not in manager_settings
    assert 'href="/manage"' in manager_settings
    assert 'href="/manage/crew"' not in manager_settings
    assert 'href="/manage/catalog"' not in manager_settings
    management_hub = manager.get("/manage").text
    assert 'href="/manage/crew"' in management_hub
    assert 'href="/manage/catalog"' in management_hub
    crew_html = manager.get(f"/manage/crew?region_id={region_id}").text
    assert "Staff directory" in crew_html
    assert f'href="/manage/crew/{person_id}"' in crew_html
    assert "capability-table" not in crew_html
    filtered_crew = manager.get(
        f"/manage/crew?region_id={region_id}&q=Amy&account_type=&crew_group_id=&position_id="
    )
    assert filtered_crew.status_code == 200
    assert f'href="/manage/crew/{person_id}"' in filtered_crew.text
    person_detail = manager.get(f"/manage/crew/{person_id}")
    assert person_detail.status_code == 200
    assert f"Person {person_id}" in person_detail.text
    assert "Account " in person_detail.text
    assert "data-person-editor" in person_detail.text
    assert "Reset PIN/password" in person_detail.text
    with factory() as db:
        southern_id = db.scalar(select(Region.id).where(Region.id != region_id))
        grant_count = len(list(db.scalars(select(RoleGrant).where(RoleGrant.user_id.is_not(None)))))
    denied = manager.post(
        f"/manage/crew/{person_id}/profile",
        data={
            "display_name": "Amy Crew",
            "email": "",
            "home_region_id": str(southern_id),
            "csrf_token": manager_csrf,
        },
    )
    assert denied.status_code == 403
    created_person = admin.post(
        "/manage/crew",
        data={
            "display_name": "Southern New Crew",
            "email": "",
            "region_id": str(southern_id),
            "csrf_token": admin_csrf,
        },
        follow_redirects=False,
    )
    assert created_person.status_code == 303
    moved = admin.post(
        f"/manage/crew/{person_id}/profile",
        data={
            "display_name": "Amy Crew",
            "email": "amy@example.test",
            "home_region_id": str(southern_id),
            "csrf_token": admin_csrf,
        },
        follow_redirects=False,
    )
    assert moved.status_code == 303
    with factory() as db:
        assert db.get(Person, person_id).home_region_id == southern_id
        assert db.scalar(
            select(Person.id).where(
                Person.display_name == "Southern New Crew", Person.home_region_id == southern_id
            )
        )
        assert len(list(db.scalars(select(RoleGrant).where(RoleGrant.user_id.is_not(None))))) == grant_count
        manager_user = db.scalar(select(User).where(User.email == "manager@example.test"))
        db.add(RoleGrant(user_id=manager_user.id, role=Role.MANAGER.value, region_id=southern_id))
        db.commit()
    allowed = manager.post(
        f"/manage/crew/{person_id}/profile",
        data={
            "display_name": "Amy Crew",
            "email": "amy@example.test",
            "home_region_id": str(region_id),
            "csrf_token": manager_csrf,
        },
        follow_redirects=False,
    )
    assert allowed.status_code == 303
    capability = manager.post(
        f"/manage/crew/{person_id}/capabilities/{position_id}",
        data={"signal": "MANAGER_ALLOW", "csrf_token": manager_csrf},
        follow_redirects=False,
    )
    assert capability.status_code == 303
    with factory() as db:
        assert db.scalar(
            select(PositionCapability.id).where(
                PositionCapability.person_id == person_id,
                PositionCapability.base_position_id == position_id,
                PositionCapability.signal == "MANAGER_ALLOW",
            )
        )


def test_personal_settings_error_report_is_safe_and_admin_reviewable(routed_db) -> None:  # type: ignore[no-untyped-def]
    factory, _ids = routed_db
    employee = TestClient(app)
    csrf = _login(employee, "amy@example.test", "654321")
    settings = employee.get("/settings")
    assert "Workspaces" not in settings.text
    assert "Operational crew notices" not in settings.text
    submitted = employee.post(
        "/settings/error-reports",
        data={
            "description": "The call time looks wrong.",
            "page_path": "/month?token=must-not-persist",
            "csrf_token": csrf,
        },
        follow_redirects=False,
    )
    assert submitted.status_code == 303
    with factory() as db:
        report = db.scalar(select(ErrorReport))
        assert report and report.page_path == "/month"
        assert report.reporting_person_id is not None
        assert "token" not in report.description and "must-not-persist" not in report.page_path
        report_id = report.id
    assert employee.get("/admin/error-reports").status_code == 403
    admin = TestClient(app)
    admin_csrf = _login(admin, "admin@example.test", "99887766")
    page = admin.get("/admin/error-reports")
    assert "The call time looks wrong." in page.text
    reviewed = admin.post(
        f"/admin/error-reports/{report_id}/review",
        data={"csrf_token": admin_csrf},
        follow_redirects=False,
    )
    assert reviewed.status_code == 303
    with factory() as db:
        assert db.get(ErrorReport, report_id).status == "REVIEWED"


def test_notice_scope_defaults_reject_forgery_and_multi_region_push_dedupes(routed_db) -> None:  # type: ignore[no-untyped-def]
    factory, (region_id, _track_id, _position_id, _person_id) = routed_db
    with factory() as db:
        other_region_id = db.scalar(select(Region.id).where(Region.id != region_id))
    manager = TestClient(app)
    csrf = _login(manager, "manager@example.test", "123456")
    denied = manager.post(
        "/manage/notices",
        data={
            "message": "Forged Southern notice",
            "duration_hours": "12",
            "region_ids": str(other_region_id),
            "csrf_token": csrf,
        },
    )
    assert denied.status_code == 403
    created = manager.post(
        "/manage/notices",
        data={
            "message": "Northern operational notice",
            "duration_hours": "12",
            "region_ids": str(region_id),
            "also_push": "on",
            "csrf_token": csrf,
        },
        follow_redirects=False,
    )
    assert created.status_code == 303
    with factory() as db:
        notice = db.scalar(select(OperationalNotice))
        assert notice
        assert set(
            db.scalars(
                select(OperationalNoticeRegion.region_id).where(
                    OperationalNoticeRegion.notice_id == notice.id
                )
            )
        ) == {region_id}
        event_row = db.get(NotificationEvent, f"operational-notice:{notice.id}")
        assert event_row and event_row.payload["region_ids"] == [str(region_id)]

    admin = TestClient(app)
    admin_csrf = _login(admin, "admin@example.test", "99887766")
    management_page = admin.get("/manage")
    assert management_page.status_code == 200
    assert len(re.findall(r'name="region_ids"[^>]*checked', management_page.text)) == 1
    with factory() as db:
        employee = db.scalar(select(User).where(User.email == "amy@example.test"))
        db.add(
            RoleGrant(
                user_id=employee.id,
                role=Role.EMPLOYEE.value,
                region_id=other_region_id,
            )
        )
        db.commit()
        employee_id = employee.id
    multi = admin.post(
        "/manage/notices",
        data={
            "message": "Both Regions operational notice",
            "duration_hours": "12",
            "region_ids": [str(region_id), str(other_region_id)],
            "also_push": "on",
            "csrf_token": admin_csrf,
        },
        follow_redirects=False,
    )
    assert multi.status_code == 303
    with factory() as db:
        notice = db.scalar(
            select(OperationalNotice).where(
                OperationalNotice.message == "Both Regions operational notice"
            )
        )
        event_row = db.get(NotificationEvent, f"operational-notice:{notice.id}")
        assert event_row
        assert set(event_row.payload["region_ids"]) == {
            str(region_id),
            str(other_region_id),
        }
        audience = audience_user_ids(db, event_row)
        assert employee_id in audience
        assert list(audience).count(employee_id) == 1


def test_account_linking_requires_matching_email_and_flags_legacy_mismatch(routed_db) -> None:  # type: ignore[no-untyped-def]
    factory, (region_id, _track_id, _position_id, _person_id) = routed_db
    with factory() as db:
        person = Person(
            display_name="Link Mismatch Person",
            email="person-link@example.com",
            home_region_id=region_id,
        )
        direct_person = Person(
            display_name="Direct Mismatch Person",
            email="direct-person@example.com",
            home_region_id=region_id,
        )
        legacy_person = Person(
            display_name="Legacy Mismatch Person",
            email="legacy-person@example.com",
            home_region_id=region_id,
        )
        link_user = User(
            email="account-link@example.com",
            display_name="Link Mismatch Account",
            credential_hash=hash_credential("246810"),
            credential_kind="pin",
        )
        legacy_user = User(
            email="legacy-login@example.com",
            display_name="Legacy Mismatch Account",
            credential_hash=hash_credential("246810"),
            credential_kind="pin",
        )
        db.add_all([person, direct_person, legacy_person, link_user, legacy_user])
        db.flush()
        db.add_all(
            [
                RoleGrant(
                    user_id=link_user.id,
                    role=Role.EMPLOYEE.value,
                    region_id=region_id,
                ),
                UserPersonLink(user_id=legacy_user.id, person_id=legacy_person.id),
                RoleGrant(
                    user_id=legacy_user.id,
                    role=Role.EMPLOYEE.value,
                    region_id=region_id,
                ),
            ]
        )
        db.commit()
        person_id = person.id
        direct_person_id = direct_person.id
        legacy_person_id = legacy_person.id
        link_user_id = link_user.id

    admin = TestClient(app)
    csrf = _login(admin, "admin@example.test", "99887766")
    linked = admin.post(
        "/manage/accounts/link-person",
        data={
            "user_id": str(link_user_id),
            "person_id": str(person_id),
            "region_id": str(region_id),
            "csrf_token": csrf,
        },
        follow_redirects=False,
    )
    assert linked.status_code == 409
    direct = admin.post(
        "/admin/users",
        data={
            "display_name": "Direct Mismatch Account",
            "email": "direct-login@example.com",
            "credential": "246810",
            "role": Role.EMPLOYEE.value,
            "region_id": str(region_id),
            "person_id": str(direct_person_id),
            "csrf_token": csrf,
        },
        follow_redirects=False,
    )
    assert direct.status_code == 422
    assert "Account not created" in direct.text
    assert 'value="Direct Mismatch Account"' in direct.text
    assert 'value="direct-login@example.com"' in direct.text
    assert 'name="credential" required' in direct.text
    assert 'value="246810"' not in direct.text
    directory = admin.get("/manage/crew?q=Legacy+Mismatch")
    assert directory.status_code == 200
    assert "Work/login email mismatch" in directory.text
    detail = admin.get(f"/manage/crew/{legacy_person_id}")
    assert detail.status_code == 200
    assert "Work/login email mismatch" in detail.text


def test_direct_account_creation_returns_to_meaningful_context(routed_db) -> None:  # type: ignore[no-untyped-def]
    factory, (region_id, _track_id, _position_id, _person_id) = routed_db
    with factory() as db:
        person = Person(
            display_name="Direct Linked Person",
            email="direct-linked@example.com",
            home_region_id=region_id,
        )
        db.add(person)
        db.commit()
        person_id = person.id

    admin = TestClient(app)
    csrf = _login(admin, "admin@example.test", "99887766")
    unlinked = admin.post(
        "/admin/users",
        data={
            "display_name": "Recovery Only Account",
            "email": "recovery-only@example.com",
            "credential": "246810",
            "role": Role.EMPLOYEE.value,
            "region_id": str(region_id),
            "person_id": "",
            "csrf_token": csrf,
        },
        follow_redirects=False,
    )
    assert unlinked.status_code == 303
    assert unlinked.headers["location"].startswith("/manage/accounts?created_user=")
    result = admin.get(unlinked.headers["location"])
    assert "Account created for Recovery Only Account" in result.text
    assert "No rosterable Person was created" in result.text

    linked = admin.post(
        "/admin/users",
        data={
            "display_name": "Direct Linked Person",
            "email": "direct-linked@example.com",
            "credential": "246810",
            "role": Role.EMPLOYEE.value,
            "region_id": str(region_id),
            "person_id": str(person_id),
            "csrf_token": csrf,
        },
        follow_redirects=False,
    )
    assert linked.status_code == 303
    assert linked.headers["location"] == f"/manage/crew/{person_id}?account=created#account"
    detail = admin.get(linked.headers["location"])
    assert "Account created and linked to Direct Linked Person" in detail.text
    with factory() as db:
        account_id = db.scalar(
            select(UserPersonLink.user_id).where(UserPersonLink.person_id == person_id)
        )
        assert account_id
        assert db.scalar(
            select(RoleGrant.id).where(
                RoleGrant.user_id == account_id,
                RoleGrant.role == Role.EMPLOYEE.value,
                RoleGrant.region_id == region_id,
            )
        )


def test_phase_b_crew_workspace_is_person_first_and_accounts_stays_advanced(routed_db) -> None:  # type: ignore[no-untyped-def]
    factory, (region_id, _track_id, _position_id, person_id) = routed_db
    admin = TestClient(app)
    csrf = _login(admin, "admin@example.test", "99887766")

    directory = admin.get(f"/manage/crew?region_id={region_id}")
    assert directory.status_code == 200
    assert 'href="/manage/crew/onboarding"' in directory.text
    assert "Search first to avoid duplicate People" in directory.text

    detail = admin.get(f"/manage/crew/{person_id}")
    assert detail.status_code == 200
    for section in ("Profile", "Roster &amp; capabilities", "Account &amp; access", "Security &amp; history"):
        assert section in detail.text
    assert 'aria-label="Person sections"' in detail.text

    advanced = admin.get("/manage/accounts")
    assert advanced.status_code == 200
    assert "<h1>Advanced Accounts</h1>" in advanced.text
    assert 'href="/manage/crew/onboarding"' in advanced.text
    assert 'action="/manage/accounts/people-invitations"' not in advanced.text
    assert "Invite a Contractor Person" not in advanced.text
    assert 'action="/admin/users"' in advanced.text

    with factory() as db:
        existing = Person(
            display_name="Existing Duplicate Guard",
            email="existing-duplicate@example.com",
            home_region_id=region_id,
        )
        db.add(existing)
        db.commit()
        before = db.scalar(select(func.count()).select_from(Person))
    duplicate = admin.post(
        "/manage/crew",
        data={
            "display_name": "Duplicate Person",
            "email": "existing-duplicate@example.com",
            "region_id": str(region_id),
            "csrf_token": csrf,
        },
    )
    assert duplicate.status_code == 422, duplicate.text
    assert "Find and manage them in Crew" in duplicate.text
    assert 'value="Duplicate Person"' in duplicate.text
    assert 'value="existing-duplicate@example.com"' in duplicate.text
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(Person)) == before


def test_submanager_crew_surface_matches_existing_assistance_authority(routed_db) -> None:  # type: ignore[no-untyped-def]
    factory, (region_id, _track_id, position_id, person_id) = routed_db
    submanager = TestClient(app)
    csrf = _login(submanager, "submanager@example.test", "445566")

    directory = submanager.get(f"/manage/crew?region_id={region_id}")
    assert directory.status_code == 200
    assert "Add Person" not in directory.text
    assert "Last seen" not in directory.text
    assert "Account type" not in directory.text

    detail = submanager.get(f"/manage/crew/{person_id}")
    assert detail.status_code == 200
    assert "Account assistance" in detail.text
    assert "Reset PIN/password" in detail.text
    assert "Add capability decision" not in detail.text
    assert "Create setup link" not in detail.text
    assert "trusted devices" not in detail.text.casefold()
    assert "Recent history" not in detail.text
    assert "Make Sub-manager" not in detail.text
    assert f'/capabilities/{position_id}' not in detail.text

    denied_profile = submanager.post(
        f"/manage/crew/{person_id}/profile",
        data={
            "display_name": "Unauthorized edit",
            "email": "amy@example.test",
            "home_region_id": str(region_id),
            "csrf_token": csrf,
        },
        follow_redirects=False,
    )
    assert denied_profile.status_code == 403
    assert submanager.post(
        f"/manage/crew/{person_id}/setup-link",
        data={"role": "EMPLOYEE", "csrf_token": csrf},
        follow_redirects=False,
    ).status_code == 403
    assert submanager.post(
        f"/manage/crew/{person_id}/devices/revoke",
        data={"csrf_token": csrf},
        follow_redirects=False,
    ).status_code == 403

    assisted = submanager.post(
        f"/manage/crew/{person_id}/account-status",
        data={"account_status": "DISABLED", "csrf_token": csrf},
        follow_redirects=False,
    )
    assert assisted.status_code == 303
    with factory() as db:
        account = db.scalar(
            select(User).join(UserPersonLink).where(UserPersonLink.person_id == person_id)
        )
        assert account.status == "DISABLED"
        outside_person_id = db.scalar(
            select(Person.id).where(Person.email == "private-south@example.test")
        )
    manager = TestClient(app)
    _login(manager, "manager@example.test", "123456")
    assert manager.get(f"/manage/crew/{outside_person_id}").status_code == 403
    admin = TestClient(app)
    _login(admin, "admin@example.test", "99887766")
    assert admin.get(f"/manage/crew/{outside_person_id}").status_code == 200


def test_person_work_email_integrity_is_global_link_aware_and_non_destructive(routed_db) -> None:  # type: ignore[no-untyped-def]
    factory, (region_id, _track_id, _position_id, person_id) = routed_db
    with factory() as db:
        other_region = db.scalar(select(Region).where(Region.id != region_id))
        collision = Person(
            display_name="Cross Region Collision",
            email="collision@example.com",
            home_region_id=other_region.id,
        )
        unlinked = User(
            email="reconcile@example.com",
            display_name="Unlinked reconciliation account",
            credential_hash=hash_credential("123456"),
            credential_kind="pin",
        )
        aligned_user = User(
            email="aligned@example.com",
            display_name="Aligned account",
            credential_hash=hash_credential("123456"),
            credential_kind="pin",
        )
        aligned_person = Person(
            display_name="Aligned Person",
            email="aligned@example.com",
            home_region_id=region_id,
        )
        db.add_all([collision, unlinked, aligned_user, aligned_person])
        db.flush()
        db.add(UserPersonLink(user_id=aligned_user.id, person_id=aligned_person.id))
        db.commit()
        unlinked_id = unlinked.id
        aligned_person_id = aligned_person.id

    admin = TestClient(app)
    csrf = _login(admin, "admin@example.test", "99887766")
    aligned = admin.post(
        f"/manage/crew/{aligned_person_id}",
        data={
            "display_name": "Aligned Case Normalized",
            "email": "ALIGNED@example.com",
            "home_region_id": str(region_id),
            "csrf_token": csrf,
        },
        follow_redirects=False,
    )
    assert aligned.status_code == 303
    linked_mismatch = admin.post(
        f"/manage/crew/{person_id}",
        data={
            "display_name": "Amy Renamed Safely",
            "email": "different@example.com",
            "home_region_id": str(region_id),
            "csrf_token": csrf,
        },
    )
    assert linked_mismatch.status_code == 422
    assert "Profile not saved" in linked_mismatch.text
    assert "must match this Person" in linked_mismatch.text
    assert 'value="different@example.com"' in linked_mismatch.text

    cross_region = admin.post(
        "/manage/crew",
        data={
            "display_name": "Duplicate Elsewhere",
            "email": "COLLISION@example.com",
            "region_id": str(region_id),
            "csrf_token": csrf,
        },
    )
    assert cross_region.status_code == 422
    assert "Another active Person already uses that work email" in cross_region.text

    reconciliation_person = admin.post(
        "/manage/crew",
        data={
            "display_name": "Reconciliation Person",
            "email": "RECONCILE@example.com",
            "region_id": str(region_id),
            "csrf_token": csrf,
        },
        follow_redirects=False,
    )
    assert reconciliation_person.status_code == 303
    with factory() as db:
        created = db.scalar(select(Person).where(Person.email == "reconcile@example.com"))
        assert created is not None
        assert db.get(UserPersonLink, unlinked_id) is None
        original = db.get(Person, person_id)
        assert original.display_name == "Amy Crew"
        assert original.email == "amy@example.test"
        aligned_person = db.get(Person, aligned_person_id)
        assert aligned_person.display_name == "Aligned Case Normalized"
        assert aligned_person.email == "aligned@example.com"

        legacy_duplicate = Person(
            display_name="Legacy Duplicate",
            email="COLLISION@example.com",
            home_region_id=region_id,
        )
        db.add(legacy_duplicate)
        db.commit()
        legacy_duplicate_id = legacy_duplicate.id
    diagnostic = admin.get(f"/manage/crew/{legacy_duplicate_id}")
    assert diagnostic.status_code == 200
    assert "Identity review needed" in diagnostic.text
    assert "Another active Person uses this work email" in diagnostic.text
    with factory() as db:
        assert db.scalar(
            select(func.count()).select_from(Person).where(
                func.lower(Person.email) == "collision@example.com"
            )
        ) == 2


def test_admin_onboards_regionless_person_then_invites_exact_identity(routed_db) -> None:  # type: ignore[no-untyped-def]
    factory, (region_id, _track_id, _position_id, _person_id) = routed_db
    with factory() as db:
        regionless = Person(
            display_name="Imported Regionless Crew",
            email="regionless@example.com",
            home_region_id=None,
        )
        db.add(regionless)
        db.commit()
        person_id = regionless.id
        person_count = db.scalar(select(func.count()).select_from(Person))

    manager = TestClient(app)
    manager_csrf = _login(manager, "manager@example.test", "123456")
    manager_page = manager.get("/manage/crew")
    assert "Imported Regionless Crew" not in manager_page.text
    denied = manager.post(
        f"/manage/crew/{person_id}/profile",
        data={
            "display_name": "Imported Regionless Crew",
            "email": "regionless@example.com",
            "home_region_id": str(region_id),
            "csrf_token": manager_csrf,
        },
    )
    assert denied.status_code == 404

    admin = TestClient(app)
    admin_csrf = _login(admin, "admin@example.test", "99887766")
    queue = admin.get("/manage/crew?q=Regionless")
    assert queue.status_code == 200
    assert "Needs setup" in queue.text
    assert "Imported Regionless Crew" in queue.text
    assigned = admin.post(
        f"/manage/crew/{person_id}/profile",
        data={
            "display_name": "Imported Regionless Crew",
            "email": "regionless@example.com",
            "home_region_id": str(region_id),
            "csrf_token": admin_csrf,
        },
        follow_redirects=False,
    )
    assert assigned.status_code == 303
    regional_page = admin.get(f"/manage/crew?region_id={region_id}&q=Regionless")
    assert "Imported Regionless Crew" in regional_page.text
    detail_page = admin.get(f"/manage/crew/{person_id}")
    assert "Ready for setup" in detail_page.text
    assert "Create setup link" in detail_page.text
    assert '<option value="CASUAL">Casual</option>' in detail_page.text
    assert "Create a direct account instead" in detail_page.text
    assert f'name="person_id" value="{person_id}"' in detail_page.text
    failed_direct = admin.post(
        "/admin/users",
        data={
            "display_name": "Imported Regionless Crew",
            "email": "regionless@example.com",
            "credential": "123",
            "role": "CASUAL",
            "region_id": str(region_id),
            "person_id": str(person_id),
            "return_person_id": str(person_id),
            "csrf_token": admin_csrf,
        },
    )
    assert failed_direct.status_code == 422
    assert "Imported Regionless Crew" in failed_direct.text
    assert "Account not created" in failed_direct.text
    assert '<option value="CASUAL" selected>Casual</option>' in failed_direct.text
    assert 'value="123"' not in failed_direct.text
    advanced_accounts = admin.get("/manage/accounts")
    assert "Advanced Accounts" in advanced_accounts.text
    assert 'action="/manage/accounts/people-invitations"' not in advanced_accounts.text
    onboarding = admin.get("/manage/crew/onboarding?onboarding_q=Regionless")
    assert onboarding.status_code == 200
    assert "Imported Regionless Crew" in onboarding.text

    invited = admin.post(
        f"/manage/crew/{person_id}/setup-link",
        data={
            "role": "EMPLOYEE",
            "csrf_token": admin_csrf,
        },
    )
    assert invited.status_code == 200 and "#token=" in invited.text
    pending = admin.get(f"/manage/crew/{person_id}")
    assert "Invitation pending" in pending.text
    assert "Revoke setup link" in pending.text
    revoked = admin.post(
        f"/manage/crew/{person_id}/setup-link/revoke",
        data={"csrf_token": admin_csrf},
        follow_redirects=False,
    )
    assert revoked.status_code == 303
    reissued = admin.post(
        f"/manage/crew/{person_id}/setup-link",
        data={"role": "EMPLOYEE", "csrf_token": admin_csrf},
    )
    assert reissued.status_code == 200 and "#token=" in reissued.text
    with factory() as db:
        invitation = db.scalar(
            select(Invitation)
            .where(
                Invitation.email == "regionless@example.com",
                Invitation.revoked_at.is_(None),
            )
            .order_by(Invitation.created_at.desc())
        )
        assert invitation.person_id == person_id
        assert invitation.role == "EMPLOYEE"
        assert db.scalar(select(func.count()).select_from(Person)) == person_count
        person = db.get(Person, person_id)
        assert person.home_region_id == region_id
        assert db.scalar(
            select(AuditEvent.id).where(
                AuditEvent.action == "person.profile.updated",
                AuditEvent.target_id == str(person_id),
            )
        )


def test_admin_batch_onboarding_metadata_is_exact_and_atomic(routed_db) -> None:  # type: ignore[no-untyped-def]
    factory, (region_id, _track_id, _position_id, _person_id) = routed_db
    with factory() as db:
        first = Person(display_name="Batch First", home_region_id=None)
        second = Person(display_name="Batch Second", home_region_id=None)
        db.add_all([first, second])
        db.commit()
        first_id, second_id = first.id, second.id
        initial_user_count = db.scalar(select(func.count()).select_from(User))

    admin = TestClient(app)
    csrf = _login(admin, "admin@example.test", "99887766")
    queue = admin.get("/manage/crew")
    assert queue.status_code == 200
    assert "Edit only the People you are ready to update." in queue.text
    assert "Work email and Primary Region may stay blank until account setup." in queue.text
    assert 'name="emails" type="email"' in queue.text
    assert 'name="emails" type="email" value="" required' not in queue.text
    assert "Person UUID is the exact machine key" not in queue.text
    assert f"Person {first_id}" not in queue.text
    assert f"Person {second_id}" not in queue.text
    assert queue.text.count('class="onboarding-card onboarding-setup-card"') >= 2
    assert "Needs email" in queue.text and "Needs Region" in queue.text
    assert 'class="save-bar onboarding-save-bar"' in queue.text
    assert "Save setup details" in queue.text
    invalid = admin.post(
        "/manage/crew/onboarding/metadata",
        data={
            "person_ids": [str(first_id), str(second_id)],
            "emails": ["same@example.com", "same@example.com"],
            "region_ids": [str(region_id), str(region_id)],
            "csrf_token": csrf,
        },
    )
    assert invalid.status_code == 409
    with factory() as db:
        assert db.get(Person, first_id).email is None
        assert db.get(Person, second_id).home_region_id is None
        assert db.scalar(select(func.count()).select_from(User)) == initial_user_count

    invalid_format = admin.post(
        "/manage/crew/onboarding/metadata",
        data={
            "person_ids": [str(first_id), str(second_id)],
            "emails": ["not-an-email", "valid@example.com"],
            "region_ids": [str(region_id), str(region_id)],
            "csrf_token": csrf,
        },
    )
    assert invalid_format.status_code == 400
    with factory() as db:
        assert db.get(Person, first_id).home_region_id is None
        assert db.get(Person, second_id).email is None

    saved = admin.post(
        "/manage/crew/onboarding/metadata",
        data={
            "person_ids": [str(second_id)],
            "emails": ["batch.second@example.com"],
            "region_ids": [str(region_id)],
            "csrf_token": csrf,
        },
        follow_redirects=False,
    )
    assert saved.status_code == 303
    with factory() as db:
        assert db.get(Person, first_id).email is None
        assert db.get(Person, first_id).home_region_id is None
        assert db.get(Person, second_id).email == "batch.second@example.com"
        assert db.get(Person, second_id).home_region_id == region_id
        assert not db.scalar(
            select(UserPersonLink.user_id).where(
                UserPersonLink.person_id.in_([first_id, second_id])
            )
        )
    queue_after = admin.get(f"/manage/crew?region_id={region_id}")
    assert "Batch First" in queue_after.text
    assert "Needs email" in queue_after.text
    no_email_setup = admin.post(
        f"/manage/crew/{first_id}/setup-link",
        data={"role": "EMPLOYEE", "csrf_token": csrf},
    )
    assert no_email_setup.status_code == 409


def test_position_convergence_is_explicit_admin_only_and_audited(routed_db) -> None:  # type: ignore[no-untyped-def]
    factory, (_region_id, _track_id, _position_id, _person_id) = routed_db
    with factory() as db:
        camera = CrewGroup(name="Camera Crew Convergence")
        imported = CrewGroup(name="Operational Crew")
        db.add_all([camera, imported])
        db.flush()
        canonical = BasePosition(name="Head On", crew_group_id=camera.id, display_order=2)
        duplicate = BasePosition(name="Head On", crew_group_id=imported.id)
        db.add_all([canonical, duplicate])
        db.commit()
        duplicate_id = duplicate.id

    manager = TestClient(app)
    manager_csrf = _login(manager, "manager@example.test", "123456")
    assert manager.get("/admin/data-import").status_code == 403
    assert manager.post(
        "/admin/data-import/position-convergence",
        data={"csrf_token": manager_csrf},
    ).status_code == 403

    admin = TestClient(app)
    admin_csrf = _login(admin, "admin@example.test", "99887766")
    preview = admin.get("/admin/data-import")
    assert preview.status_code == 200
    assert "Canonical Position convergence" in preview.text
    assert "Archive Head On" in preview.text
    applied = admin.post(
        "/admin/data-import/position-convergence",
        data={"csrf_token": admin_csrf},
        follow_redirects=False,
    )
    assert applied.status_code == 303
    with factory() as db:
        assert db.get(BasePosition, duplicate_id).lifecycle == "ARCHIVED"
        assert db.scalar(
            select(AuditEvent.id).where(AuditEvent.action == "base_positions.converged")
        )


def test_calendar_empty_cells_open_prefilled_builder_only_for_managers(routed_db) -> None:  # type: ignore[no-untyped-def]
    _factory, (region_id, _track_id, _position_id, _person_id) = routed_db
    manager = TestClient(app)
    _login(manager, "manager@example.test", "123456")
    builder = manager.get(f"/manage/workdays/new?date=2030-02-12&region_id={region_id}")
    assert builder.status_code == 200
    assert 'name="work_date" value="2030-02-12"' in builder.text
    assert f'<option value="{region_id}" selected' in builder.text
    personal_month = manager.get("/month?year=2030&month=2")
    assert 'data-empty-build-url="/manage/workdays/new?date=2030-02-12"' in personal_month.text
    crew_month = manager.get(f"/crew?region_id={region_id}&year=2030&month=2")
    assert (
        f'data-empty-build-url="/manage/workdays/new?date=2030-02-12&amp;region_id={region_id}"'
        in crew_month.text
    )

    employee = TestClient(app)
    _login(employee, "amy@example.test", "654321")
    employee_month = employee.get("/month?year=2030&month=2")
    assert "data-empty-build-url" not in employee_month.text


def test_delete_never_published_workday_cleans_generated_travel_but_keeps_source(routed_db) -> None:  # type: ignore[no-untyped-def]
    factory, (region_id, track_id, _position_id, _person_id) = routed_db
    with factory() as db:
        manager = db.scalar(select(User).where(User.email == "manager@example.test"))
        event_row = ExternalCalendarEvent(
            event_date=date(2030, 3, 5),
            track_id=track_id,
            discipline="THOROUGHBRED",
            event_kind="RACE",
        )
        operation = Operation(name="Delete me", region_id=region_id)
        db.add_all([event_row, operation])
        db.flush()
        parent = Workday(
            region_id=region_id,
            operation_id=operation.id,
            external_event_id=event_row.id,
            category="RACE_DAY",
            created_by_user_id=manager.id,
        )
        db.add(parent)
        db.flush()
        parent_revision = WorkdayRevision(
            workday_id=parent.id,
            revision_number=1,
            state="DRAFT",
            work_date=date(2030, 3, 5),
            created_by_user_id=manager.id,
        )
        child = Workday(
            region_id=region_id,
            operation_id=operation.id,
            generated_from_workday_id=parent.id,
            category="TRAVEL",
            created_by_user_id=manager.id,
        )
        db.add_all([parent_revision, child])
        db.flush()
        child_revision = WorkdayRevision(
            workday_id=child.id,
            revision_number=1,
            state="DRAFT",
            work_date=date(2030, 3, 4),
            created_by_user_id=manager.id,
        )
        db.add(child_revision)
        db.flush()
        parent.current_draft_revision_id = parent_revision.id
        child.current_draft_revision_id = child_revision.id
        db.commit()
        parent_id, child_id, event_id, operation_id = parent.id, child.id, event_row.id, operation.id

    manager_client = TestClient(app)
    csrf = _login(manager_client, "manager@example.test", "123456")
    deleted = manager_client.post(
        f"/manage/workdays/{parent_id}/delete",
        data={"confirm_delete": "yes", "csrf_token": csrf},
        follow_redirects=False,
    )
    assert deleted.status_code == 303, deleted.text
    assert f"/manage/workdays/{parent_id}" not in manager_client.get(
        "/month?year=2030&month=3"
    ).text
    with factory() as db:
        assert db.get(Workday, parent_id) is None
        assert db.get(Workday, child_id) is None
        assert db.get(Operation, operation_id) is None
        assert db.get(ExternalCalendarEvent, event_id) is not None
        audit = db.scalar(
            select(AuditEvent).where(AuditEvent.action == "workday.private_draft.deleted")
        )
        assert audit is not None

        manager = db.scalar(select(User).where(User.email == "manager@example.test"))
        published = Workday(
            region_id=region_id,
            category="RACE_DAY",
            created_by_user_id=manager.id,
        )
        db.add(published)
        db.flush()
        historical = WorkdayRevision(
            workday_id=published.id,
            revision_number=1,
            state="PUBLISHED",
            work_date=date(2030, 3, 6),
            created_by_user_id=manager.id,
            published_by_user_id=manager.id,
            published_at=utcnow(),
        )
        db.add(historical)
        db.commit()
        published_id = published.id
        other_region_id = db.scalar(select(Region.id).where(Region.id != region_id))
        outside = Workday(
            region_id=other_region_id,
            category="OFFICE_DAY",
            created_by_user_id=manager.id,
        )
        db.add(outside)
        db.flush()
        outside_revision = WorkdayRevision(
            workday_id=outside.id,
            revision_number=1,
            state="DRAFT",
            work_date=date(2030, 3, 7),
            created_by_user_id=manager.id,
        )
        db.add(outside_revision)
        db.flush()
        outside.current_draft_revision_id = outside_revision.id
        db.commit()
        outside_id = outside.id
    rejected = manager_client.post(
        f"/manage/workdays/{published_id}/delete",
        data={"confirm_delete": "yes", "csrf_token": csrf},
    )
    assert rejected.status_code == 409
    with factory() as db:
        assert db.get(Workday, published_id) is not None
    unauthorized = manager_client.post(
        f"/manage/workdays/{outside_id}/delete",
        data={"confirm_delete": "yes", "csrf_token": csrf},
    )
    assert unauthorized.status_code == 403
    with factory() as db:
        assert db.get(Workday, outside_id) is not None


def test_vehicle_management_and_audit_are_region_scoped(routed_db) -> None:  # type: ignore[no-untyped-def]
    factory, (region_id, _track_id, _position_id, _person_id) = routed_db
    manager = TestClient(app)
    csrf = _login(manager, "manager@example.test", "123456")
    created = manager.post(
        "/manage/catalog/vehicles",
        data={"csrf_token": csrf, "name": "Rental Van - October",
              "home_region_id": str(region_id), "category": "Rental",
              "model": "Transit", "fuel_type": "Diesel"},
        follow_redirects=False,
    )
    assert created.status_code == 303
    with factory() as db:
        vehicle = db.scalar(select(Vehicle).where(Vehicle.name == "Rental Van - October"))
        assert vehicle and vehicle.home_region_id == region_id
        vehicle_id = vehicle.id
        other_region = db.scalar(select(Region).where(Region.id != region_id))
        borrowed = Vehicle(
            name="Southern Borrowed Van",
            home_region_id=other_region.id,
            category="Operational",
        )
        db.add(borrowed)
        db.flush()
        borrowed_id = borrowed.id
        manager_user = db.scalar(select(User).where(User.email == "manager@example.test"))
        record_audit(db, "regional.visible", "vehicle", vehicle.id, manager_user.id,
                     region_id=region_id,
                     detail={"credential_token": "never-show", "name": vehicle.name})
        record_audit(db, "regional.hidden", "vehicle", vehicle.id, manager_user.id,
                     region_id=other_region.id)
        db.commit()
        other_region_id = other_region.id
    builder = manager.get(f"/manage/workdays/new?region_id={region_id}")
    assert "Southern Borrowed Van" in builder.text
    assert "Borrowed / other Region" in builder.text
    forged_borrowed_edit = manager.post(
        f"/manage/catalog/vehicles/{borrowed_id}",
        data={"csrf_token": csrf, "name": "Taken over",
              "home_region_id": str(region_id), "category": "Operational",
              "model": "", "fuel_type": "", "lifecycle": "ACTIVE"},
    )
    assert forged_borrowed_edit.status_code == 403
    with factory() as db:
        borrowed = db.get(Vehicle, borrowed_id)
        assert borrowed.name == "Southern Borrowed Van"
        assert borrowed.home_region_id == other_region_id
    archived = manager.post(
        f"/manage/catalog/vehicles/{vehicle_id}",
        data={"csrf_token": csrf, "name": "Rental Van - October",
              "home_region_id": str(region_id), "category": "Rental",
              "model": "Transit", "fuel_type": "Diesel", "lifecycle": "ARCHIVED"},
        follow_redirects=False,
    )
    assert archived.status_code == 303
    with factory() as db:
        assert db.get(Vehicle, vehicle_id).lifecycle == "ARCHIVED"
    rejected = manager.post(
        "/manage/catalog/vehicles",
        data={"csrf_token": csrf, "name": "Unauthorized Van",
              "home_region_id": str(other_region_id), "category": "Rental"},
    )
    assert rejected.status_code == 403
    audit = manager.get("/manage/audit")
    assert audit.status_code == 200
    assert "regional.visible" in audit.text and "regional.hidden" not in audit.text
    assert "never-show" not in audit.text and "[REDACTED]" in audit.text
    submanager = TestClient(app)
    _login(submanager, "submanager@example.test", "445566")
    assert submanager.get("/manage/audit").status_code == 403
    admin = TestClient(app)
    _login(admin, "admin@example.test", "99887766")
    global_audit = admin.get("/manage/audit")
    assert "regional.visible" in global_audit.text and "regional.hidden" in global_audit.text


def test_position_display_order_controls_builder_picker(routed_db) -> None:  # type: ignore[no-untyped-def]
    factory, (region_id, _track_id, position_id, _person_id) = routed_db
    with factory() as db:
        group = db.scalar(select(CrewGroup))
        db.get(BasePosition, position_id).display_order = 90
        db.add(BasePosition(name="Custom First", crew_group_id=group.id, display_order=1))
        db.commit()
    manager = TestClient(app)
    _login(manager, "manager@example.test", "123456")
    page = manager.get(f"/manage/workdays/new?region_id={region_id}")
    assert page.status_code == 200
    assert page.text.index('data-label="Custom First"') < page.text.index('data-label="CCU"')
