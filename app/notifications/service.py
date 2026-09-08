from __future__ import annotations

import base64
import hashlib
import json
import uuid
from collections.abc import Callable
from datetime import UTC, timedelta
from urllib.parse import urlsplit

from cryptography.fernet import Fernet, InvalidToken
from pywebpush import WebPushException, webpush
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.enums import Role
from app.core.time import utcnow
from app.identity.models import RoleGrant, User, UserPersonLink
from app.notifications.models import (
    NotificationDelivery,
    NotificationEvent,
    NotificationPreference,
    PushSubscription,
)
from app.positions.service import eligibility
from app.rostering.models import Assignment


def record_event(
    db: Session,
    *,
    event_key: str,
    event_type: str,
    region_id: uuid.UUID | None,
    workday_id: uuid.UUID | None,
    slot_key: uuid.UUID | None = None,
    audience_user_id: uuid.UUID | None = None,
    payload: dict[str, object] | None = None,
) -> NotificationEvent:
    existing = db.get(NotificationEvent, event_key)
    if existing:
        return existing
    event = NotificationEvent(
        event_key=event_key,
        event_type=event_type,
        region_id=region_id,
        workday_id=workday_id,
        assignment_slot_key=slot_key,
        audience_user_id=audience_user_id,
        payload=payload or {},
    )
    db.add(event)
    return event


