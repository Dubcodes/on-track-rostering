import uuid
import warnings
from urllib.parse import parse_qs, urlsplit

import pyotp
import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

with warnings.catch_warnings():
    warnings.simplefilter("ignore", DeprecationWarning)
    from fastapi.testclient import TestClient

from app.auth.factors import begin_totp
from app.auth.security import hash_credential
from app.catalog.models import BasePosition, CrewGroup, Region, Track
from app.core.database import Base
from app.core.enums import Role
from app.core.time import utcnow
from app.identity.models import Person, RoleGrant, User, UserPersonLink
from app.main import app
from app.rostering.models import Assignment, Workday, WorkdayRevision


def test_public_login_and_liveness_routes_render() -> None:
    client = TestClient(app)
    login = client.get("/login")
    assert login.status_code == 200
    assert "Good to see you" in login.text
    assert "default-src 'self'" in login.headers["content-security-policy"]
    assert client.get("/health/live").json() == {"status": "ok"}


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
        group = CrewGroup(name="OB Crew")
        db.add_all([region, group])
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
        db.add_all([track, position, person, manager, employee, viewer, admin])
        db.flush()
        db.add_all(
            [
                UserPersonLink(user_id=employee.id, person_id=person.id),
                RoleGrant(user_id=manager.id, role=Role.MANAGER.value, region_id=region.id),
                RoleGrant(user_id=employee.id, role=Role.EMPLOYEE.value, region_id=region.id),
                RoleGrant(user_id=viewer.id, role=Role.VIEWER.value, region_id=region.id),
                RoleGrant(user_id=admin.id, role=Role.ADMIN.value, region_id=None),
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
    assigned = manager_client.post(
        f"{workday_path}/assignments",
        data={
            "base_position_id": str(position_id),
            "slot_index": "2",
            "person_id": str(person_id),
            "status": "ASSIGNED",
            "note": "Private transport",
            "note_private": "true",
            "csrf_token": csrf,
        },
        follow_redirects=False,
    )
    assert assigned.status_code == 303
    assert "First publication" in manager_client.get(f"{workday_path}/preview").text
    with factory() as db:
        workday = db.get(Workday, workday_id)
        draft_id = workday.current_draft_revision_id
    published = manager_client.post(
        f"{workday_path}/publish",
        data={"draft_id": str(draft_id), "csrf_token": csrf},
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
    day_payload = employee_client.get(f"/api/day/{workday_id}").json()
    assert day_payload["saved_at"]
    assert day_payload["workday"]["revision_id"]
    assert day_payload["workday"]["revision_number"] == 1
    crew = employee_client.get(f"/crew?region_id={region_id}&year=2026&month=9")
    assert crew.status_code == 200 and "Ellerslie" in crew.text
    assert employee_client.get("/manage/workdays/new").status_code == 403
    assert manager_client.get("/admin").status_code == 403
    viewer_client = TestClient(app)
    _login(viewer_client, "viewer@example.test", "112233")
    assert "Private transport" not in viewer_client.get(f"/day/{workday_id}").text

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
