from __future__ import annotations

from datetime import date

from sqlalchemy import func, select

from app.catalog.models import (
    BasePosition,
    CrewGroup,
    PositionPreset,
    PositionPresetItem,
    Region,
)
from app.external_calendar.importer import apply_bundle, parse_bundle, preview_bundle
from app.identity.models import Person, User
from app.positions.canonical import (
    apply_position_convergence,
    position_convergence_preview,
)
from app.rostering.models import Assignment, PositionCapability, Workday, WorkdayRevision


def test_neutral_import_reuses_global_canonical_position_and_is_idempotent(db) -> None:  # type: ignore[no-untyped-def]
    user = User(email="admin@example.test", display_name="Admin", credential_hash="unused")
    region = Region(name="Northern")
    camera = CrewGroup(name="Camera Crew")
    operational = CrewGroup(name="Operational Crew")
    db.add_all([user, region, camera, operational])
    db.flush()
    canonical = BasePosition(name="Side 1", crew_group_id=camera.id, display_order=1)
    db.add(canonical)
    db.flush()
    bundle = parse_bundle(
        '{"positions":[{"name":" side   1 ","crew_group":"Operational Crew"}],'
        '"people":[{"display_name":"Imported Crew","email":"imported@example.test",'
        '"home_region":"Northern","crew_groups":["Operational Crew"],'
        '"capabilities":[{"crew_group":"Operational Crew","position":"SIDE 1"}]}]}'
    )
    plan = preview_bundle(db, bundle)
    assert plan.valid and plan.counts["positions"] == {"match": 1}
    apply_bundle(db, bundle, user.id)
    db.flush()
    person = db.scalar(select(Person).where(Person.email == "imported@example.test"))
    capability = db.scalar(
        select(PositionCapability).where(PositionCapability.person_id == person.id)
    )
    assert capability.base_position_id == canonical.id
    assert db.scalar(
        select(func.count()).select_from(BasePosition).where(BasePosition.lifecycle == "ACTIVE")
    ) == 1

    apply_bundle(db, bundle, user.id)
    db.flush()
    assert db.scalar(
        select(func.count()).select_from(BasePosition).where(BasePosition.lifecycle == "ACTIVE")
    ) == 1


def test_neutral_import_blocks_ambiguous_global_canonical_positions(db) -> None:  # type: ignore[no-untyped-def]
    camera = CrewGroup(name="Camera Crew")
    operational = CrewGroup(name="Operational Crew")
    db.add_all([camera, operational])
    db.flush()
    db.add_all(
        [
            BasePosition(name="CCU", crew_group_id=camera.id),
            BasePosition(name="CCU1", crew_group_id=operational.id),
        ]
    )
    db.flush()
    bundle = parse_bundle(
        '{"positions":[{"name":"CCU 2","crew_group":"Operational Crew"}]}'
    )
    plan = preview_bundle(db, bundle)
    assert not plan.valid
    assert plan.counts["positions"] == {"conflict": 1}
    assert any("multiple active canonical records" in warning for warning in plan.warnings)


def test_position_convergence_transfers_mutable_references_not_published_assignments(db) -> None:  # type: ignore[no-untyped-def]
    user = User(email="admin@example.test", display_name="Admin", credential_hash="unused")
    person = Person(display_name="Camera Operator")
    region = Region(name="Northern")
    camera = CrewGroup(name="Camera Crew")
    engineers = CrewGroup(name="Engineers")
    operational = CrewGroup(name="Operational Crew")
    db.add_all([user, person, region, camera, engineers, operational])
    db.flush()
    side = BasePosition(name="Side 1", crew_group_id=camera.id, display_order=1)
    side_duplicate = BasePosition(name="Side 1", crew_group_id=operational.id)
    ccu = BasePosition(name="CCU", crew_group_id=engineers.id, display_order=20)
    ccu_one = BasePosition(name="CCU1", crew_group_id=operational.id)
    ccu_two = BasePosition(name="CCU 2", crew_group_id=operational.id)
    db.add_all([side, side_duplicate, ccu, ccu_one, ccu_two])
    db.flush()
    preset = PositionPreset(region_id=region.id, preset_key="THOROUGHBRED")
    db.add(preset)
    db.flush()
    db.add_all(
        [
            PositionPresetItem(preset_id=preset.id, base_position_id=side.id),
            PositionPresetItem(preset_id=preset.id, base_position_id=side_duplicate.id),
            PositionPresetItem(preset_id=preset.id, base_position_id=ccu_one.id),
            PositionPresetItem(preset_id=preset.id, base_position_id=ccu_two.id),
            PositionCapability(
                person_id=person.id,
                base_position_id=side.id,
                signal="MANAGER_ALLOW",
                changed_by_user_id=user.id,
            ),
            PositionCapability(
                person_id=person.id,
                base_position_id=side_duplicate.id,
                signal="MANAGER_ALLOW",
                changed_by_user_id=user.id,
            ),
            PositionCapability(
                person_id=person.id,
                base_position_id=side_duplicate.id,
                signal="WORKED",
                changed_by_user_id=user.id,
            ),
            PositionCapability(
                person_id=person.id,
                base_position_id=ccu_one.id,
                signal="WORKED",
                changed_by_user_id=user.id,
            ),
            PositionCapability(
                person_id=person.id,
                base_position_id=ccu_two.id,
                signal="WORKED",
                changed_by_user_id=user.id,
            ),
        ]
    )
    workday = Workday(region_id=region.id, created_by_user_id=user.id)
    db.add(workday)
    db.flush()
    revision = WorkdayRevision(
        workday_id=workday.id,
        revision_number=1,
        state="PUBLISHED",
        work_date=date(2026, 10, 14),
        title="Imported day",
        created_by_user_id=user.id,
    )
    db.add(revision)
    db.flush()
    assignments = [
        Assignment(
            revision_id=revision.id,
            base_position_id=position.id,
            display_name_snapshot=snapshot,
            status="ASSIGNED",
        )
        for position, snapshot in (
            (side_duplicate, "Side 1"),
            (ccu_one, "CCU1"),
            (ccu_two, "CCU2"),
        )
    ]
    db.add_all(assignments)
    db.flush()
    workday.current_published_revision_id = revision.id
    before_assignments = {
        row.id: (row.base_position_id, row.display_name_snapshot) for row in assignments
    }

    preview = position_convergence_preview(db)
    assert preview.duplicate_count == 3
    assert preview.capability_references == 4
    assert preview.preset_references == 3
    assert preview.published_assignment_references == 3
    result = apply_position_convergence(db)
    assert result["archived_positions"] == 3
    assert result["published_assignments_unchanged"] == 3
    assert {
        row.id: (row.base_position_id, row.display_name_snapshot)
        for row in db.scalars(select(Assignment))
    } == before_assignments
    assert side_duplicate.lifecycle == ccu_one.lifecycle == ccu_two.lifecycle == "ARCHIVED"
    assert {
        (row.base_position_id, row.signal)
        for row in db.scalars(select(PositionCapability))
    } == {
        (side.id, "MANAGER_ALLOW"),
        (side.id, "WORKED"),
        (ccu.id, "WORKED"),
    }
    assert set(
        db.scalars(
            select(PositionPresetItem.base_position_id).where(
                PositionPresetItem.preset_id == preset.id
            )
        )
    ) == {side.id, ccu.id}
    assert position_convergence_preview(db).duplicate_count == 0
