from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.core.time import utcnow
from app.system_settings.models import SystemSettings


@dataclass(frozen=True)
class OperationalSettings:
    public_signup_enabled: bool = False


DEFAULT_OPERATIONAL_SETTINGS = OperationalSettings()


def operational_settings_for(db: Session) -> OperationalSettings:
    row = db.get(SystemSettings, 1)
    return (
        OperationalSettings(public_signup_enabled=row.public_signup_enabled)
        if row
        else DEFAULT_OPERATIONAL_SETTINGS
    )


def update_operational_settings(
    db: Session, *, public_signup_enabled: bool, actor_user_id: uuid.UUID
) -> SystemSettings:
    row = db.get(SystemSettings, 1)
    if row is None:
        row = SystemSettings(id=1)
        db.add(row)
    row.public_signup_enabled = public_signup_enabled
    row.updated_at = utcnow()
    row.updated_by_user_id = actor_user_id
    return row
