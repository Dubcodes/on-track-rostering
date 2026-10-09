from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.core.time import utcnow
from app.system_settings.models import SystemSettings


@dataclass(frozen=True)
class OperationalSettings:
    public_signup_enabled: bool = False
    contractor_inactivity_days: int = 30


DEFAULT_OPERATIONAL_SETTINGS = OperationalSettings()


def operational_settings_for(db: Session) -> OperationalSettings:
    row = db.get(SystemSettings, 1)
    return (
        OperationalSettings(
            public_signup_enabled=row.public_signup_enabled,
            contractor_inactivity_days=row.contractor_inactivity_days,
        )
        if row
        else DEFAULT_OPERATIONAL_SETTINGS
    )


def update_operational_settings(
    db: Session,
    *,
    public_signup_enabled: bool,
    contractor_inactivity_days: int,
    actor_user_id: uuid.UUID,
) -> SystemSettings:
    if not 1 <= contractor_inactivity_days <= 365:
        raise ValueError("Contractor inactivity must be between 1 and 365 days.")
    row = db.get(SystemSettings, 1)
    if row is None:
        row = SystemSettings(id=1)
        db.add(row)
    row.public_signup_enabled = public_signup_enabled
    row.contractor_inactivity_days = contractor_inactivity_days
    row.updated_at = utcnow()
    row.updated_by_user_id = actor_user_id
    return row
