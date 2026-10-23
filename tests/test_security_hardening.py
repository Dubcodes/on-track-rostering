from __future__ import annotations

import zipfile
from datetime import timedelta
from io import BytesIO
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.admin.data_export import _csv_bytes, safe_data_export
from app.auth.factors import begin_totp
from app.auth.policy import Actor, actor_for, can_administer_user, can_assist_user
from app.auth.security import create_device, hash_credential, resolve_device, safe_return_path
from app.auth.service import (
    CREDENTIAL_RESET_UNAVAILABLE,
    consume_credential_reset,
    create_credential_reset,
    grant_role,
    revoke_role_grant,
)
from app.catalog.models import Region
from app.core.enums import Role
from app.core.time import utcnow
from app.identity.models import Person, RoleGrant, User, UserPersonLink
from app.notifications.service import (
    MAX_PUSH_RESPONSE_BYTES,
    _BoundedPushSession,
    _send_webpush,
    validate_push_endpoint,
)


def _user(db, email: str, secret: str = "123456") -> User:  # type: ignore[no-untyped-def]
    user = User(
        email=email,
        display_name=email.split("@", 1)[0],
        credential_hash=hash_credential(secret),
    )
    db.add(user)
    db.flush()
    return user


def test_pending_privileged_authority_is_protected_from_regional_takeover(db) -> None:  # type: ignore[no-untyped-def]
    region = Region(name="Protected")
    manager = _user(db, "manager@example.test")
    sub_manager = _user(db, "sub@example.test")
    pending_manager = _user(db, "pending-manager@example.test")
    pending_admin = _user(db, "pending-admin@example.test", "12345678")
    manager_person = Person(display_name="Pending manager", home_region_id=region.id)
    admin_person = Person(display_name="Pending admin", home_region_id=region.id)
    db.add_all([region, manager_person, admin_person])
    db.flush()
    db.add_all(
        [
            RoleGrant(user_id=manager.id, role=Role.MANAGER.value, region_id=region.id),
            RoleGrant(user_id=sub_manager.id, role=Role.SUB_MANAGER.value, region_id=region.id),
            RoleGrant(
                user_id=pending_manager.id,
                role=Role.MANAGER.value,
                region_id=region.id,
                status="PENDING",
            ),
            RoleGrant(
                user_id=pending_manager.id,
                role=Role.EMPLOYEE.value,
                region_id=region.id,
            ),
            RoleGrant(
                user_id=pending_admin.id,
                role=Role.ADMIN.value,
                status="PENDING",
            ),
            UserPersonLink(user_id=pending_manager.id, person_id=manager_person.id),
            UserPersonLink(user_id=pending_admin.id, person_id=admin_person.id),
        ]
    )
    db.commit()

    assert not can_administer_user(db, actor_for(db, manager), pending_manager, region.id)
    assert not can_assist_user(db, actor_for(db, sub_manager), pending_manager, region.id)
    assert not can_administer_user(db, actor_for(db, manager), pending_admin, region.id)
    assert not can_assist_user(db, actor_for(db, sub_manager), pending_admin, region.id)

    with pytest.raises(PermissionError, match="cannot reset credentials"):
        create_credential_reset(
            db,
            user=pending_manager,
            actor=actor_for(db, manager),
            region_id=region.id,
            allow_operational_assist=True,
        )
    db.rollback()

    with pytest.raises(PermissionError, match="cannot grant access"):
        grant_role(
            db,
            actor=actor_for(db, manager),
            target_user=pending_manager,
            role=Role.CASUAL.value,
            region_id=region.id,
        )
    db.rollback()

    employee_grant = db.scalar(
        select(RoleGrant).where(
            RoleGrant.user_id == pending_manager.id,
            RoleGrant.role == Role.EMPLOYEE.value,
        )
    )
    assert employee_grant is not None
    with pytest.raises(PermissionError, match="cannot revoke access"):
        revoke_role_grant(db, actor=actor_for(db, manager), grant=employee_grant)
    db.rollback()


