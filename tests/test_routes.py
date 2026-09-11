import re
import uuid
import warnings
from calendar import monthrange
from datetime import date, time, timedelta
from urllib.parse import parse_qs, urlsplit

import pyotp
import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

with warnings.catch_warnings():
    warnings.simplefilter("ignore", DeprecationWarning)
    from fastapi.testclient import TestClient

from app.audit.models import AuditEvent, HumanChange
from app.auth.factors import begin_totp
from app.auth.security import hash_credential, token_hash
from app.branding.models import SystemBranding
from app.catalog.models import BasePosition, CrewGroup, Region, Track
from app.core.database import Base
from app.core.enums import Role
from app.core.time import local_today, utcnow
from app.identity.models import (
    Invitation,
    LoginThrottle,
    Person,
    RoleGrant,
    SignupRequest,
    User,
    UserPersonLink,
)
from app.main import app
from app.rostering.models import Assignment, Workday, WorkdayRevision
from app.system_settings.models import SystemSettings


def test_public_login_and_liveness_routes_render(routed_db) -> None:  # type: ignore[no-untyped-def]
    client = TestClient(app)
    login = client.get("/login")
    assert login.status_code == 200
    assert "Good to see you" in login.text
    assert "default-src 'self'" in login.headers["content-security-policy"]
    assert client.get("/health/live").json() == {"status": "ok"}


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
    match = re.search(r'value="/invite#token=([A-Za-z0-9_-]+)"', created.text)
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
    assert repeated_create.status_code == 400
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
        data={"public_signup_enabled": "true", "csrf_token": manager_csrf},
    ).status_code == 403

    admin = TestClient(app)
    admin_csrf = _login(admin, "admin@example.test", "99887766")
    toggled = admin.post(
        "/admin/system-settings",
        data={"public_signup_enabled": "true", "csrf_token": admin_csrf},
        follow_redirects=False,
    )
    assert toggled.status_code == 303
    assert "Public account requests are currently closed" not in public.get("/signup").text
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
    assert re.search(r'value="/invite#token=[A-Za-z0-9_-]+"', approved.text)


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
        track = Track(name="Ellerslie", region_id=region.id, display_colour="#C33D52")
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
            track_colour_snapshot="#C33D52",
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


def test_manager_publish_employee_visibility_and_route_authorization(routed_db) -> None:  # type: ignore[no-untyped-def]
    factory, (region_id, track_id, position_id, person_id) = routed_db
    manager_client = TestClient(app)
    csrf = _login(manager_client, "manager@example.test", "123456")
    created = manager_client.post(
        "/manage/workdays",
        data={
            "region_id": str(region_id),
            "category": "RACE_DAY",
            "work_date": "2026-09-14",
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
    month = employee_client.get("/month?year=2026&month=9")
    assert month.status_code == 200
    assert "Ellerslie" in month.text and "CCU 2" in month.text
    day = employee_client.get(f"/day/{workday_id}")
    assert "Private transport" in day.text
    assert "'unsafe-inline'" not in day.headers["content-security-policy"]
    assert 'style nonce="' in day.text and 'data-track-colour="#C33D52"' in day.text
    day_payload = employee_client.get(f"/api/day/{workday_id}").json()
    assert "cached_at" not in day_payload
    assert day_payload["workday"]["revision_id"]
    assert day_payload["workday"]["revision_number"] == 1
    crew = employee_client.get(f"/crew?region_id={region_id}&year=2026&month=9")
    assert crew.status_code == 200 and "Ellerslie" in crew.text
    assert employee_client.get("/manage/workdays/new").status_code == 403
    assert manager_client.get("/admin").status_code == 403
    viewer_client = TestClient(app)
    _login(viewer_client, "viewer@example.test", "112233")
    viewer_day = viewer_client.get(f"/day/{workday_id}")
    assert "Private transport" in viewer_day.text
    assert "Edit private draft" not in viewer_day.text
    assert viewer_client.get(workday_path).status_code == 403

    # A later draft cannot leak into the employee read path.
    assert manager_client.get(workday_path).status_code == 200
    with factory() as db:
        workday = db.get(Workday, workday_id)
        draft = db.get(WorkdayRevision, workday.current_draft_revision_id)
        draft.track_name_snapshot = "DRAFT LEAK SENTINEL"
        db.commit()
    assert "DRAFT LEAK SENTINEL" not in employee_client.get("/month?year=2026&month=9").text
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
    assert "Office Day details" in builder.text
    assert "First race" not in builder.text and "Race count" not in builder.text
    preview = manager.get(edit_url + "/preview")
    assert "Race Day timing is incomplete" not in preview.text


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
            "display_colour": "#123ABC",
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
                track_colour_snapshot="#00AA11" if index == 1 else "#123ABC",
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
    assert 'data-track-colour="#00AA11"' in cross_region_month.text
    assert "Your rostered week: 45h 0m" in cross_region_month.text
    january = employee_client.get("/month?year=2026&month=1")
    assert 'title="Auckland Anniversary Day"' in january.text


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
