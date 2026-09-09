from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.enums import CapabilitySignal
from app.rostering.models import PositionCapability

POSITIVE_SIGNALS = {
    CapabilitySignal.WORKED.value,
    CapabilitySignal.EMPLOYEE_ALLOW.value,
    CapabilitySignal.MANAGER_ALLOW.value,
}


def eligibility(db: Session, person_id: uuid.UUID, base_position_id: uuid.UUID) -> tuple[bool, str]:
    signals = set(
        db.scalars(
            select(PositionCapability.signal).where(
                PositionCapability.person_id == person_id,
                PositionCapability.base_position_id == base_position_id,
            )
        )
    )
    if CapabilitySignal.MANAGER_BLOCK.value in signals:
        return False, "Manager restricted"
    if CapabilitySignal.EMPLOYEE_OPT_OUT.value in signals:
        return False, "Employee opted out"
    if signals & POSITIVE_SIGNALS:
        return True, "Worked before" if CapabilitySignal.WORKED.value in signals else "Allowed"
    return False, "No capability signal"


def set_signal(
    db: Session,
    person_id: uuid.UUID,
    base_position_id: uuid.UUID,
    signal: str,
    actor_user_id: uuid.UUID,
) -> PositionCapability | None:
    existing = db.scalar(
        select(PositionCapability).where(
            PositionCapability.person_id == person_id,
            PositionCapability.base_position_id == base_position_id,
            PositionCapability.signal == signal,
        )
    )
    if existing:
        return existing
    row = PositionCapability(
        person_id=person_id,
        base_position_id=base_position_id,
        signal=signal,
        changed_by_user_id=actor_user_id,
    )
    db.add(row)
    return row


def set_preference_signal(
    db: Session,
    person_id: uuid.UUID,
    base_position_id: uuid.UUID,
    signal: str,
    actor_user_id: uuid.UUID,
    *,
    family: str,
) -> PositionCapability | None:
    families = {
        CapabilitySignal.EMPLOYEE_ALLOW.value: {
            CapabilitySignal.EMPLOYEE_ALLOW.value,
            CapabilitySignal.EMPLOYEE_OPT_OUT.value,
        },
        CapabilitySignal.EMPLOYEE_OPT_OUT.value: {
            CapabilitySignal.EMPLOYEE_ALLOW.value,
            CapabilitySignal.EMPLOYEE_OPT_OUT.value,
        },
        CapabilitySignal.MANAGER_ALLOW.value: {
            CapabilitySignal.MANAGER_ALLOW.value,
            CapabilitySignal.MANAGER_BLOCK.value,
        },
        CapabilitySignal.MANAGER_BLOCK.value: {
            CapabilitySignal.MANAGER_ALLOW.value,
            CapabilitySignal.MANAGER_BLOCK.value,
        },
    }
    if family not in {"employee", "manager"}:
        raise ValueError("Invalid capability preference family.")
    family_signals = (
        {
            CapabilitySignal.EMPLOYEE_ALLOW.value,
            CapabilitySignal.EMPLOYEE_OPT_OUT.value,
        }
        if family == "employee"
        else {
            CapabilitySignal.MANAGER_ALLOW.value,
            CapabilitySignal.MANAGER_BLOCK.value,
        }
    )
    if signal == "CLEAR":
        db.query(PositionCapability).filter(
            PositionCapability.person_id == person_id,
            PositionCapability.base_position_id == base_position_id,
            PositionCapability.signal.in_(family_signals),
        ).delete(synchronize_session=False)
        return None
    if signal not in families or signal not in family_signals:
        raise ValueError("Invalid capability preference signal.")
    db.query(PositionCapability).filter(
        PositionCapability.person_id == person_id,
        PositionCapability.base_position_id == base_position_id,
        PositionCapability.signal.in_(families[signal]),
    ).delete(synchronize_session=False)
    return set_signal(db, person_id, base_position_id, signal, actor_user_id)
