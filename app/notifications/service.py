from __future__ import annotations

import base64
import hashlib
import json
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, time, timedelta
from urllib.parse import urlsplit

from cryptography.fernet import Fernet, InvalidToken
from pywebpush import WebPushException, webpush
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.enums import Role
from app.core.time import local_today, utcnow
from app.identity.models import RoleGrant, User, UserPersonLink
from app.notifications.models import (
    NotificationDelivery,
    NotificationEvent,
    NotificationPreference,
    PushSubscription,
)
from app.positions.service import eligibility
from app.rostering.models import Assignment, Workday, WorkdayRevision
from app.rostering.participation import person_day_participation


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
    available_at: datetime | None = None,
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
        available_at=available_at or utcnow(),
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
    fields = {
        "OPEN_POSITION_AVAILABLE": "open_positions",
        "ROSTER_PUBLISHED": "roster_changes",
        "NIGHT_BEFORE": "night_before",
        "TWO_DAYS_BEFORE": "two_days_before",
        "ONE_HOUR_BEFORE": "one_hour_before",
    }
    field = fields.get(event_type)
    return bool(getattr(preference, field)) if field else True


def audience_user_ids(db: Session, event: NotificationEvent) -> set[uuid.UUID]:
    if event.audience_user_id:
        user = db.get(User, event.audience_user_id)
        return (
            {user.id}
            if user
            and user.status == "ACTIVE"
            and _preference_allows(db, user.id, event.event_type)
            else set()
        )
    if event.event_type == "ROSTER_PUBLISHED":
        revision_ids: set[uuid.UUID] = set()
        for key in ("revision_id", "previous_revision_id"):
            value = event.payload.get(key)
            if value:
                try:
                    revision_ids.add(uuid.UUID(str(value)))
                except ValueError:
                    return set()
        if not revision_ids:
            return set()
        person_ids = set(
            db.scalars(
                select(Assignment.person_id).where(
                    Assignment.revision_id.in_(revision_ids),
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
        "NIGHT_BEFORE": "Roster reminder for tomorrow",
        "TWO_DAYS_BEFORE": "Roster reminder in two days",
        "ONE_HOUR_BEFORE": "Roster starts in one hour",
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
    claim_token: str | None = None,
) -> NotificationEvent:
    if claim_token is not None and (
        event.status != "PROCESSING" or event.claim_token != claim_token
    ):
        raise RuntimeError("Notification event claim is no longer owned by this worker.")
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
    retry_at = [
        delivery.next_attempt_at
        for delivery in existing.values()
        if delivery.status in {"PENDING", "RETRY"}
    ]
    if not retry_at:
        event.status = "PROCESSED"
        event.processed_at = utcnow()
    else:
        event.status = "RETRY"
        event.available_at = min(retry_at)
    event.claim_token = None
    event.claimed_at = None
    db.commit()
    return event


def process_pending(
    db: Session,
    limit: int = 50,
    *,
    sender: Callable[[dict[str, object], str], None] = _send_webpush,
) -> int:
    now = utcnow()
    stale_at = now - timedelta(minutes=5)
    events = list(
        db.scalars(
            select(NotificationEvent)
            .where(
                or_(
                    (
                        NotificationEvent.status.in_(["PENDING", "RETRY"])
                        & (NotificationEvent.available_at <= now)
                    ),
                    (
                        (NotificationEvent.status == "PROCESSING")
                        & (NotificationEvent.claimed_at <= stale_at)
                    ),
                )
            )
            .order_by(NotificationEvent.created_at)
            .with_for_update(skip_locked=True)
            .limit(limit)
        )
    )
    claims: list[tuple[NotificationEvent, str]] = []
    for event in events:
        token = str(uuid.uuid4())
        event.status = "PROCESSING"
        event.claim_token = token
        event.claimed_at = now
        claims.append((event, token))
    db.commit()
    for event, token in claims:
        try:
            process_event(db, event, claim_token=token, sender=sender)
        except Exception:
            db.rollback()
            current = db.get(NotificationEvent, event.event_key)
            if current and current.claim_token == token:
                current.status = "RETRY"
                current.claim_token = None
                current.claimed_at = None
                current.available_at = utcnow() + timedelta(minutes=2)
                db.commit()
    return len(events)


def generate_reminders(
    db: Session, *, now: datetime | None = None, horizon_days: int = 14
) -> int:
    """Create deterministic reminder events from authoritative published snapshots."""
    current = now or utcnow()
    if current.tzinfo is None:
        current = current.replace(tzinfo=UTC)
    settings = get_settings()
    today = local_today(current)
    rows = db.execute(
        select(Workday, WorkdayRevision, Assignment)
        .join(WorkdayRevision, Workday.current_published_revision_id == WorkdayRevision.id)
        .join(Assignment, Assignment.revision_id == WorkdayRevision.id)
        .where(
            WorkdayRevision.work_date >= today,
            WorkdayRevision.work_date <= today + timedelta(days=horizon_days),
            Assignment.person_id.is_not(None),
            Assignment.status == "ASSIGNED",
        )
    ).all()
    grouped: dict[tuple[uuid.UUID, uuid.UUID, uuid.UUID], list[Assignment]] = {}
    revisions: dict[tuple[uuid.UUID, uuid.UUID, uuid.UUID], tuple[Workday, WorkdayRevision]] = {}
    for workday, revision, assignment in rows:
        assert assignment.person_id is not None
        key = (workday.id, revision.id, assignment.person_id)
        grouped.setdefault(key, []).append(assignment)
        revisions[key] = (workday, revision)
    person_ids = {key[2] for key in grouped}
    users_by_person = dict(
        db.execute(
            select(UserPersonLink.person_id, UserPersonLink.user_id)
            .join(User, User.id == UserPersonLink.user_id)
            .where(UserPersonLink.person_id.in_(person_ids), User.status == "ACTIVE")
        ).all()
    ) if person_ids else {}
    created = 0
    for key, assignments in grouped.items():
        workday, revision = revisions[key]
        user_id = users_by_person.get(key[2])
        if not user_id:
            continue
        participation = person_day_participation(revision, assignments)
        schedules = {
            "NIGHT_BEFORE": datetime.combine(
                revision.work_date - timedelta(days=1), time(19), tzinfo=settings.timezone
            ).astimezone(UTC),
            "TWO_DAYS_BEFORE": datetime.combine(
                revision.work_date - timedelta(days=2), time(19), tzinfo=settings.timezone
            ).astimezone(UTC),
        }
        if participation.start is not None:
            participation_date = revision.work_date
            if (
                revision.start_time is not None
                and revision.end_time is not None
                and revision.end_time < revision.start_time
                and participation.start < revision.start_time
            ):
                participation_date += timedelta(days=1)
            schedules["ONE_HOUR_BEFORE"] = (
                datetime.combine(
                    participation_date, participation.start, tzinfo=settings.timezone
                )
                - timedelta(hours=1)
            ).astimezone(UTC)
        for event_type, available_at in schedules.items():
            event_key = f"reminder:{event_type}:{revision.id}:{key[2]}"
            if db.get(NotificationEvent, event_key) is None:
                record_event(
                    db,
                    event_key=event_key,
                    event_type=event_type,
                    region_id=workday.region_id,
                    workday_id=workday.id,
                    audience_user_id=user_id,
                    payload={"revision_id": str(revision.id)},
                    available_at=available_at,
                )
                created += 1
    db.commit()
    return created
