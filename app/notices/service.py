from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from sqlalchemy import case, or_, select
from sqlalchemy.orm import Session

from app.core.time import utcnow
from app.notices.models import OperationalNotice


def prominent_notice(
    db: Session, region_ids: set[uuid.UUID], now: datetime | None = None
) -> OperationalNotice | None:
    current = now or utcnow()
    scope_filter = OperationalNotice.scope == "GLOBAL"
    if region_ids:
        scope_filter = or_(
            scope_filter,
            (OperationalNotice.scope == "REGION") & OperationalNotice.region_id.in_(region_ids),
        )
    return db.scalar(
        select(OperationalNotice)
        .where(
            OperationalNotice.starts_at <= current,
            OperationalNotice.expires_at > current,
            scope_filter,
        )
        .order_by(
            case((OperationalNotice.scope == "REGION", 0), else_=1),
            OperationalNotice.starts_at.desc(),
            OperationalNotice.created_at.desc(),
            OperationalNotice.id.desc(),
        )
        .limit(1)
    )


def recent_notices(db: Session, region_ids: set[uuid.UUID], is_admin: bool):
    cutoff = utcnow() - timedelta(days=31)
    scope_filter = OperationalNotice.scope == "GLOBAL"
    if is_admin:
        scope_filter = True
    elif region_ids:
        scope_filter = or_(scope_filter, OperationalNotice.region_id.in_(region_ids))
    return list(
        db.scalars(
            select(OperationalNotice)
            .where(OperationalNotice.created_at >= cutoff, scope_filter)
            .order_by(OperationalNotice.created_at.desc())
        )
    )
