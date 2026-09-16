import uuid
from datetime import time

import pytest

from app.auth.policy import Actor
from app.core.time import parse_time
from app.employee.read_models import day_assignments
from app.identity.models import User
from app.positions.ordering import position_order
from app.rostering.models import Assignment, WorkdayRevision


@pytest.mark.parametrize("value", ["930", "0930", "9:30", "09:30", " 930 "])
def test_friendly_time_input(value: str) -> None:
    assert parse_time(value) == time(9, 30)


@pytest.mark.parametrize("value", ["24:00", "1260", "9", "09:30:20", "09:30+12:00", "abc", "９３０"])
def test_invalid_time_input(value: str) -> None:
    with pytest.raises(ValueError):
        parse_time(value)


def test_empty_and_midnight_time() -> None:
    assert parse_time(" ") is None
    assert parse_time("0000") == time(0, 0)
    assert parse_time("2359") == time(23, 59)


def test_operational_position_order_preserves_names() -> None:
    expected = [
        "Side 1",
        "Side 2",
        "Head On",
        "Back",
        "Back 1",
        "Back 2",
        "Back 10",
        "Turn",
        "RTS",
        "Gimble",
        "Gimbal Assist",
        "Steadicam",
        "Steady Assist",
        "Director",
        "VT",
        "Sound",
        "Sound/VT",
        "CCU1",
        "CCU2",
        "CCU10",
        "ENG",
        "Engineer 2",
        "ENG 10",
        "Custom A",
        "Custom B",
    ]
    assert sorted(reversed(expected), key=position_order) == expected
    assert position_order("Gimble")[:3] == position_order("Gimbal")[:3]
    assert position_order("Steady")[:3] == position_order("Steadicam")[:3]


def test_published_assignment_order_does_not_mutate_identity(db) -> None:  # type: ignore[no-untyped-def]
    # A narrow read-model unit test; SQLite is not PostgreSQL qualification.
    from datetime import date

    from app.catalog.models import Region
    from app.rostering.models import Workday

    user = User(email="order@example.test", display_name="Order", credential_hash="unused")
    region = Region(name="Order Region")
    db.add_all([user, region])
    db.flush()
    wd = Workday(region_id=region.id, created_by_user_id=user.id)
    db.add(wd)
    db.flush()
    revision = WorkdayRevision(
        workday_id=wd.id,
        revision_number=1,
        state="PUBLISHED",
        work_date=date(2026, 9, 20),
        created_by_user_id=user.id,
    )
    db.add(revision)
    db.flush()
    rows = [
        Assignment(
            revision_id=revision.id, display_name_snapshot=name, slot_index=index, slot_key=uuid.uuid4()
        )
        for index, name in enumerate(["Sound/VT", "CCU10", "Side 2", "Head On", "CCU2"], 1)
    ]
    db.add_all(rows)
    db.flush()
    identities = [(row.id, row.slot_key, row.slot_index, row.display_name_snapshot) for row in rows]
    actor = Actor(user_id=user.id, person_id=None, global_roles=frozenset(), regional_roles={})
    visible = day_assignments(db, actor, revision, can_view_all_rows=True, can_view_private_notes=False)
    assert [row["role"] for row in visible] == ["Side 2", "Head On", "Sound/VT", "CCU2", "CCU10"]
    assert identities == [(row.id, row.slot_key, row.slot_index, row.display_name_snapshot) for row in rows]
