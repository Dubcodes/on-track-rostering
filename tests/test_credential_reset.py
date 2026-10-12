from __future__ import annotations

import uuid
from datetime import timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.audit.models import AuditEvent
from app.auth.factors import mfa_required
from app.auth.security import create_device, hash_credential, token_hash, verify_credential
from app.auth.service import (
    CREDENTIAL_RESET_UNAVAILABLE,
    consume_credential_reset,
    create_credential_reset,
)
from app.catalog.models import Region
from app.core.enums import Role
from app.core.time import utcnow
from app.identity.models import (
    PasskeyCredential,
    Person,
    RecoveryCode,
    RoleGrant,
    TotpFactor,
    User,
    UserPersonLink,
    WebAuthnChallenge,
)


def _user(db, email: str = "target@example.test", secret: str = "123456") -> User:  # type: ignore[no-untyped-def]
    user = User(
        email=email,
        display_name="Reset Target",
        credential_hash=hash_credential(secret),
        credential_kind="pin",
    )
    db.add(user)
    db.flush()
    return user


def test_reset_tokens_are_hash_only_hour_limited_and_supersede_prior_tokens(db) -> None:  # type: ignore[no-untyped-def]
    admin = _user(db, "admin@example.test", "12345678")
    target = _user(db)
    db.commit()

    first, first_raw = create_credential_reset(db, user=target, actor_user_id=admin.id)
    second, second_raw = create_credential_reset(db, user=target, actor_user_id=admin.id)

    db.refresh(first)
    assert first_raw != second_raw
    assert first.token_hash == token_hash(first_raw)
    assert first_raw not in first.token_hash
    assert timedelta(minutes=59, seconds=55) <= first.expires_at - first.created_at <= timedelta(
        minutes=60, seconds=5
    )
    assert first.revoked_at is not None
    assert second.revoked_at is None and second.consumed_at is None
    event = db.scalar(
        select(AuditEvent)
        .where(AuditEvent.action == "user.credential_reset.created")
        .order_by(AuditEvent.occurred_at.desc())
    )
    assert event.actor_user_id == admin.id
    assert first_raw not in str(event.detail) and second_raw not in str(event.detail)


@pytest.mark.parametrize("state", ["missing", "expired", "revoked", "consumed", "disabled"])
def test_reset_terminal_states_share_one_generic_error(db, state: str) -> None:  # type: ignore[no-untyped-def]
    admin = _user(db, "admin@example.test", "12345678")
    target = _user(db)
    db.commit()
    reset, raw = create_credential_reset(db, user=target, actor_user_id=admin.id)
    if state == "missing":
        raw = "unknown-token"
    elif state == "expired":
        reset.expires_at = utcnow() - timedelta(seconds=1)
    elif state == "revoked":
        reset.revoked_at = utcnow()
    elif state == "consumed":
        reset.consumed_at = utcnow()
    else:
        target.status = "DISABLED"
    db.commit()

    with pytest.raises(ValueError, match=CREDENTIAL_RESET_UNAVAILABLE):
        consume_credential_reset(db, raw, "654321")


@pytest.mark.parametrize("grant_status", ["ACTIVE", "PENDING"])
def test_admin_grant_requires_admin_strength_credential(db, grant_status: str) -> None:  # type: ignore[no-untyped-def]
    issuer = _user(db, "issuer@example.test", "12345678")
    target = _user(db)
    db.add(RoleGrant(user_id=target.id, role=Role.ADMIN.value, status=grant_status))
    db.commit()
    _reset, raw = create_credential_reset(db, user=target, actor_user_id=issuer.id)

    with pytest.raises(ValueError, match="Admin PIN"):
        consume_credential_reset(db, raw, "654321")
    assert verify_credential("123456", target.credential_hash)


