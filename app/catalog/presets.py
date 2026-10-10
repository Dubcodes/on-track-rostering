from __future__ import annotations

import uuid

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.catalog.models import BasePosition, PositionPreset, PositionPresetItem
from app.core.enums import Lifecycle
from app.core.time import utcnow
from app.positions.ordering import catalog_position_order

PRESET_KEYS = ("THOROUGHBRED", "HARNESS", "TRIALS")
FALLBACK_POSITION_NAMES = {
    "THOROUGHBRED": (
        "Side 1", "Side 2", "Start", "Head On", "Back", "Turn", "RTS",
        "Director", "Sound", "VT", "CCU1", "CCU2", "FM", "ENG",
    ),
    "HARNESS": (
        "Side 1", "Side 2", "Head On", "Back", "Director", "Sound/VT",
        "CCU1", "CCU2", "FM", "ENG",
    ),
    "TRIALS": (
        "Side 1", "Side 2", "Head On", "Back", "Director", "Sound/VT", "ENG",
    ),
}


def _active_positions(db: Session) -> list[BasePosition]:
    return sorted(
        db.scalars(
            select(BasePosition).where(BasePosition.lifecycle == Lifecycle.ACTIVE.value)
        ),
        key=catalog_position_order,
    )


def effective_position_presets(
    db: Session, region_ids: list[uuid.UUID]
) -> dict[str, dict[str, list[str]]]:
    positions = _active_positions(db)
    positions_by_id = {row.id: row for row in positions}
    first_position_by_name: dict[str, BasePosition] = {}
    for position in positions:
        first_position_by_name.setdefault(position.name, position)

    persisted = {
        (row.region_id, row.preset_key): row
        for row in db.scalars(
            select(PositionPreset).where(PositionPreset.region_id.in_(region_ids))
        )
    } if region_ids else {}
    preset_ids = [row.id for row in persisted.values()]
    item_ids: dict[uuid.UUID, set[uuid.UUID]] = {preset_id: set() for preset_id in preset_ids}
    if preset_ids:
        for item in db.scalars(
            select(PositionPresetItem).where(PositionPresetItem.preset_id.in_(preset_ids))
        ):
            item_ids[item.preset_id].add(item.base_position_id)

    result: dict[str, dict[str, list[str]]] = {}
    for region_id in region_ids:
        regional: dict[str, list[str]] = {}
        for key in PRESET_KEYS:
            override = persisted.get((region_id, key))
            if override:
                selected = [
                    row for row in positions if row.id in item_ids.get(override.id, set())
                ]
            else:
                selected = [
                    first_position_by_name[name]
                    for name in FALLBACK_POSITION_NAMES[key]
                    if name in first_position_by_name
                ]
                selected.sort(key=catalog_position_order)
            regional[key] = [str(row.id) for row in selected if row.id in positions_by_id]
        result[str(region_id)] = regional
    return result


def save_position_preset(
    db: Session,
    *,
    region_id: uuid.UUID,
    preset_key: str,
    position_ids: list[uuid.UUID],
    actor_user_id: uuid.UUID,
) -> PositionPreset:
    if preset_key not in PRESET_KEYS:
        raise ValueError("Invalid roster preset.")
    unique_ids = set(position_ids)
    active_ids = set(
        db.scalars(
            select(BasePosition.id).where(
                BasePosition.id.in_(unique_ids or {uuid.uuid4()}),
                BasePosition.lifecycle == Lifecycle.ACTIVE.value,
            )
        )
    )
    if active_ids != unique_ids:
        raise ValueError("Roster presets may contain only active base positions.")
    row = db.scalar(
        select(PositionPreset).where(
            PositionPreset.region_id == region_id,
            PositionPreset.preset_key == preset_key,
        )
    )
    if row is None:
        row = PositionPreset(region_id=region_id, preset_key=preset_key)
        db.add(row)
        db.flush()
    db.execute(delete(PositionPresetItem).where(PositionPresetItem.preset_id == row.id))
    for position in _active_positions(db):
        if position.id in unique_ids:
            db.add(PositionPresetItem(preset_id=row.id, base_position_id=position.id))
    row.updated_at = utcnow()
    row.updated_by_user_id = actor_user_id
    return row


def reset_position_preset(
    db: Session, *, region_id: uuid.UUID, preset_key: str
) -> PositionPreset | None:
    if preset_key not in PRESET_KEYS:
        raise ValueError("Invalid roster preset.")
    row = db.scalar(
        select(PositionPreset).where(
            PositionPreset.region_id == region_id,
            PositionPreset.preset_key == preset_key,
        )
    )
    if row:
        db.execute(delete(PositionPresetItem).where(PositionPresetItem.preset_id == row.id))
        db.delete(row)
    return row