def test_privilege_grant_revokes_preexisting_credential_reset(db) -> None:  # type: ignore[no-untyped-def]
    region = Region(name="Elevation")
    admin = _user(db, "admin@example.test", "12345678")
    target = _user(db, "target@example.test")
    db.add(region)
    db.flush()
    db.add_all(
        [
            RoleGrant(user_id=admin.id, role=Role.ADMIN.value),
            RoleGrant(user_id=target.id, role=Role.EMPLOYEE.value, region_id=region.id),
        ]
    )
    db.commit()
    reset, raw = create_credential_reset(
        db,
        user=target,
        actor=Actor(
            user_id=admin.id,
            person_id=None,
            global_roles=frozenset({Role.ADMIN.value}),
            regional_roles={},
        ),
    )

    grant_role(
        db,
        actor=actor_for(db, admin),
        target_user=target,
        role=Role.MANAGER.value,
        region_id=region.id,
    )

    db.refresh(reset)
    assert reset.revoked_at is not None
    with pytest.raises(ValueError, match=CREDENTIAL_RESET_UNAVAILABLE):
        consume_credential_reset(db, raw, "654321")


def test_mandatory_manager_mfa_requires_enrollment_before_grant(db, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    region = Region(name="MFA transition")
    admin = _user(db, "mfa-admin@example.test", "12345678")
    target = _user(db, "mfa-target@example.test")
    db.add(region)
    db.flush()
    db.add_all(
        [
            RoleGrant(user_id=admin.id, role=Role.ADMIN.value),
            RoleGrant(user_id=target.id, role=Role.EMPLOYEE.value, region_id=region.id),
        ]
    )
    db.commit()
    from app.auth import service as service_module

    enforced = service_module.get_settings().model_copy(update={"mfa_required_manager": True})
    monkeypatch.setattr(service_module, "get_settings", lambda: enforced)
    with pytest.raises(ValueError, match="Enroll an authenticator"):
        grant_role(
            db,
            actor=actor_for(db, admin),
            target_user=target,
            role=Role.MANAGER.value,
            region_id=region.id,
        )

    factor, _secret, _svg = begin_totp(db, target)
    factor.confirmed_at = utcnow()
    db.commit()
    grant = grant_role(
        db,
        actor=actor_for(db, admin),
        target_user=target,
        role=Role.MANAGER.value,
        region_id=region.id,
    )
    assert grant.status == "PENDING"


def test_final_grant_revocation_prevents_session_and_new_device(db) -> None:  # type: ignore[no-untyped-def]
    region = Region(name="Deprovision")
    admin = _user(db, "admin@example.test", "12345678")
    target = _user(db, "target@example.test")
    db.add(region)
    db.flush()
    admin_grant = RoleGrant(user_id=admin.id, role=Role.ADMIN.value)
    target_grant = RoleGrant(user_id=target.id, role=Role.EMPLOYEE.value, region_id=region.id)
    db.add_all([admin_grant, target_grant])
    db.commit()
    raw_session, _raw_csrf, _device = create_device(db, target)

    revoke_role_grant(db, actor=actor_for(db, admin), grant=target_grant)

    assert resolve_device(db, raw_session) is None
    with pytest.raises(ValueError, match="no active access grant"):
        create_device(db, target)


def test_absolute_session_lifetime_cannot_slide_forever(db) -> None:  # type: ignore[no-untyped-def]
    region = Region(name="Sessions")
    user = _user(db, "session@example.test")
    db.add(region)
    db.flush()
    db.add(RoleGrant(user_id=user.id, role=Role.EMPLOYEE.value, region_id=region.id))
    db.commit()
    raw_session, _raw_csrf, device = create_device(db, user)
    device.created_at = utcnow() - timedelta(days=181)
    device.expires_at = utcnow() + timedelta(days=30)
    db.commit()

    assert resolve_device(db, raw_session) is None
    db.refresh(device)
    assert device.revoked_at is not None


@pytest.mark.parametrize(
    "value",
    [
        "//evil.test",
        "/\\evil.test",
        "/%5cevil.test",
        "/%2f%2fevil.test",
        "/safe%0d%0aLocation:%20https://evil.test",
        "https://evil.test/path",
        "/month#https://evil.test",
    ],
)
def test_safe_return_path_rejects_redirect_confusion(value: str) -> None:
    assert safe_return_path(value) == "/month"


def test_safe_return_path_preserves_local_query() -> None:
    assert safe_return_path("/month?date=2026-10-01") == "/month?date=2026-10-01"


def test_push_endpoint_rejects_untrusted_or_non_public_destinations() -> None:
    def public(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        return [(2, 1, 6, "", ("8.8.8.8", 443))]

    def private(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        return [(2, 1, 6, "", ("127.0.0.1", 443))]

    validate_push_endpoint("https://fcm.googleapis.com/fcm/send/id", resolver=public)
    with pytest.raises(ValueError, match="provider"):
        validate_push_endpoint("https://attacker.example/push", resolver=public)
    with pytest.raises(ValueError, match="non-public"):
        validate_push_endpoint("https://fcm.googleapis.com/fcm/send/id", resolver=private)
    with pytest.raises(ValueError, match="permitted"):
        validate_push_endpoint("https://user@fcm.googleapis.com/fcm/send/id", resolver=public)


def test_rejected_push_destination_is_never_sent(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from app.notifications import service as notification_service

    settings = notification_service.get_settings().model_copy(
        update={"vapid_private_key": "configured-test-key"}
    )
    contacted = []
    monkeypatch.setattr(notification_service, "get_settings", lambda: settings)
    monkeypatch.setattr(
        notification_service,
        "validate_push_endpoint",
        lambda _endpoint: (_ for _ in ()).throw(ValueError("rejected")),
    )
    monkeypatch.setattr(
        notification_service,
        "webpush",
        lambda **kwargs: contacted.append(kwargs),
    )

    with pytest.raises(ValueError, match="rejected"):
        _send_webpush(
            {
                "endpoint": "https://127.0.0.1/internal",
                "keys": {"p256dh": "x", "auth": "y"},
            },
            "{}",
        )
    assert contacted == []


def test_push_transport_disables_redirects_and_bounds_response(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from requests import Session

    observed = {}

    class RawBody(BytesIO):
        def read(self, size=-1, decode_content=False):  # type: ignore[no-untyped-def]
            return super().read(size)

    class Response:
        def __init__(self, content: bytes) -> None:
            self.raw = RawBody(content)
            self.closed = False
            self._content = b""
            self._content_consumed = False

        def close(self) -> None:
            self.closed = True

    response = Response(b"accepted")

    def post(_self, url, data=None, json=None, **kwargs):  # type: ignore[no-untyped-def]
        observed.update(url=url, data=data, json=json, kwargs=kwargs)
        return response

    monkeypatch.setattr(Session, "post", post)
    returned = _BoundedPushSession().post("https://fcm.googleapis.com/push", data=b"x")
    assert returned is response and response.closed
    assert response._content == b"accepted"
    assert observed["kwargs"]["allow_redirects"] is False
    assert observed["kwargs"]["stream"] is True

    oversized = Response(b"x" * (MAX_PUSH_RESPONSE_BYTES + 1))
    monkeypatch.setattr(Session, "post", lambda *_args, **_kwargs: oversized)
    with pytest.raises(RuntimeError, match="response exceeded"):
        _BoundedPushSession().post("https://fcm.googleapis.com/push")
    assert oversized.closed


def test_spreadsheet_safe_csv_is_distinct_from_exact_machine_export() -> None:
    row = SimpleNamespace(name=" \t=HYPERLINK(\"https://evil.test\")", ordinary="Crew")
    safe = _csv_bytes([row], ("name", "ordinary"))
    exact = _csv_bytes([row], ("name", "ordinary"), spreadsheet_safe=False)
    assert b"'=HYPERLINK" not in safe  # Original leading whitespace remains after the apostrophe.
    assert b"' \t=HYPERLINK" in safe
    assert b"' \t=HYPERLINK" not in exact
    assert b" \t=HYPERLINK" in exact
    assert b"Crew" in safe and b"Crew" in exact


def test_export_zip_keeps_safe_presentation_and_exact_machine_values(db) -> None:  # type: ignore[no-untyped-def]
    db.add(Person(display_name="=2+2", email="normal@example.test"))
    db.commit()

    with zipfile.ZipFile(BytesIO(safe_data_export(db))) as archive:
        safe = archive.read("people.csv")
        exact = archive.read("machine-readable/people.csv")
        readme = archive.read("README.txt")

    assert b"'=2+2" in safe and b"'=2+2" not in exact
    assert b"=2+2" in exact
    assert b"spreadsheet-safe" in readme and b"machine-readable" in readme
