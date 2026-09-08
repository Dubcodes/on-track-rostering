from __future__ import annotations

import json
from types import SimpleNamespace

from pywebpush import WebPushException
from sqlalchemy import select

from app.auth.security import hash_credential
from app.identity.models import User
from app.notifications.models import NotificationDelivery, NotificationEvent, PushSubscription
from app.notifications.service import process_event, save_subscription


def _user(db, status: str = "ACTIVE") -> User:  # type: ignore[no-untyped-def]
    user = User(
        email=f"{status.lower()}@example.test",
        display_name=status.title(),
        credential_hash=hash_credential("123456"),
        credential_kind="pin",
        status=status,
    )
    db.add(user)
    db.commit()
    return user


def _subscription(db, user: User) -> PushSubscription:  # type: ignore[no-untyped-def]
    return save_subscription(
        db,
        user_id=user.id,
        value={
            "endpoint": f"https://push.example.test/{user.id}",
            "keys": {"p256dh": "public-material", "auth": "auth-material"},
        },
        label="Test browser",
    )


def test_delivery_is_encrypted_and_idempotent(db) -> None:  # type: ignore[no-untyped-def]
    user = _user(db)
    subscription = _subscription(db, user)
    assert "push.example.test" not in json.dumps(subscription.encrypted_subscription)
    event = NotificationEvent(
        event_key="direct:1",
        event_type="ROSTER_PUBLISHED",
        audience_user_id=user.id,
        payload={},
    )
    db.add(event)
    db.commit()
    sent: list[str] = []

    process_event(db, event, sender=lambda _subscription, payload: sent.append(payload))
    process_event(db, event, sender=lambda _subscription, payload: sent.append(payload))
    delivery = db.scalar(
        select(NotificationDelivery).where(NotificationDelivery.event_key == event.event_key)
    )
    assert len(sent) == 1
    assert delivery.status == "DELIVERED" and delivery.attempt_count == 1
    assert event.status == "PROCESSED"


def test_permanent_endpoint_failure_deactivates_subscription(db) -> None:  # type: ignore[no-untyped-def]
    user = _user(db)
    subscription = _subscription(db, user)
    event = NotificationEvent(
        event_key="direct:gone",
        event_type="ROSTER_PUBLISHED",
        audience_user_id=user.id,
        payload={},
    )
    db.add(event)
    db.commit()

    def gone(_subscription, _payload):  # type: ignore[no-untyped-def]
        raise WebPushException("gone", response=SimpleNamespace(status_code=410))

    process_event(db, event, sender=gone)
    db.refresh(subscription)
    delivery = db.scalar(
        select(NotificationDelivery).where(NotificationDelivery.event_key == event.event_key)
    )
    assert subscription.active is False
    assert delivery.status == "PERMANENT_FAILURE"
    assert event.status == "PROCESSED"


def test_transient_failure_stays_retryable_and_disabled_user_is_skipped(db) -> None:  # type: ignore[no-untyped-def]
    user = _user(db)
    _subscription(db, user)
    event = NotificationEvent(
        event_key="direct:retry",
        event_type="ROSTER_PUBLISHED",
        audience_user_id=user.id,
        payload={},
    )
    db.add(event)
    db.commit()
    process_event(db, event, sender=lambda *_: (_ for _ in ()).throw(RuntimeError("offline")))
    delivery = db.scalar(
        select(NotificationDelivery).where(NotificationDelivery.event_key == event.event_key)
    )
    assert delivery.status == "RETRY" and event.status == "RETRY"

    disabled = _user(db, "DISABLED")
    _subscription(db, disabled)
    disabled_event = NotificationEvent(
        event_key="direct:disabled",
        event_type="ROSTER_PUBLISHED",
        audience_user_id=disabled.id,
        payload={},
    )
    db.add(disabled_event)
    db.commit()
    process_event(db, disabled_event, sender=lambda *_: (_ for _ in ()).throw(AssertionError()))
    assert disabled_event.status == "PROCESSED"
