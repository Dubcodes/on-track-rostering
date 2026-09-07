from __future__ import annotations

import hashlib
import hmac
import ipaddress
import secrets
from datetime import UTC, timedelta
from urllib.parse import urlsplit

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from fastapi import HTTPException, Request, status
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.enums import Role
from app.core.time import utcnow
from app.identity.models import LoginThrottle, RoleGrant, TrustedDevice, User

SESSION_COOKIE = "ontrack_session"
CSRF_COOKIE = "ontrack_csrf"
ph = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=2, hash_len=32, salt_len=16)
ELEVATED_ROLES = {Role.SUB_MANAGER.value, Role.MANAGER.value, Role.VIEWER.value, Role.ADMIN.value}


def credential_error(secret: str, role: str) -> str:
    if role == Role.ADMIN.value:
        if secret.isascii() and secret.isdigit():
            return "" if 8 <= len(secret) <= 64 else "Admin PIN must contain at least 8 digits."
        return "" if len(secret) >= 12 else "Admin password must contain at least 12 characters."
    return (
        ""
        if secret.isascii() and secret.isdigit() and 6 <= len(secret) <= 32
        else "PIN must contain 6–32 digits."
    )


def hash_credential(secret: str) -> str:
    peppered = secret + get_settings().credential_pepper
    return ph.hash(peppered)


def verify_credential(secret: str, encoded: str) -> bool:
    try:
        return ph.verify(encoded, secret + get_settings().credential_pepper)
    except (VerificationError, InvalidHashError):
        return False


def token_hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def keyed_hash(value: str) -> str:
    return hmac.new(get_settings().secret_key.encode(), value.encode(), hashlib.sha256).hexdigest()


def is_safe_next(value: str) -> bool:
    parsed = urlsplit(value)
    return bool(
        value.startswith("/") and not value.startswith("//") and not parsed.scheme and not parsed.netloc
    )


def same_origin(request: Request) -> bool:
    if request.headers.get("sec-fetch-site", "same-origin") == "cross-site":
        return False
    origin = request.headers.get("origin")
    if not origin:
        return True
    parsed = urlsplit(origin)
    expected_port = request.url.port or (443 if request.url.scheme == "https" else 80)
    actual_port = parsed.port or (443 if parsed.scheme == "https" else 80)
    return (
        parsed.scheme == request.url.scheme
        and parsed.hostname == request.url.hostname
        and actual_port == expected_port
    )


def verify_csrf(request: Request, submitted: str) -> None:
    device = getattr(request.state, "device", None)
    cookie = request.cookies.get(CSRF_COOKIE, "")
    if not device or not submitted or not cookie:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Form security check failed")
    if not secrets.compare_digest(submitted, cookie) or not secrets.compare_digest(
        token_hash(cookie), device.csrf_hash
    ):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Form security check failed")
    if not same_origin(request):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Cross-site request rejected")


def throttle_keys(email: str, ip: str) -> tuple[str, str]:
    try:
        ip = str(ipaddress.ip_address(ip))
    except ValueError:
        ip = "unknown"
    return keyed_hash(f"account|{email.strip().lower()}"), keyed_hash(f"address|{ip}")


def is_throttled(db: Session, key: str) -> bool:
    row = db.get(LoginThrottle, key)
    blocked_until = row.blocked_until if row else None
    if blocked_until and blocked_until.tzinfo is None:
        blocked_until = blocked_until.replace(tzinfo=UTC)
    return bool(blocked_until and blocked_until > utcnow())


def record_failure(db: Session, key: str) -> None:
    now = utcnow()
    row = db.get(LoginThrottle, key)
    window_start = row.window_started_at if row else None
    if window_start and window_start.tzinfo is None:
        window_start = window_start.replace(tzinfo=UTC)
    if row is None or window_start < now - timedelta(minutes=15):
        row = LoginThrottle(key_hash=key, failure_count=1, window_started_at=now, updated_at=now)
        db.merge(row)
    else:
        row.failure_count += 1
        row.updated_at = now
        if row.failure_count >= 5:
            row.blocked_until = now + timedelta(minutes=15)
    db.commit()


def clear_failures(db: Session, key: str) -> None:
    db.execute(delete(LoginThrottle).where(LoginThrottle.key_hash == key))
    db.commit()


def create_device(db: Session, user: User, label: str = "Browser") -> tuple[str, str, TrustedDevice]:
    session_token, csrf_token = secrets.token_urlsafe(40), secrets.token_urlsafe(32)
    roles = set(db.scalars(select(RoleGrant.role).where(RoleGrant.user_id == user.id)))
    elevated = bool(roles & ELEVATED_ROLES)
    days = (
        get_settings().trusted_device_days_elevated
        if elevated
        else get_settings().trusted_device_days_standard
    )
    now = utcnow()
    device = TrustedDevice(
        user_id=user.id,
        token_hash=token_hash(session_token),
        csrf_hash=token_hash(csrf_token),
        label=label[:120],
        auth_epoch=user.auth_epoch,
        elevated=elevated,
        created_at=now,
        last_seen_at=now,
        expires_at=now + timedelta(days=days),
    )
    db.add(device)
    db.flush()
    device_ids = list(
        db.scalars(
            select(TrustedDevice.id)
            .where(TrustedDevice.user_id == user.id, TrustedDevice.revoked_at.is_(None))
            .order_by(TrustedDevice.last_seen_at.desc())
        )
    )
    for old_id in device_ids[get_settings().trusted_device_limit :]:
        old = db.get(TrustedDevice, old_id)
        if old:
            old.revoked_at = now
    db.commit()
    return session_token, csrf_token, device


def resolve_device(db: Session, raw_token: str) -> tuple[User, TrustedDevice] | None:
    if not raw_token:
        return None
    device = db.scalar(select(TrustedDevice).where(TrustedDevice.token_hash == token_hash(raw_token)))
    now = utcnow()
    expires_at = device.expires_at if device else None
    if expires_at and expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    if not device or device.revoked_at or expires_at <= now:
        return None
    user = db.get(User, device.user_id)
    if not user or user.status != "ACTIVE" or user.auth_epoch != device.auth_epoch:
        if device and not device.revoked_at:
            device.revoked_at = now
            db.commit()
        return None
    days = (
        get_settings().trusted_device_days_elevated
        if device.elevated
        else get_settings().trusted_device_days_standard
    )
    device.last_seen_at = now
    device.expires_at = now + timedelta(days=days)
    db.commit()
    return user, device


def active_device_count(db: Session, user_id: object) -> int:
    return int(
        db.scalar(
            select(func.count())
            .select_from(TrustedDevice)
            .where(
                TrustedDevice.user_id == user_id,
                TrustedDevice.revoked_at.is_(None),
                TrustedDevice.expires_at > utcnow(),
            )
        )
        or 0
    )
