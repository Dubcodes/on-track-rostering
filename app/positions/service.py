from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.enums import CapabilitySignal
from app.rostering.models import PositionCapability


def _eligibility_from_signals(signals: set[str]) -> tuple[bool, str]:
    if CapabilitySignal.MANAGER_BLOCK.value in signals:
        return False, "Manager restricted"
    if CapabilitySignal.EMPLOYEE_OPT_OUT.value in signals:
        return False, "Employee opted out"
    if signals & {
        CapabilitySignal.EMPLOYEE_ALLOW.value,
        CapabilitySignal.MANAGER_ALLOW.value,
    }:
        return True, "Allowed"
    if CapabilitySignal.WORKED.value in signals:
        return True, "Worked before"
    return False, "No capability signal"


def eligibility(db: Session, person_id: uuid.UUID, base_position_id: uuid.UUID) -> tuple[bool, str]:
    signals = set(
        db.scalars(
            select(PositionCapability.signal).where(
                PositionCapability.person_id == person_id,
                PositionCapability.base_position_id == base_position_id,
            )
        )
    )
    return _eligibility_from_signals(signals)


def bulk_eligibility(
    db: Session,
    person_ids: set[uuid.UUID],
    base_position_ids: set[uuid.UUID],
) -> dict[tuple[uuid.UUID, uuid.UUID], tuple[bool, str]]:
    """Load capability signals once, then evaluate every requested pair in memory."""
    if not person_ids or not base_position_ids:
        return {}
    signals_by_pair: dict[tuple[uuid.UUID, uuid.UUID], set[str]] = {}
    for person_id, position_id, signal in db.execute(
        select(
            PositionCapability.person_id,
            PositionCapability.base_position_id,
            PositionCapability.signal,
        ).where(
            PositionCapability.person_id.in_(person_ids),
            PositionCapability.base_position_id.in_(base_position_ids),
        )
    ):
        signals_by_pair.setdefault((person_id, position_id), set()).add(signal)
    return {
        (person_id, position_id): _eligibility_from_signals(
            signals_by_pair.get((person_id, position_id), set())
        )
        for person_id in person_ids
        for position_id in base_position_ids
    }


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
