from __future__ import annotations

import re
import uuid
from collections.abc import Mapping
from typing import Any

from sqlalchemy.orm import Session

from app.audit.models import AuditEvent

SENSITIVE_KEY = re.compile(
    r"(?:password|(?:^|[_-])pin(?:$|[_-])|token|secret|cookie|session|authorization|"
    r"credential|vapid.*private|encrypted|private[_-]?key|csrf)",
    re.IGNORECASE,
)
MAX_DEPTH = 6
MAX_ITEMS = 40
MAX_STRING = 500


def redact(value: Any, *, _depth: int = 0) -> Any:
    if _depth >= MAX_DEPTH:
        return "[TRUNCATED]"
    if isinstance(value, Mapping):
        result: dict[str, object] = {}
        for index, (raw_key, item) in enumerate(value.items()):
            if index >= MAX_ITEMS:
                result["_truncated"] = True
                break
            key = str(raw_key)
            result[key] = (
                "[REDACTED]" if SENSITIVE_KEY.search(key) else redact(item, _depth=_depth + 1)
            )
        return result
    if isinstance(value, list | tuple | set):
        items = list(value)
        result = [redact(item, _depth=_depth + 1) for item in items[:MAX_ITEMS]]
        if len(items) > MAX_ITEMS:
            result.append("[TRUNCATED]")
        return result
    if isinstance(value, str):
        return value[:MAX_STRING] + ("…" if len(value) > MAX_STRING else "")
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
