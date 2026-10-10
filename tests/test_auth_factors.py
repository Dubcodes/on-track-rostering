from __future__ import annotations

import secrets
from datetime import timedelta

import pyotp
import pytest
from sqlalchemy import select

from app.auth.factors import (
    active_totp,
    begin_totp,
    consume_challenge,
    consume_recovery_code,
    create_challenge,
    decrypt_totp_secret,
    generate_recovery_codes,
    remaining_recovery_codes,
    verify_totp_factor,
)
from app.auth.security import hash_credential
from app.core.time import utcnow
from app.identity.models import RecoveryCode, User


def _user(db) -> User:  # type: ignore[no-untyped-def]
    user = User(
        email="factor@example.test",
        display_name="Factor User",
        credential_hash=hash_credential("123456"),
        credential_kind="pin",
    )
    db.add(user)
    db.commit()
    return user


def test_totp_setup_requires_confirmation_and_prevents_replay(db) -> None:  # type: ignore[no-untyped-def]
    user = _user(db)
    factor, secret, svg = begin_totp(db, user)
    assert active_totp(db, user.id) is None
    assert secret.encode() not in factor.encrypted_secret
    assert svg
    code = pyotp.TOTP(secret).now()
    assert verify_totp_factor(factor, code)
    assert not verify_totp_factor(factor, code)
    factor.confirmed_at = utcnow()
    db.commit()
    assert active_totp(db, user.id) is not None
    with pytest.raises(ValueError, match="already enabled"):
        begin_totp(db, user)


def test_totp_ciphertext_cannot_be_opened_after_key_change(db, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    user = _user(db)
    factor, _, _ = begin_totp(db, user)
    from app.auth import factors as factor_module

    original = factor_module.get_settings
    settings = original().model_copy(update={"secret_key": "different-key-material-that-is-long-enough"})
    monkeypatch.setattr(factor_module, "get_settings", lambda: settings)
    with pytest.raises(ValueError, match="cannot be decrypted"):
        decrypt_totp_secret(factor.encrypted_secret)


def test_webauthn_challenge_is_bound_expiring_and_one_time(db) -> None:  # type: ignore[no-untyped-def]
    user = _user(db)
    raw = secrets.token_bytes(32)
    challenge, _ = create_challenge(
        db,
        purpose="PASSKEY_REGISTER",
        challenge=raw,
        user_id=user.id,
    )
    with pytest.raises(ValueError):
        consume_challenge(
            db,
            challenge_id=challenge.id,
            purpose="PASSKEY_REGISTER",
            raw_challenge=raw,
            user_id=None,
        )
    consume_challenge(
        db,
        challenge_id=challenge.id,
        purpose="PASSKEY_REGISTER",
        raw_challenge=raw,
        user_id=user.id,
    )
    with pytest.raises(ValueError, match="already used"):
        consume_challenge(
            db,
            challenge_id=challenge.id,
            purpose="PASSKEY_REGISTER",
            raw_challenge=raw,
            user_id=user.id,
        )

    expired, expired_raw = create_challenge(db, purpose="PASSKEY_AUTH")
    expired.expires_at = utcnow() - timedelta(seconds=1)
    db.commit()
    with pytest.raises(ValueError, match="expired"):
        consume_challenge(
            db,
            challenge_id=expired.id,
            purpose="PASSKEY_AUTH",
            raw_challenge=expired_raw,
        )


def test_recovery_codes_are_hashed_scoped_single_use_and_replace_unused(db) -> None:  # type: ignore[no-untyped-def]
    user = _user(db)
    other = User(
        email="other-factor@example.test",
        display_name="Other Factor User",
        credential_hash=hash_credential("123456"),
        credential_kind="pin",
    )
    db.add(other)
    db.commit()

    codes = generate_recovery_codes(db, user.id)
    db.commit()
    rows = list(db.scalars(select(RecoveryCode).where(RecoveryCode.user_id == user.id)))
    assert len(codes) == len(rows) == 10
    assert remaining_recovery_codes(db, user.id) == 10
    assert all(code not in {row.code_hash for row in rows} for code in codes)
    assert all(len(row.code_hash) == 64 for row in rows)
    assert not consume_recovery_code(db, other.id, codes[0])
    assert consume_recovery_code(db, user.id, codes[0].lower().replace("-", " "))
    assert not consume_recovery_code(db, user.id, codes[0])
    assert remaining_recovery_codes(db, user.id) == 9
    db.commit()

    replacement = generate_recovery_codes(db, user.id)
    db.commit()
    assert len(replacement) == 10
    assert remaining_recovery_codes(db, user.id) == 10
    assert not consume_recovery_code(db, user.id, codes[1])
