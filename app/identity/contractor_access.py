from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from app.audit.service import record_audit
from app.core.config import get_settings
from app.core.enums import AssignmentStatus, Role, WorkdayStatus
from app.core.time import local_today, utcnow
from app.identity.models import RoleGrant, TrustedDevice, User, UserPersonLink
from app.rostering.models import Assignment, Workday, WorkdayRevision
from app.system_settings.service import operational_settings_for


@dataclass(frozen=True)
class ContractorAccessResult:
    latest_participation_date: date | None
    expires_at: datetime | None
    changed: bool
    status: str


def _aware(value: datetime | None) -> datetime | None:
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def is_contractor_user(db: Session, user_id: uuid.UUID) -> bool:
    return bool(
        db.scalar(
            select(RoleGrant.id)
            .where(
                RoleGrant.user_id == user_id,
                RoleGrant.role == Role.CONTRACTOR.value,
                RoleGrant.status == "ACTIVE",
            )
            .limit(1)
        )
    )


def latest_future_participation_date(
    db: Session, user_id: uuid.UUID, *, today: date | None = None
) -> date | None:
    today = today or local_today()
    return db.scalar(
        select(func.max(WorkdayRevision.work_date))
        .select_from(UserPersonLink)
        .join(Assignment, Assignment.person_id == UserPersonLink.person_id)
        .join(
            WorkdayRevision,
            WorkdayRevision.id == Assignment.revision_id,
        )
        .join(
            Workday,
            and_(
                Workday.current_published_revision_id == WorkdayRevision.id,
                Workday.status == WorkdayStatus.SCHEDULED.value,
            ),
        )
        .where(
            UserPersonLink.user_id == user_id,
            Assignment.status == AssignmentStatus.ASSIGNED.value,
            WorkdayRevision.work_date >= today,
            Workday.generated_from_workday_id.is_(None),
        )
    )


def _revoke_devices(db: Session, user: User, now: datetime) -> None:
    for device in db.scalars(
        select(TrustedDevice).where(
            TrustedDevice.user_id == user.id,
            TrustedDevice.revoked_at.is_(None),
        )
    ):
        device.revoked_at = now


def refresh_contractor_access(
    db: Session,
    user: User,
    *,
    now: datetime | None = None,
    manual_extension_at: datetime | None = None,
    actor_user_id: uuid.UUID | None = None,
    reason: str = "refresh",
) -> ContractorAccessResult:
    now = _aware(now or utcnow())
    assert now is not None
    if not is_contractor_user(db, user.id):
        return ContractorAccessResult(None, user.contractor_access_expires_at, False, user.status)
    settings = operational_settings_for(db)
    inactivity = timedelta(days=settings.contractor_inactivity_days)
    if manual_extension_at is not None:
        supplied = _aware(manual_extension_at)
        current_manual = _aware(user.contractor_manual_extension_at)
        if supplied is not None and (current_manual is None or supplied > current_manual):
            user.contractor_manual_extension_at = supplied
    latest = latest_future_participation_date(
        db, user.id, today=now.astimezone(get_settings().timezone).date()
    )
    candidates: list[datetime] = []
    if latest is not None:
        local_end = datetime.combine(latest, time.max, tzinfo=get_settings().timezone)
        candidates.append(local_end.astimezone(UTC) + inactivity)
    manual = _aware(user.contractor_manual_extension_at)
    if manual is not None:
        candidates.append(manual + inactivity)
    existing = _aware(user.contractor_access_expires_at)
    effective = max([candidate for candidate in candidates if candidate] + ([existing] if existing else []), default=None)
    changed = effective != existing
    if changed:
        user.contractor_access_expires_at = effective
    previous_status = user.status
    if effective is not None and effective <= now and user.status == "ACTIVE":
        user.status = "EXPIRED"
        user.auth_epoch += 1
        _revoke_devices(db, user, now)
        record_audit(
            db,
            "contractor.access.expired",
            "user",
            user.id,
            actor_user_id,
            detail={"expires_at": effective.isoformat(), "reason": reason},
        )
    elif effective is not None and effective > now and user.status == "EXPIRED":
        user.status = "ACTIVE"
        user.auth_epoch += 1
        record_audit(
            db,
            "contractor.access.reactivated",
            "user",
            user.id,
            actor_user_id,
            detail={"expires_at": effective.isoformat(), "reason": reason},
        )
    if changed:
        record_audit(
            db,
            "contractor.access.refreshed",
            "user",
            user.id,
            actor_user_id,
            detail={
                "expires_at": effective.isoformat() if effective else None,
                "latest_participation_date": latest.isoformat() if latest else None,
                "reason": reason,
            },
        )
    return ContractorAccessResult(latest, effective, changed or user.status != previous_status, user.status)


def refresh_all_contractor_access(
    db: Session, *, now: datetime | None = None
) -> dict[str, int]:
    user_ids = set(
        db.scalars(
            select(RoleGrant.user_id).where(
                RoleGrant.role == Role.CONTRACTOR.value,
                RoleGrant.status == "ACTIVE",
            )
        )
    )
    counts = {"checked": 0, "changed": 0, "expired": 0}
    for user_id in user_ids:
        user = db.get(User, user_id)
        if user is None:
            continue
        result = refresh_contractor_access(db, user, now=now, reason="scheduler")
        counts["checked"] += 1
        counts["changed"] += int(result.changed)
        counts["expired"] += int(result.status == "EXPIRED")
    return counts