def _subscription_cipher() -> Fernet:
    digest = hashlib.sha256(("ontrack-webpush|" + get_settings().secret_key).encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_subscription(value: dict[str, object]) -> dict[str, object]:
    return {"ciphertext": _subscription_cipher().encrypt(json.dumps(value).encode()).decode()}


def decrypt_subscription(value: dict[str, object]) -> dict[str, object]:
    ciphertext = value.get("ciphertext")
    if not isinstance(ciphertext, str):
        raise ValueError("Push subscription is not encrypted.")
    try:
        decoded = json.loads(_subscription_cipher().decrypt(ciphertext.encode()))
    except (InvalidToken, json.JSONDecodeError) as exc:
        raise ValueError("Push subscription cannot be decrypted.") from exc
    if not isinstance(decoded, dict):
        raise ValueError("Push subscription is malformed.")
    return decoded


def save_subscription(
    db: Session, *, user_id: uuid.UUID, value: dict[str, object], label: str
) -> PushSubscription:
    endpoint = value.get("endpoint")
    keys = value.get("keys")
    if (
        not isinstance(endpoint, str)
        or urlsplit(endpoint).scheme != "https"
        or not isinstance(keys, dict)
        or not isinstance(keys.get("p256dh"), str)
        or not isinstance(keys.get("auth"), str)
    ):
        raise ValueError("Malformed push subscription.")
    endpoint_hash = hashlib.sha256(endpoint.encode()).hexdigest()
    row = db.scalar(
        select(PushSubscription).where(PushSubscription.endpoint_hash == endpoint_hash)
    )
    if row and row.user_id != user_id:
        raise ValueError("That push endpoint belongs to another account.")
    if row is None:
        row = PushSubscription(user_id=user_id, endpoint_hash=endpoint_hash)
        db.add(row)
    row.encrypted_subscription = encrypt_subscription(value)
    row.device_label = label.strip()[:120] or "Browser"
    row.active = True
    db.commit()
    return row


def _preference_allows(db: Session, user_id: uuid.UUID, event_type: str) -> bool:
    preference = db.get(NotificationPreference, user_id)
    if not preference:
        return True
    return preference.open_positions if event_type == "OPEN_POSITION_AVAILABLE" else preference.roster_changes


def audience_user_ids(db: Session, event: NotificationEvent) -> set[uuid.UUID]:
    if event.audience_user_id:
        user = db.get(User, event.audience_user_id)
        return {user.id} if user and user.status == "ACTIVE" else set()
    if event.event_type == "ROSTER_PUBLISHED":
        try:
            revision_id = uuid.UUID(str(event.payload["revision_id"]))
        except (KeyError, ValueError):
            return set()
        person_ids = set(
            db.scalars(
                select(Assignment.person_id).where(
                    Assignment.revision_id == revision_id,
                    Assignment.person_id.is_not(None),
                )
            )
        )
        return {
            user_id
            for user_id in db.scalars(
                select(UserPersonLink.user_id)
                .join(User, User.id == UserPersonLink.user_id)
                .where(UserPersonLink.person_id.in_(person_ids), User.status == "ACTIVE")
            )
            if _preference_allows(db, user_id, event.event_type)
        }
    if event.event_type == "MANAGER_ACTION_REQUIRED" and event.region_id:
        return set(
            db.scalars(
                select(RoleGrant.user_id)
                .join(User, User.id == RoleGrant.user_id)
                .where(
                    RoleGrant.region_id == event.region_id,
                    RoleGrant.role.in_([Role.MANAGER.value, Role.SUB_MANAGER.value]),
                    RoleGrant.status == "ACTIVE",
                    User.status == "ACTIVE",
                )
            )
        )
    if event.event_type == "OPEN_POSITION_AVAILABLE" and event.region_id:
        try:
            position_id = uuid.UUID(str(event.payload["base_position_id"]))
        except (KeyError, ValueError):
            return set()
        candidates = db.execute(
            select(User.id, UserPersonLink.person_id)
            .join(UserPersonLink, UserPersonLink.user_id == User.id)
            .join(RoleGrant, RoleGrant.user_id == User.id)
            .where(
                User.status == "ACTIVE",
                RoleGrant.status == "ACTIVE",
                RoleGrant.role == Role.EMPLOYEE.value,
                RoleGrant.region_id == event.region_id,
            )
        ).all()
        return {
            user_id
            for user_id, person_id in candidates
            if _preference_allows(db, user_id, event.event_type)
            and eligibility(db, person_id, position_id)[0]
        }
    return set()


def _notification_payload(event: NotificationEvent) -> dict[str, str]:
    titles = {
        "ROSTER_PUBLISHED": "Roster updated",
        "OPEN_POSITION_AVAILABLE": "Open position",
        "MANAGER_ACTION_REQUIRED": "Roster needs attention",
    }
    return {
        "title": titles.get(event.event_type, "On Track update"),
        "body": "Open On Track to view the authoritative roster details.",
        "url": f"/day/{event.workday_id}" if event.workday_id else "/month",
        "event_key": event.event_key,
    }


def _send_webpush(subscription: dict[str, object], payload: str) -> None:
    settings = get_settings()
    if not settings.vapid_private_key:
        raise RuntimeError("VAPID private key is not configured")
    webpush(
        subscription_info=subscription,
        data=payload,
        vapid_private_key=settings.vapid_private_key,
        vapid_claims={"sub": settings.vapid_subject},
        timeout=10,
    )


def process_event(
    db: Session,
    event: NotificationEvent,
    *,
    sender: Callable[[dict[str, object], str], None] = _send_webpush,
) -> NotificationEvent:
    user_ids = audience_user_ids(db, event)
    subscriptions = list(
        db.scalars(
            select(PushSubscription).where(
                PushSubscription.user_id.in_(user_ids), PushSubscription.active.is_(True)
            )
        )
    ) if user_ids else []
    existing = {
        row.subscription_id: row
        for row in db.scalars(
            select(NotificationDelivery).where(NotificationDelivery.event_key == event.event_key)
        )
    }
    for subscription in subscriptions:
        if subscription.id not in existing:
            delivery = NotificationDelivery(
                event_key=event.event_key,
                subscription_id=subscription.id,
            )
            db.add(delivery)
            existing[subscription.id] = delivery
    db.commit()

    now = utcnow()
    payload = json.dumps(_notification_payload(event), separators=(",", ":"))
    terminal = {"DELIVERED", "PERMANENT_FAILURE", "FAILED"}
    for delivery in existing.values():
        next_attempt = delivery.next_attempt_at
        if next_attempt.tzinfo is None:
            next_attempt = next_attempt.replace(tzinfo=UTC)
        if delivery.status in terminal or next_attempt > now:
            continue
        subscription = db.get(PushSubscription, delivery.subscription_id)
        if not subscription or not subscription.active:
            delivery.status = "PERMANENT_FAILURE"
            continue
        delivery.attempt_count += 1
        try:
            sender(decrypt_subscription(subscription.encrypted_subscription), payload)
        except WebPushException as exc:
            http_status = exc.response.status_code if exc.response is not None else None
            delivery.last_http_status = http_status
            delivery.last_error = "WebPush rejected delivery" if http_status else "WebPush transport failure"
            if http_status in {404, 410}:
                subscription.active = False
                delivery.status = "PERMANENT_FAILURE"
            elif delivery.attempt_count >= get_settings().notification_max_attempts:
                delivery.status = "FAILED"
            else:
                delivery.status = "RETRY"
                delivery.next_attempt_at = now + timedelta(minutes=2 ** delivery.attempt_count)
        except (RuntimeError, ValueError):
            delivery.last_error = "Push delivery configuration failure"
            delivery.status = (
                "FAILED"
                if delivery.attempt_count >= get_settings().notification_max_attempts
                else "RETRY"
            )
            delivery.next_attempt_at = now + timedelta(minutes=2 ** delivery.attempt_count)
        else:
            delivery.status = "DELIVERED"
            delivery.delivered_at = now
            delivery.last_error = ""
    db.commit()
    statuses = {delivery.status for delivery in existing.values()}
    if not statuses or statuses <= terminal:
        event.status = "PROCESSED"
        event.processed_at = utcnow()
    else:
        event.status = "RETRY"
        event.available_at = min(
            delivery.next_attempt_at for delivery in existing.values() if delivery.status == "RETRY"
        )
    db.commit()
    return event


def process_pending(db: Session, limit: int = 50) -> int:
    events = list(
        db.scalars(
            select(NotificationEvent)
            .where(
                NotificationEvent.status.in_(["PENDING", "RETRY"]),
                NotificationEvent.available_at <= utcnow(),
            )
            .order_by(NotificationEvent.created_at)
            .limit(limit)
        )
    )
    for event in events:
        process_event(db, event)
    return len(events)
