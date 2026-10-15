from __future__ import annotations

import uuid

from sqlalchemy import distinct, func, select
from sqlalchemy.orm import Session

from app.catalog.models import BasePosition
from app.core.enums import AssignmentStatus, CapabilitySignal, WorkdayStatus
from app.positions.canonical import canonical_position_identity
from app.rostering.models import Assignment, PositionCapability, Workday


def _eligibility_from_signals(
    signals: set[str], *, worked_qualified: bool = True
) -> tuple[bool, str]:
    if CapabilitySignal.MANAGER_BLOCK.value in signals:
        return False, "Manager restricted"
    if CapabilitySignal.EMPLOYEE_OPT_OUT.value in signals:
        return False, "Employee opted out"
    if signals & {
        CapabilitySignal.EMPLOYEE_ALLOW.value,
        CapabilitySignal.MANAGER_ALLOW.value,
    }:
        return True, "Allowed"
    if CapabilitySignal.WORKED.value in signals and worked_qualified:
        return True, "Worked before"
    return False, "No capability signal"


def eligibility(db: Session, person_id: uuid.UUID, base_position_id: uuid.UUID) -> tuple[bool, str]:
    return bulk_eligibility(db, {person_id}, {base_position_id})[(person_id, base_position_id)]


def bulk_eligibility(
    db: Session,
    person_ids: set[uuid.UUID],
    base_position_ids: set[uuid.UUID],
) -> dict[tuple[uuid.UUID, uuid.UUID], tuple[bool, str]]:
    """Load capability signals once, then evaluate every requested pair in memory."""
    if not person_ids or not base_position_ids:
        return {}
    positions = list(db.scalars(select(BasePosition)))
    key_by_position = {
        position.id: canonical_position_identity(position.name).key for position in positions
    }
    requested_keys = {
        key_by_position[position_id]
        for position_id in base_position_ids
        if position_id in key_by_position
    }
    relevant_position_ids = {
        position_id
        for position_id, key in key_by_position.items()
        if key in requested_keys
    }
    signals_by_key: dict[tuple[uuid.UUID, str], set[str]] = {}
    sourced_worked_keys: set[tuple[uuid.UUID, str]] = set()
    legacy_worked_keys: set[tuple[uuid.UUID, str]] = set()
    for person_id, position_id, signal, source_revision_id in db.execute(
        select(
            PositionCapability.person_id,
            PositionCapability.base_position_id,
            PositionCapability.signal,
            PositionCapability.source_revision_id,
        ).where(
            PositionCapability.person_id.in_(person_ids),
            PositionCapability.base_position_id.in_(relevant_position_ids),
        )
    ):
        key = (person_id, key_by_position[position_id])
        signals_by_key.setdefault(key, set()).add(signal)
        if signal == CapabilitySignal.WORKED.value:
            (sourced_worked_keys if source_revision_id else legacy_worked_keys).add(key)
    worked_days: dict[tuple[uuid.UUID, str], set[uuid.UUID]] = {}
    if sourced_worked_keys:
        for person_id, workday_id, position_id in db.execute(
            select(Assignment.person_id, Workday.id, Assignment.base_position_id)
            .join(Workday, Assignment.revision_id == Workday.current_published_revision_id)
            .where(
                Workday.status == WorkdayStatus.SCHEDULED.value,
                Assignment.person_id.in_(person_ids),
                Assignment.status == AssignmentStatus.ASSIGNED.value,
                Assignment.base_position_id.in_(relevant_position_ids),
            )
            .distinct()
        ):
            worked_days.setdefault((person_id, key_by_position[position_id]), set()).add(workday_id)
    return {
        (person_id, position_id): _eligibility_from_signals(
            signals_by_key.get((person_id, key_by_position[position_id]), set()),
            worked_qualified=(
                (person_id, key_by_position[position_id]) in legacy_worked_keys
                or len(worked_days.get((person_id, key_by_position[position_id]), set())) >= 2
            ),
        )
        for person_id in person_ids
        for position_id in base_position_ids
        if position_id in key_by_position
    }


def infer_worked_capability(
    db: Session,
    person_id: uuid.UUID,
    base_position_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    source_revision_id: uuid.UUID,
) -> bool:
    """Record raw work history and report two-day inferred eligibility.

    Historical aliases share one canonical identity. Multiple slots on the same
    Workday count once, and repeated publication remains idempotent.
    """
    position = db.get(BasePosition, base_position_id)
    if not position:
        return False
    identity = canonical_position_identity(position.name).key
    canonical_ids = {
        row.id
        for row in db.scalars(select(BasePosition))
        if canonical_position_identity(row.name).key == identity
    }
    worked_days = db.scalar(
        select(func.count(distinct(Workday.id)))
        .join(Assignment, Assignment.revision_id == Workday.current_published_revision_id)
        .where(
            Workday.status == WorkdayStatus.SCHEDULED.value,
            Assignment.person_id == person_id,
            Assignment.status == AssignmentStatus.ASSIGNED.value,
            Assignment.base_position_id.in_(canonical_ids),
        )
    ) or 0
    existing = db.scalar(
        select(PositionCapability).where(
            PositionCapability.person_id == person_id,
            PositionCapability.base_position_id == base_position_id,
            PositionCapability.signal == CapabilitySignal.WORKED.value,
        )
    )
    if existing is None:
        existing = set_signal(
            db,
            person_id,
            base_position_id,
            CapabilitySignal.WORKED.value,
            actor_user_id,
        )
        existing.source_revision_id = source_revision_id
    return worked_days >= 2


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
