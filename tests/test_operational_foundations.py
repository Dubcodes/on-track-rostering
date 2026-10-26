from __future__ import annotations

import uuid
from datetime import date, time

from sqlalchemy import event, select

from app.auth.policy import Actor
from app.catalog.models import (
    BasePosition,
    EquipmentSetup,
    EquipmentSetupItem,
    OperationalPlace,
    Region,
    Track,
)
from app.core.enums import CapabilitySignal, Role
from app.employee.read_models import month_items
from app.identity.models import User
from app.rostering.diff import publication_diff
from app.rostering.models import (
    Assignment,
    PositionCapability,
    TravelLeg,
    Workday,
    WorkdayEquipmentItem,
    WorkdayRevision,
)
from app.rostering.service import (
    DraftDetailsInput,
    DraftEquipmentInput,
    create_workday,
    ensure_draft,
    publish,
    save_draft,
)


def _details(draft: WorkdayRevision, track: Track, setup_id: uuid.UUID) -> DraftDetailsInput:
    return DraftDetailsInput(
        work_date=draft.work_date,
        track_id=track.id,
        title="Regional raceday",
        start_time=time(9),
        end_time=time(18),
        on_track_time=time(10),
        first_trial_time=None,
        first_race_time=time(11),
        last_race_time=time(16),
        race_count=8,
        day_note="",
        change_reason="",
        start_origin="Northern base",
        finish_destination="Northern base",
        standard_travel_enabled=True,
        travel_departure_time=time(12),
        travel_to_hotel_minutes=120,
        default_hotel="Trackside Lodge",
        hotel_to_track_minutes=20,
        return_travel_minutes=120,
        equipment_setup_id=setup_id,
    )


def test_places_and_equipment_are_snapshotted_cloned_and_diffed(db) -> None:  # type: ignore[no-untyped-def]
    region = Region(
        name="Northern",
        base_name="Northern base",
        base_address="1 Base Road",
    )
    manager = User(email="manager@example.test", display_name="Manager", credential_hash="unused")
    db.add_all([region, manager])
    db.flush()
    track = Track(name="Ellerslie", region_id=region.id, palette_slot=1)
    hotel = OperationalPlace(
        region_id=region.id,
        name="Trackside Lodge",
        address="20 Hotel Lane",
        kind="ACCOMMODATION",
    )
    setup = EquipmentSetup(region_id=region.id, name="Standard IV")
    db.add_all([track, hotel, setup])
    db.flush()
    db.add(EquipmentSetupItem(setup_id=setup.id, name="Wireless microphone", quantity=2))
    db.commit()

    workday = create_workday(
        db,
        region_id=region.id,
        category="RACE_DAY",
        racing_discipline="THOROUGHBRED",
        work_date=date(2026, 11, 7),
        track_id=track.id,
        title="Regional raceday",
        actor_user_id=manager.id,
    )
    draft = db.get(WorkdayRevision, workday.current_draft_revision_id)
    assert draft is not None
    item_key = uuid.uuid4()
    save_draft(
        db,
        workday_id=workday.id,
        draft_id=draft.id,
        expected_version=workday.lock_version,
        details=_details(draft, track, setup.id),
        assignments=[],
        equipment_items=[
            DraftEquipmentInput(item_key, "Wireless microphone", 3, "One spare")
        ],
    )
    assert draft.start_address_snapshot == "1 Base Road"
    assert draft.finish_address_snapshot == "1 Base Road"
    assert draft.default_hotel_address_snapshot == "20 Hotel Lane"
    travel = db.scalar(select(Workday).where(Workday.generated_from_workday_id == workday.id))
    assert travel is not None
    travel_revision = db.get(WorkdayRevision, travel.current_draft_revision_id)
    assert travel_revision is not None
    assert travel_revision.track_address_snapshot == "20 Hotel Lane"
    assert len(list(db.scalars(select(TravelLeg).where(TravelLeg.operation_id == workday.operation_id)))) == 3

    db.commit()
    publish(db, workday.id, draft.id, manager.id, workday.lock_version)
    hotel.name = "Renamed Lodge"
    hotel.address = "99 New Road"
    hotel.lifecycle = "ARCHIVED"
    db.commit()

    editable = ensure_draft(db, workday, manager.id)
    copied = list(
        db.scalars(
            select(WorkdayEquipmentItem).where(WorkdayEquipmentItem.revision_id == editable.id)
        )
    )
    assert [(row.item_key, row.name, row.quantity, row.notes) for row in copied] == [
        (item_key, "Wireless microphone", 3, "One spare")
    ]
    assert editable.default_hotel == "Trackside Lodge"
    assert editable.default_hotel_address_snapshot == "20 Hotel Lane"

    copied[0].quantity = 4
    changes = publication_diff(db, draft, editable)
    assert any("3 × Wireless microphone to 4 × Wireless microphone" in row.summary for row in changes)


