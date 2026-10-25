from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from datetime import UTC, datetime, timedelta
from urllib.parse import unquote, urlsplit

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from fastapi import HTTPException, Request, status
from fastapi.responses import Response
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.auth.access import user_has_active_access
from app.auth.network import same_origin
from app.core.config import get_settings
from app.core.enums import Role
from app.core.time import utcnow
from app.identity.models import FreshActionIntent, LoginThrottle, RoleGrant, TrustedDevice, User

SESSION_COOKIE = "ontrack_session"
CSRF_COOKIE = "ontrack_csrf"
FRESH_ACTION_COOKIE = "ontrack_fresh_action"
ph = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=2, hash_len=32, salt_len=16)
ELEVATED_ROLES = {Role.SUB_MANAGER.value, Role.MANAGER.value, Role.VIEWER.value, Role.ADMIN.value}


class FreshAuthenticationRequired(HTTPException):
    def __init__(self) -> None:
        super().__init__(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Recent authentication is required. Confirm your credential and try again.",
        )


def credential_error(secret: str, role: str) -> str:
    if role == Role.ADMIN.value:
        if secret.isascii() and secret.isdigit():
            return "" if 8 <= len(secret) <= 64 else "Admin PIN must contain at least 8 digits."
        return "" if len(secret) >= 12 else "Admin password must contain at least 12 characters."
    if secret.isascii() and secret.isdigit():
        return "" if 6 <= len(secret) <= 32 else "PIN must contain 6–32 digits."
    return "" if len(secret) >= 12 else "Password must contain at least 12 characters."


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
    return safe_return_path(value, fallback="") == value


def safe_return_path(value: str | None, *, fallback: str = "/month") -> str:
    """Accept only an unambiguous same-origin absolute path."""
    if not value or any(ord(char) < 32 or ord(char) == 127 for char in value):
        return fallback
    decoded = value
    for _ in range(3):
        candidate = unquote(decoded)
        if candidate == decoded:
            break
        decoded = candidate
    if (
        any(ord(char) < 32 or ord(char) == 127 for char in decoded)
        or "\\" in decoded
        or not decoded.startswith("/")
        or decoded.startswith("//")
    ):
        return fallback
    try:
        parsed = urlsplit(decoded)
        original = urlsplit(value)
    except ValueError:
        return fallback
    if parsed.scheme or parsed.netloc or original.scheme or original.netloc or original.fragment:
        return fallback
    return value


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
    return keyed_hash(f"account|{email.strip().lower()}"), keyed_hash(f"address|{ip}")


def mfa_throttle_keys(user_id: object, ip: str) -> tuple[str, str]:
    return keyed_hash(f"mfa-user|{user_id}"), keyed_hash(f"mfa-address|{ip}")


def challenge_throttle_key(purpose: str, subject: object) -> str:
    return keyed_hash(f"challenge|{purpose}|{subject}")


def is_throttled(db: Session, key: str) -> bool:
    row = db.get(LoginThrottle, key)
    blocked_until = row.blocked_until if row else None
    if blocked_until and blocked_until.tzinfo is None:
        blocked_until = blocked_until.replace(tzinfo=UTC)
    return bool(blocked_until and blocked_until > utcnow())


def record_failure(db: Session, key: str) -> None:
    now = utcnow()
    if db.get_bind().dialect.name == "postgresql":
        advisory_key = int.from_bytes(bytes.fromhex(key[:16]), "big", signed=True)
        db.execute(select(func.pg_advisory_xact_lock(advisory_key)))
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
    if not user_has_active_access(db, user.id):
        raise ValueError("This account has no active access grant.")
    session_token, csrf_token = secrets.token_urlsafe(40), secrets.token_urlsafe(32)
    roles = set(
        db.scalars(select(RoleGrant.role).where(RoleGrant.user_id == user.id, RoleGrant.status == "ACTIVE"))
    )
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
        primary_authenticated_at=now,
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


_FRESH_ACTION_PATHS = (
    re.compile(r"^/admin/(users|invitations)(/[^/]+(?:/(?:status|devices/revoke|credential-reset|revoke))?)?$"),
    re.compile(r"^/admin/signup-requests/[^/]+/reject$"),
    re.compile(r"^/manage/accounts/(?:link-person|invitations(?:/[^/]+/revoke)?|contractors/[^/]+/(?:invite|extend)|people-invitations|people-metadata/apply|signup-requests/[^/]+/(?:approve|reject)|grants(?:/[^/]+/revoke)?)$"),
    re.compile(r"^/manage/crew$"),
    re.compile(r"^/manage/crew/[^/]+/(?:setup-link(?:/revoke)?|account-status|employment-type|credential-reset|devices/revoke|sub-manager|notice-scope|grants(?:/[^/]+/revoke)?)$"),
)


def allowed_fresh_action_path(value: str) -> str | None:
    path = safe_return_path(value, fallback="")
    if not path or "?" in path:
        return None
    return path if any(pattern.fullmatch(path) for pattern in _FRESH_ACTION_PATHS) else None


