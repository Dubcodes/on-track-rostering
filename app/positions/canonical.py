from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.catalog.models import BasePosition, CrewGroup, PositionPresetItem
from app.catalog.service import normalized_name
from app.rostering.models import Assignment, PositionCapability, WorkdayRevision

POSITION_ALIASES = {
    "dir": "Director",
    "eng": "Engineer",
    "soundvt": "Sound/VT",
    "sound vt": "Sound/VT",
    "back2": "Back 2",
}


class AmbiguousPositionError(ValueError):
    pass


@dataclass(frozen=True)
class CanonicalPositionIdentity:
    key: str
    canonical_name: str
    display_name: str
    slot_index: int | None = None


@dataclass
class PositionConvergenceRow:
    identity: CanonicalPositionIdentity
    canonical: BasePosition
    duplicates: list[BasePosition]
    group_names: dict[uuid.UUID | None, str]
    capability_references: int
    preset_references: int
    published_assignment_references: int


@dataclass
class PositionConvergencePlan:
    rows: list[PositionConvergenceRow] = field(default_factory=list)

    @property
    def duplicate_count(self) -> int:
        return sum(len(row.duplicates) for row in self.rows)

    @property
    def capability_references(self) -> int:
        return sum(row.capability_references for row in self.rows)

    @property
    def preset_references(self) -> int:
        return sum(row.preset_references for row in self.rows)

    @property
    def published_assignment_references(self) -> int:
        return sum(row.published_assignment_references for row in self.rows)


def canonical_position_identity(value: str) -> CanonicalPositionIdentity:
    clean = " ".join(value.split())
    key = normalized_name(clean)
    ccu_match = re.fullmatch(r"ccu\s*(\d+)", key)
    if ccu_match:
        slot_index = int(ccu_match.group(1))
        return CanonicalPositionIdentity("ccu", "CCU", f"CCU {slot_index}", slot_index)
    canonical_name = POSITION_ALIASES.get(key, clean)
    canonical_key = normalized_name(canonical_name)
    return CanonicalPositionIdentity(canonical_key, canonical_name, canonical_name)


def active_position_matches(
    db: Session,
    value: str,
    *,
    positions: list[BasePosition] | None = None,
) -> list[BasePosition]:
    identity = canonical_position_identity(value)
    rows = positions if positions is not None else list(
        db.scalars(select(BasePosition).where(BasePosition.lifecycle == "ACTIVE"))
    )
    return [
        row
        for row in rows
        if canonical_position_identity(row.name).key == identity.key
    ]


def resolve_active_position(
    db: Session,
    value: str,
    *,
    positions: list[BasePosition] | None = None,
) -> BasePosition | None:
    matches = active_position_matches(db, value, positions=positions)
    if len(matches) > 1:
        names = ", ".join(sorted({row.name for row in matches}))
        raise AmbiguousPositionError(
            f"Position {value!r} matches multiple active canonical records: {names}."
        )
    return matches[0] if matches else None


def resolve_or_create_position(
    db: Session,
    value: str,
    *,
    crew_group_id: uuid.UUID | None,
    lifecycle: str = "ACTIVE",
) -> tuple[BasePosition, CanonicalPositionIdentity, bool]:
    identity = canonical_position_identity(value)
    existing = resolve_active_position(db, value)
    if existing is not None:
        return existing, identity, False
    position = BasePosition(
        name=identity.canonical_name,
        crew_group_id=crew_group_id,
        lifecycle=lifecycle,
    )
    db.add(position)
    db.flush()
    return position, identity, True


def _canonical_sort_key(
    position: BasePosition,
    group_names: dict[uuid.UUID | None, str],
) -> tuple[int, int, int, str, str]:
    group_name = normalized_name(group_names.get(position.crew_group_id, ""))
    return (
        0 if position.display_order is not None else 1,
        1 if group_name == "operational crew" else 0,
        position.display_order if position.display_order is not None else 2_147_483_647,
        group_name,
        str(position.id),
    )


def position_convergence_preview(db: Session) -> PositionConvergencePlan:
    groups = {row.id: row.name for row in db.scalars(select(CrewGroup))}
    groups[None] = "Unassigned group"
    positions = list(
        db.scalars(select(BasePosition).where(BasePosition.lifecycle == "ACTIVE"))
    )
    by_identity: dict[str, list[BasePosition]] = {}
    for position in positions:
        by_identity.setdefault(canonical_position_identity(position.name).key, []).append(position)

    plan = PositionConvergencePlan()
    for _key, candidates in sorted(by_identity.items()):
        if len(candidates) < 2:
            continue
        ordered = sorted(candidates, key=lambda row: _canonical_sort_key(row, groups))
        canonical, duplicates = ordered[0], ordered[1:]
        duplicate_ids = [row.id for row in duplicates]
        capability_count = db.scalar(
            select(func.count()).select_from(PositionCapability).where(
                PositionCapability.base_position_id.in_(duplicate_ids)
            )
        ) or 0
        preset_count = db.scalar(
            select(func.count()).select_from(PositionPresetItem).where(
                PositionPresetItem.base_position_id.in_(duplicate_ids)
            )
        ) or 0
        published_assignment_count = db.scalar(
            select(func.count())
            .select_from(Assignment)
            .join(WorkdayRevision, WorkdayRevision.id == Assignment.revision_id)
            .where(
                Assignment.base_position_id.in_(duplicate_ids),
                WorkdayRevision.state == "PUBLISHED",
            )
        ) or 0
        plan.rows.append(
            PositionConvergenceRow(
                identity=canonical_position_identity(canonical.name),
                canonical=canonical,
                duplicates=duplicates,
                group_names=groups,
                capability_references=capability_count,
                preset_references=preset_count,
                published_assignment_references=published_assignment_count,
            )
        )
    return plan


def apply_position_convergence(db: Session) -> dict[str, int]:
    plan = position_convergence_preview(db)
    result = {
        "canonical_positions": len(plan.rows),
        "archived_positions": 0,
        "capabilities_transferred": 0,
        "capabilities_deduplicated": 0,
        "presets_transferred": 0,
        "presets_deduplicated": 0,
        "published_assignments_unchanged": plan.published_assignment_references,
    }
    for row in plan.rows:
        duplicate_ids = {item.id for item in row.duplicates}
        capability_keys = {
            (item.person_id, item.signal)
            for item in db.scalars(
                select(PositionCapability).where(
                    PositionCapability.base_position_id == row.canonical.id
                )
            )
        }
        for capability in list(
            db.scalars(
                select(PositionCapability).where(
                    PositionCapability.base_position_id.in_(duplicate_ids)
                )
            )
        ):
            key = (capability.person_id, capability.signal)
            if key in capability_keys:
                db.delete(capability)
                result["capabilities_deduplicated"] += 1
            else:
                capability.base_position_id = row.canonical.id
                capability_keys.add(key)
                result["capabilities_transferred"] += 1

        preset_ids = set(
            db.scalars(
                select(PositionPresetItem.preset_id).where(
                    PositionPresetItem.base_position_id == row.canonical.id
                )
            )
        )
        for item in list(
            db.scalars(
                select(PositionPresetItem).where(
                    PositionPresetItem.base_position_id.in_(duplicate_ids)
                )
            )
        ):
            if item.preset_id in preset_ids:
                db.delete(item)
                result["presets_deduplicated"] += 1
            else:
                item.base_position_id = row.canonical.id
                preset_ids.add(item.preset_id)
                result["presets_transferred"] += 1

        for duplicate in row.duplicates:
            duplicate.lifecycle = "ARCHIVED"
            result["archived_positions"] += 1
    db.flush()
    return result