def test_one_off_place_text_remains_valid_without_catalogue_address(db) -> None:  # type: ignore[no-untyped-def]
    region = Region(name="Central", base_name="Central base", base_address="1 Base Road")
    manager = User(email="central@example.test", display_name="Manager", credential_hash="unused")
    db.add_all([region, manager])
    db.flush()
    track = Track(name="Cambridge Synthetic", region_id=region.id, palette_slot=1)
    db.add(track)
    db.commit()
    workday = create_workday(
        db,
        region_id=region.id,
        category="OTHER",
        work_date=date(2026, 11, 8),
        track_id=track.id,
        title="Setup",
        actor_user_id=manager.id,
    )
    draft = db.get(WorkdayRevision, workday.current_draft_revision_id)
    assert draft is not None
    details = DraftDetailsInput(
        work_date=draft.work_date,
        track_id=track.id,
        title="Setup",
        start_time=time(9),
        end_time=time(17),
        on_track_time=None,
        first_trial_time=None,
        first_race_time=None,
        last_race_time=None,
        race_count=None,
        day_note="",
        change_reason="",
        start_origin="One-off loading bay",
        finish_destination="Temporary studio",
        default_hotel="One-off motel",
    )
    save_draft(
        db,
        workday_id=workday.id,
        draft_id=draft.id,
        expected_version=workday.lock_version,
        details=details,
        assignments=[],
        equipment_items=[],
    )
    assert (draft.start_origin, draft.start_address_snapshot) == ("One-off loading bay", "")
    assert (draft.finish_destination, draft.finish_address_snapshot) == ("Temporary studio", "")
    assert (draft.default_hotel, draft.default_hotel_address_snapshot) == ("One-off motel", "")


def test_personal_month_open_position_eligibility_has_bounded_query_count(db) -> None:  # type: ignore[no-untyped-def]
    region = Region(name="Synthetic performance region")
    creator = User(email="perf@example.test", display_name="Creator", credential_hash="unused")
    from app.identity.models import Person

    person = Person(display_name="Synthetic crew", home_region_id=region.id)
    db.add_all([region, creator])
    db.flush()
    person.home_region_id = region.id
    db.add(person)
    positions = [BasePosition(name=f"Synthetic position {index}") for index in range(25)]
    db.add_all(positions)
    db.flush()
    for index, position in enumerate(positions):
        db.add(
            PositionCapability(
                person_id=person.id,
                base_position_id=position.id,
                signal=CapabilitySignal.MANAGER_ALLOW.value,
                changed_by_user_id=creator.id,
            )
        )
        workday = Workday(region_id=region.id, created_by_user_id=creator.id)
        db.add(workday)
        db.flush()
        revision = WorkdayRevision(
            workday_id=workday.id,
            revision_number=1,
            state="PUBLISHED",
            work_date=date(2026, 12, 1 + index),
            title=f"Synthetic open day {index}",
            created_by_user_id=creator.id,
        )
        db.add(revision)
        db.flush()
        db.add(
            Assignment(
                revision_id=revision.id,
                base_position_id=position.id,
                display_name_snapshot=position.name,
                status="OPEN",
            )
        )
        workday.current_published_revision_id = revision.id
    db.commit()
    actor = Actor(
        user_id=creator.id,
        person_id=person.id,
        global_roles=frozenset(),
        regional_roles={region.id: frozenset({Role.EMPLOYEE.value})},
    )
    statements: list[str] = []

    def count_query(_connection, _cursor, statement, _parameters, _context, _many):  # type: ignore[no-untyped-def]
        statements.append(statement)

    event.listen(db.get_bind(), "before_cursor_execute", count_query)
    try:
        rows = month_items(db, actor, date(2026, 12, 1), date(2027, 1, 1))
    finally:
        event.remove(db.get_bind(), "before_cursor_execute", count_query)
    assert len(rows) == 25
    assert len(statements) <= 6, "open-position Month reads must not query eligibility per row"
