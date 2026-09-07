from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.audit.models import AuditEvent

REDACTED_KEYS = {"password", "pin", "secret", "token", "credential_hash", "csrf", "cookie"}


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: "[REDACTED]" if key.lower() in REDACTED_KEYS else redact(item) for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact(item) for item in value]
    return value


def record_audit(
    db: Session,
    action: str,
    target_type: str,
    target_id: object | None,
    actor_user_id: uuid.UUID | None,
    *,
    region_id: uuid.UUID | None = None,
    detail: dict[str, object] | None = None,
) -> AuditEvent:
    event = AuditEvent(
        actor_user_id=actor_user_id,
        action=action,
        target_type=target_type,
        target_id=str(target_id) if target_id is not None else None,
        region_id=region_id,
        detail=redact(detail or {}),
    )
    db.add(event)
    return event
