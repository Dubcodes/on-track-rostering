from __future__ import annotations

import secrets
from datetime import timedelta

import pyotp
import pytest

from app.auth.factors import (
    active_totp,
    begin_totp,
    consume_challenge,
    create_challenge,
    decrypt_totp_secret,
    verify_totp_factor,
)
from app.auth.security import hash_credential
from app.core.time import utcnow
from app.identity.models import User


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