def test_consumption_rotates_only_primary_credential_and_security_state(db) -> None:  # type: ignore[no-untyped-def]
    issuer = _user(db, "issuer@example.test", "12345678")
    target = _user(db)
    target.contractor_manual_extension_at = utcnow()
    target.contractor_access_expires_at = utcnow() + timedelta(days=30)
    region = Region(name="Reset Region")
    person = Person(display_name="Reset Person")
    db.add_all([region, person])
    db.flush()
    active_grant = RoleGrant(
        user_id=target.id, role=Role.EMPLOYEE.value, region_id=region.id, status="ACTIVE"
    )
    pending_grant = RoleGrant(
        user_id=target.id, role=Role.SUB_MANAGER.value, region_id=region.id, status="PENDING"
    )
    passkey = PasskeyCredential(
        user_id=target.id,
        credential_id=b"credential-" + uuid.uuid4().bytes,
        public_key=b"public-key",
    )
    totp = TotpFactor(user_id=target.id, encrypted_secret=b"encrypted", confirmed_at=utcnow())
    recovery = RecoveryCode(user_id=target.id, code_hash=uuid.uuid4().hex)
    db.add_all(
        [
            active_grant,
            pending_grant,
            UserPersonLink(user_id=target.id, person_id=person.id),
            passkey,
            totp,
            recovery,
        ]
    )
    db.commit()
    _session, _csrf, device = create_device(db, target)
    challenge = WebAuthnChallenge(
        purpose="TOTP_LOGIN",
        challenge_hash=uuid.uuid4().hex,
        user_id=target.id,
        expires_at=utcnow() + timedelta(minutes=5),
    )
    db.add(challenge)
    db.commit()
    original_epoch = target.auth_epoch
    original_status = target.status
    original_contractor_expiry = target.contractor_access_expires_at
    original_person_id = person.id
    reset, raw = create_credential_reset(db, user=target, actor_user_id=issuer.id)

    consumed_user = consume_credential_reset(db, raw, "654321")

    db.refresh(reset)
    db.refresh(device)
    db.refresh(challenge)
    db.refresh(pending_grant)
    assert consumed_user.id == target.id
    assert consumed_user.status == original_status
    assert consumed_user.contractor_access_expires_at == original_contractor_expiry
    assert consumed_user.auth_epoch == original_epoch + 1
    assert not verify_credential("123456", consumed_user.credential_hash)
    assert verify_credential("654321", consumed_user.credential_hash)
    assert reset.consumed_at is not None
    assert device.revoked_at is not None and challenge.consumed_at is not None
    assert pending_grant.status == "PENDING" and pending_grant.activated_at is None
    assert db.get(UserPersonLink, target.id).person_id == original_person_id
    assert db.get(PasskeyCredential, passkey.id) is not None
    assert db.get(TotpFactor, target.id) is not None
    assert db.get(RecoveryCode, recovery.id) is not None
    assert mfa_required(db, consumed_user)
    event = db.scalar(
        select(AuditEvent).where(AuditEvent.action == "user.credential_reset.completed")
    )
    assert event is not None and event.actor_user_id is None and event.detail == {}


def test_reset_credential_cli_uses_getpass_and_never_prints_secret(
    db, monkeypatch, capsys
) -> None:  # type: ignore[no-untyped-def]
    import app.cli as cli_module

    target = _user(db)
    db.commit()
    factory = sessionmaker(bind=db.get_bind(), expire_on_commit=False)
    answers = iter(["654321", "654321"])
    monkeypatch.setattr(cli_module, "SessionLocal", factory)
    monkeypatch.setattr(cli_module.getpass, "getpass", lambda _prompt: next(answers))

    assert cli_module.reset_credential_command(
        SimpleNamespace(email="TARGET@example.test")
    ) == 0
    output = capsys.readouterr().out
    assert "654321" not in output and "Reset Target" in output
    with factory() as check:
        saved = check.get(User, target.id)
        event = check.scalar(
            select(AuditEvent).where(AuditEvent.action == "user.credential_reset.cli")
        )
        assert verify_credential("654321", saved.credential_hash)
        assert event is not None and event.actor_user_id is None and event.detail == {}


def test_reset_credential_cli_rejects_mismatch_without_mutation(
    db, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    import app.cli as cli_module

    target = _user(db)
    original_hash = target.credential_hash
    db.commit()
    factory = sessionmaker(bind=db.get_bind(), expire_on_commit=False)
    answers = iter(["654321", "111111"])
    monkeypatch.setattr(cli_module, "SessionLocal", factory)
    monkeypatch.setattr(cli_module.getpass, "getpass", lambda _prompt: next(answers))
    with pytest.raises(SystemExit, match="did not match"):
        cli_module.reset_credential_command(SimpleNamespace(email=target.email))
    with factory() as check:
        assert check.get(User, target.id).credential_hash == original_hash


def test_reset_credential_cli_enforces_pending_admin_policy(db, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import app.cli as cli_module

    target = _user(db)
    db.add(RoleGrant(user_id=target.id, role=Role.ADMIN.value, status="PENDING"))
    db.commit()
    factory = sessionmaker(bind=db.get_bind(), expire_on_commit=False)
    answers = iter(["654321", "654321"])
    monkeypatch.setattr(cli_module, "SessionLocal", factory)
    monkeypatch.setattr(cli_module.getpass, "getpass", lambda _prompt: next(answers))
    with pytest.raises(SystemExit, match="Admin PIN"):
        cli_module.reset_credential_command(SimpleNamespace(email=target.email))
    with factory() as check:
        assert verify_credential("123456", check.get(User, target.id).credential_hash)
