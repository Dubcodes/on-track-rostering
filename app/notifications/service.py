from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.notifications.models import NotificationEvent


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
