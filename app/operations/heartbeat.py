from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session, sessionmaker

from app.core.database import SessionLocal
from app.core.time import utcnow
from app.operations.models import SchedulerHeartbeat


@dataclass(frozen=True)
class SchedulerHealth:
    status: str
    label: str
    last_completed_at: datetime | None
    notifications_status: str | None
    last_error: str | None


def _aware(value: datetime | None) -> datetime | None:
    if value and value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def _row(db: Session) -> SchedulerHeartbeat:
    row = db.get(SchedulerHeartbeat, 1)
    if row is None:
        row = SchedulerHeartbeat(id=1, last_status="NEVER")
        db.add(row)
    return row


def record_scheduler_started(db: Session, *, now: datetime | None = None) -> None:
    row = _row(db)
    row.last_started_at = now or utcnow()
    row.updated_at = now or utcnow()
    db.commit()


def record_scheduler_completed(
    db: Session, result: dict[str, object], *, now: datetime | None = None
) -> None:
    instant = now or utcnow()
    row = _row(db)
    notifications = result.get("notifications")
    row.last_completed_at = instant
    row.last_status = "OK"
    row.last_error = None
    row.notifications_status = (
        str(notifications.get("status", ""))[:16]
        if isinstance(notifications, dict)
        else None
    )
    row.updated_at = instant
    db.commit()


def sanitized_scheduler_error(exc: Exception) -> str:
    message = " ".join(str(exc).split())
    message = re.sub(r"(?i)(password|token|secret|key)=\S+", r"\1=[REDACTED]", message)
    message = re.sub(r"(?i)://[^/@\s]+@", "://[REDACTED]@", message)
    return f"{type(exc).__name__}: {message}"[:400]


def record_scheduler_failed(
    db: Session, exc: Exception, *, now: datetime | None = None
) -> None:
    instant = now or utcnow()
    row = _row(db)
    row.last_failed_at = instant
    row.last_status = "ERROR"
    row.last_error = sanitized_scheduler_error(exc)
    row.updated_at = instant
    db.commit()


def scheduler_health(
    db: Session,
    *,
    interval_seconds: int,
    now: datetime | None = None,
) -> SchedulerHealth:
    row = db.get(SchedulerHeartbeat, 1)
    if row is None or row.last_completed_at is None:
        label = "Error" if row and row.last_status == "ERROR" else "Never run"
        return SchedulerHealth(
            status="error" if label == "Error" else "never",
            label=label,
            last_completed_at=None,
            notifications_status=row.notifications_status if row else None,
            last_error=row.last_error if row else None,
        )
    completed = _aware(row.last_completed_at)
    failed = _aware(row.last_failed_at)
    instant = now or utcnow()
    if row.last_status == "ERROR" and (failed is None or failed >= completed):
        return SchedulerHealth(
            "error", "Error", completed, row.notifications_status, row.last_error
        )
    tolerance = timedelta(seconds=max(interval_seconds * 3, 15 * 60))
    if completed < instant - tolerance:
        return SchedulerHealth(
            "stale", "Stale", completed, row.notifications_status, row.last_error
        )
    return SchedulerHealth(
        "ok", "Healthy", completed, row.notifications_status, row.last_error
    )


def _write_heartbeat(
    action, *, session_factory: sessionmaker, logger, **kwargs: object
) -> None:  # type: ignore[no-untyped-def]
    try:
        with session_factory() as db:
            action(db, **kwargs)
    except Exception:
        logger.exception("scheduler_heartbeat_write_failed")


def run_scheduler_iteration(
    tick,
    *,
    session_factory: sessionmaker | None = None,
    logger,
    now: datetime | None = None,
) -> dict[str, object]:  # type: ignore[no-untyped-def]
    factory = session_factory or SessionLocal
    instant = now or utcnow()
    _write_heartbeat(
        record_scheduler_started,
        session_factory=factory,
        logger=logger,
        now=instant,
    )
    try:
        with factory() as db:
            result = tick(db, now=instant)
    except Exception as exc:
        _write_heartbeat(
            record_scheduler_failed,
            session_factory=factory,
            logger=logger,
            exc=exc,
            now=utcnow(),
        )
        raise
    _write_heartbeat(
        record_scheduler_completed,
        session_factory=factory,
        logger=logger,
        result=result,
        now=utcnow(),
    )
    return result