def create_fresh_action_intent(
    db: Session, *, user_id: object, device_id: object, action_path: str
) -> str:
    path = allowed_fresh_action_path(action_path)
    if not path:
        raise ValueError("That action cannot be reauthenticated here.")
    now = utcnow()
    db.execute(
        delete(FreshActionIntent).where(
            FreshActionIntent.device_id == device_id,
            (FreshActionIntent.consumed_at.is_not(None)) | (FreshActionIntent.expires_at <= now),
        )
    )
    raw = secrets.token_urlsafe(32)
    db.add(
        FreshActionIntent(
            user_id=user_id,
            device_id=device_id,
            token_hash=token_hash(raw),
            action_path=path,
            expires_at=now + timedelta(minutes=2),
        )
    )
    db.commit()
    return raw


def consume_fresh_action_intent(
    db: Session,
    *,
    raw_token: str,
    user_id: object,
    device_id: object,
    action_path: str,
) -> bool:
    """Atomically consume one intent bound to one device, user, and action."""
    if not raw_token:
        return False
    intent = db.scalar(
        select(FreshActionIntent)
        .where(FreshActionIntent.token_hash == token_hash(raw_token))
        .with_for_update()
    )
    now = utcnow()
    expires_at = intent.expires_at if intent else None
    if expires_at is not None and expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    if (
        not intent
        or intent.user_id != user_id
        or intent.device_id != device_id
        or intent.action_path != action_path
        or intent.consumed_at is not None
        or expires_at is None
        or expires_at <= now
    ):
        return False
    intent.consumed_at = now
    db.flush()
    return True


def _consume_fresh_action_intent(request: Request, db: Session) -> bool:
    raw = request.cookies.get(FRESH_ACTION_COOKIE, "")
    device = getattr(request.state, "device", None)
    user = getattr(request.state, "user", None)
    if not raw or not device or not user:
        return False
    consumed = consume_fresh_action_intent(
        db,
        raw_token=raw,
        user_id=user.id,
        device_id=device.id,
        action_path=request.url.path,
    )
    if consumed:
        request.state.fresh_action_consumed = True
    return consumed


def require_fresh_auth(request: Request, db: Session | None = None) -> None:
    device = getattr(request.state, "device", None)
    authenticated_at = device.primary_authenticated_at if device else None
    if authenticated_at and authenticated_at.tzinfo is None:
        authenticated_at = authenticated_at.replace(tzinfo=UTC)
    cutoff = utcnow() - timedelta(minutes=get_settings().fresh_auth_minutes)
    if (not authenticated_at or authenticated_at < cutoff) and not (
        db is not None and _consume_fresh_action_intent(request, db)
    ):
        raise FreshAuthenticationRequired()


def fresh_auth_expires_at(request: Request) -> datetime | None:
    """Return the current device's fresh-authentication deadline, if any."""
    device = getattr(request.state, "device", None)
    authenticated_at = device.primary_authenticated_at if device else None
    if not authenticated_at:
        return None
    if authenticated_at.tzinfo is None:
        authenticated_at = authenticated_at.replace(tzinfo=UTC)
    return authenticated_at + timedelta(minutes=get_settings().fresh_auth_minutes)


def has_fresh_auth(request: Request) -> bool:
    deadline = fresh_auth_expires_at(request)
    return bool(deadline and deadline >= utcnow())


def set_auth_cookies(
    response: Response,
    raw_session: str,
    raw_csrf: str,
    device: TrustedDevice,
) -> None:
    expires_at = device.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    max_age = max(0, int((expires_at - utcnow()).total_seconds()))
    response.set_cookie(
        SESSION_COOKIE,
        raw_session,
        max_age=max_age,
        httponly=True,
        secure=get_settings().cookie_secure,
        samesite="lax",
        path="/",
    )
    response.set_cookie(
        CSRF_COOKIE,
        raw_csrf,
        max_age=max_age,
        httponly=False,
        secure=get_settings().cookie_secure,
        samesite="strict",
        path="/",
    )


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
    contractor_expiry = user.contractor_access_expires_at if user else None
    if contractor_expiry and contractor_expiry.tzinfo is None:
        contractor_expiry = contractor_expiry.replace(tzinfo=UTC)
    if (
        not user
        or user.status != "ACTIVE"
        or user.auth_epoch != device.auth_epoch
        or not user_has_active_access(db, user.id)
        or (contractor_expiry is not None and contractor_expiry <= now)
    ):
        if device and not device.revoked_at:
            device.revoked_at = now
            db.commit()
        return None
    days = (
        get_settings().trusted_device_days_elevated
        if device.elevated
        else get_settings().trusted_device_days_standard
    )
    created_at = device.created_at
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=UTC)
    absolute_days = (
        get_settings().trusted_device_absolute_days_elevated
        if device.elevated
        else get_settings().trusted_device_absolute_days_standard
    )
    absolute_expiry = created_at + timedelta(days=absolute_days)
    if absolute_expiry <= now:
        device.revoked_at = now
        db.commit()
        return None
    device.last_seen_at = now
    device.expires_at = min(now + timedelta(days=days), absolute_expiry)
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
