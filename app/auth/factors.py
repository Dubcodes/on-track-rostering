from __future__ import annotations

import base64
import hashlib
import json
import secrets
import uuid
from datetime import UTC, datetime, timedelta

import pyotp
import qrcode
from cryptography.fernet import Fernet, InvalidToken
from qrcode.image.svg import SvgPathImage
from sqlalchemy import select
from sqlalchemy.orm import Session
from webauthn import base64url_to_bytes

from app.branding.service import branding_for
from app.core.config import get_settings
from app.core.enums import Role
from app.core.time import utcnow
from app.identity.models import RoleGrant, TotpFactor, User, WebAuthnChallenge


def _fernet() -> Fernet:
    material = hashlib.sha256(("ontrack-totp|" + get_settings().secret_key).encode()).digest()
    return Fernet(base64.urlsafe_b64encode(material))


def encrypt_totp_secret(secret: str) -> bytes:
    return _fernet().encrypt(secret.encode())


def decrypt_totp_secret(value: bytes) -> str:
    try:
        return _fernet().decrypt(value).decode()
    except InvalidToken as exc:
        raise ValueError("The stored authenticator factor cannot be decrypted.") from exc


def begin_totp(
    db: Session, user: User, *, product_name: str | None = None
) -> tuple[TotpFactor, str, str]:
    secret = pyotp.random_base32()
    factor = db.get(TotpFactor, user.id)
    if factor and factor.confirmed_at and not factor.disabled_at:
        raise ValueError("Authenticator MFA is already enabled.")
    if factor is None:
        factor = TotpFactor(user_id=user.id, encrypted_secret=encrypt_totp_secret(secret))
        db.add(factor)
    else:
        factor.encrypted_secret = encrypt_totp_secret(secret)
        factor.confirmed_at = None
        factor.disabled_at = None
        factor.last_counter = None
    uri = pyotp.TOTP(secret).provisioning_uri(
        name=user.email, issuer_name=product_name or branding_for(db).product_name
    )
    qr = qrcode.make(uri, image_factory=SvgPathImage)
    from io import BytesIO

    stream = BytesIO()
    qr.save(stream)
    db.commit()
    return factor, secret, base64.b64encode(stream.getvalue()).decode()


def verify_totp_factor(factor: TotpFactor, code: str, *, now: datetime | None = None) -> bool:
    secret = decrypt_totp_secret(factor.encrypted_secret)
    instant = now or utcnow()
    totp = pyotp.TOTP(secret)
    current = totp.timecode(instant)
    for counter in (current - 1, current, current + 1):
        if factor.last_counter is not None and counter <= factor.last_counter:
            continue
        if secrets.compare_digest(totp.generate_otp(counter), code.strip()):
            factor.last_counter = counter
            return True
    return False


def active_totp(db: Session, user_id: uuid.UUID) -> TotpFactor | None:
    return db.scalar(
        select(TotpFactor).where(
            TotpFactor.user_id == user_id,
            TotpFactor.confirmed_at.is_not(None),
            TotpFactor.disabled_at.is_(None),
        )
    )


def mfa_required(db: Session, user: User) -> bool:
    if active_totp(db, user.id):
        return True
    roles = set(
        db.scalars(
            select(RoleGrant.role).where(
                RoleGrant.user_id == user.id, RoleGrant.status == "ACTIVE"
            )
        )
    )
    settings = get_settings()
    return bool(
        (settings.mfa_required_admin and Role.ADMIN.value in roles)
        or (settings.mfa_required_manager and Role.MANAGER.value in roles)
    )


def create_challenge(
    db: Session,
    *,
    purpose: str,
    challenge: bytes | None = None,
    user_id: uuid.UUID | None = None,
    device_id: uuid.UUID | None = None,
) -> tuple[WebAuthnChallenge, bytes]:
    raw = challenge or secrets.token_bytes(32)
    row = WebAuthnChallenge(
        purpose=purpose,
        challenge_hash=hashlib.sha256(raw).hexdigest(),
        user_id=user_id,
        device_id=device_id,
        expires_at=utcnow() + timedelta(minutes=get_settings().webauthn_challenge_minutes),
    )
    db.add(row)
    db.commit()
    return row, raw


def client_challenge(credential: dict[str, object]) -> bytes:
    response = credential.get("response")
    if not isinstance(response, dict) or not isinstance(response.get("clientDataJSON"), str):
        raise ValueError("Malformed WebAuthn response.")
    try:
        data = json.loads(base64url_to_bytes(response["clientDataJSON"]))
        return base64url_to_bytes(data["challenge"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("Malformed WebAuthn client data.") from exc


def consume_challenge(
    db: Session,
    *,
    challenge_id: uuid.UUID,
    purpose: str,
    raw_challenge: bytes,
    user_id: uuid.UUID | None = None,
    device_id: uuid.UUID | None = None,
) -> WebAuthnChallenge:
    row = db.scalar(
        select(WebAuthnChallenge)
        .where(WebAuthnChallenge.id == challenge_id)
        .with_for_update()
    )
    expires = row.expires_at if row else None
    if expires and expires.tzinfo is None:
        expires = expires.replace(tzinfo=UTC)
    valid = bool(
        row
        and row.purpose == purpose
        and row.consumed_at is None
        and expires
        and expires > utcnow()
        and row.user_id == user_id
        and row.device_id == device_id
        and secrets.compare_digest(row.challenge_hash, hashlib.sha256(raw_challenge).hexdigest())
    )
    if not valid:
        raise ValueError("WebAuthn challenge is invalid, expired, or already used.")
    row.consumed_at = utcnow()
    db.commit()
    return row
