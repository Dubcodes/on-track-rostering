from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.time import utcnow
from app.identity.models import Invitation, LoginThrottle, TrustedDevice, WebAuthnChallenge
from app.notifications.models import NotificationDelivery, NotificationEvent


@dataclass(frozen=True)
class HousekeepingResult:
    cutoff: datetime
    counts: dict[str, int]
    applied: bool


def terminal_housekeeping(
    db: Session,
    *,
    retention_days: int,
    apply: bool = False,
    now: datetime | None = None,
) -> HousekeepingResult:
    cutoff = (now or utcnow()) - timedelta(days=retention_days)
    rows: dict[str, list[object]] = {
        "webauthn_challenges": list(
            db.scalars(select(WebAuthnChallenge).where(WebAuthnChallenge.expires_at < cutoff))
        ),
        "login_throttles": list(db.scalars(select(LoginThrottle).where(LoginThrottle.updated_at < cutoff))),
        "trusted_devices": list(
            db.scalars(
                select(TrustedDevice).where(
                    (TrustedDevice.expires_at < cutoff)
                    | (TrustedDevice.revoked_at.is_not(None) & (TrustedDevice.revoked_at < cutoff))
                )
            )
        ),
        "invitations": list(
            db.scalars(
                select(Invitation).where(Invitation.expires_at < cutoff)
            )
        ),
    }
    terminal_events = list(
        db.scalars(
            select(NotificationEvent).where(
                NotificationEvent.status == "PROCESSED",
                NotificationEvent.processed_at < cutoff,
            )
        )
    )
    safe_events = []
    for event in terminal_events:
        delivery_states = set(
            db.scalars(
                select(NotificationDelivery.status).where(NotificationDelivery.event_key == event.event_key)
            )
        )
        if delivery_states <= {"DELIVERED", "PERMANENT_FAILURE", "FAILED"}:
            safe_events.append(event)
    rows["notification_events"] = safe_events
    counts = {name: len(items) for name, items in rows.items()}
    if apply:
        for items in rows.values():
            for item in items:
                db.delete(item)
        db.commit()
    return HousekeepingResult(cutoff=cutoff, counts=counts, applied=apply)
