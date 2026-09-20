from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from sqlalchemy import case, or_, select
from sqlalchemy.orm import Session

from app.auth.policy import Actor
from app.catalog.models import Region
from app.core.enums import Role
from app.core.time import utcnow
from app.identity.models import RoleGrant, User, UserPersonLink
from app.notices.models import OperationalNotice
from app.rostering.models import Assignment, Workday


def relevant_notice_region_ids(db: Session, actor: Actor) -> set[uuid.UUID]:
    """Use the same grant/assignment relevance for notice banners and push."""
    if actor.is_admin:
        return set(db.scalars(select(Region.id)))
    region_ids = {
        region_id
        for region_id, roles in actor.regional_roles.items()
        if set(roles) - {Role.CONTRACTOR.value}
    }
    if actor.person_id:
        region_ids.update(
            db.scalars(
                select(Workday.region_id)
                .join(Assignment, Assignment.revision_id == Workday.current_published_revision_id)
                .where(Assignment.person_id == actor.person_id, Assignment.status == "ASSIGNED")
            )
        )
    return region_ids


def relevant_notice_user_ids(db: Session, region_id: uuid.UUID) -> set[uuid.UUID]:
    granted = set(
        db.scalars(
            select(RoleGrant.user_id)
            .join(User, User.id == RoleGrant.user_id)
            .where(
                User.status == "ACTIVE",
                RoleGrant.status == "ACTIVE",
                (
                    ((RoleGrant.region_id == region_id) & (RoleGrant.role != Role.CONTRACTOR.value))
                    | ((RoleGrant.region_id.is_(None)) & (RoleGrant.role == Role.ADMIN.value))
                ),
            )
        )
    )
    assigned = set(
        db.scalars(
            select(UserPersonLink.user_id)
            .join(User, User.id == UserPersonLink.user_id)
            .join(Assignment, Assignment.person_id == UserPersonLink.person_id)
            .join(Workday, Workday.current_published_revision_id == Assignment.revision_id)
            .where(
                User.status == "ACTIVE",
                Workday.region_id == region_id,
                Assignment.status == "ASSIGNED",
            )
        )
    )
    return granted | assigned


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
