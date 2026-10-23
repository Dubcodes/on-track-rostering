from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.enums import Role
from app.identity.models import RoleGrant

PROTECTED_AUTHORITY_ROLES = {Role.MANAGER.value, Role.ADMIN.value}


def user_has_active_access(db: Session, user_id: uuid.UUID) -> bool:
    """Return whether the account currently has any effective application authority."""
    return bool(
        db.scalar(
            select(RoleGrant.id)
            .where(RoleGrant.user_id == user_id, RoleGrant.status == "ACTIVE")
            .limit(1)
        )
    )


def user_has_protected_authority(db: Session, user_id: uuid.UUID) -> bool:
    """Protect both staged and effective Manager/Admin accounts from regional takeover."""
    return bool(
        db.scalar(
            select(RoleGrant.id)
            .where(
                RoleGrant.user_id == user_id,
                RoleGrant.status.in_(["ACTIVE", "PENDING"]),
                RoleGrant.role.in_(PROTECTED_AUTHORITY_ROLES),
            )
            .limit(1)
        )
    )
