from datetime import date, timedelta

import pytest
from sqlalchemy import select

from app.auth.security import hash_credential
from app.catalog.models import Region
from app.identity.models import Person, User
from app.unavailability.models import PersonUnavailability
from app.unavailability.service import (
    active_for_people_on_date,
    cancel_unavailability,
    create_unavailability,
)


def test_unavailability_inclusive_overlap_cancellation_and_history(db) -> None:  # type: ignore[no-untyped-def]
    region = Region(name="Leave Region")
    user = User(
        email="leave-manager@example.test",
        display_name="Leave Manager",
        credential_hash=hash_credential("123456"),
        credential_kind="pin",
    )
    db.add_all([region, user])
    db.flush()
    first = Person(display_name="First Crew", home_region_id=region.id)
    second = Person(display_name="Second Crew", home_region_id=region.id)
    db.add_all([first, second])
    db.commit()

    with pytest.raises(ValueError, match="End date"):
        create_unavailability(
            db,
            person=first,
            start_date=date(2026, 10, 16),
            end_date=date(2026, 10, 12),
            note="",
            actor_user_id=user.id,
        )

    leave = create_unavailability(
        db,
        person=first,
        start_date=date(2026, 10, 12),
        end_date=date(2026, 10, 16),
        note="Sensitive note",
        actor_user_id=user.id,
    )
    assert first.id in active_for_people_on_date(db, {first.id}, date(2026, 10, 12))
    assert first.id in active_for_people_on_date(db, {first.id}, date(2026, 10, 16))
    assert not active_for_people_on_date(db, {first.id}, date(2026, 10, 17))
    with pytest.raises(ValueError, match="overlapping"):
        create_unavailability(
            db,
            person=first,
            start_date=date(2026, 10, 16),
            end_date=date(2026, 10, 18),
            note="",
            actor_user_id=user.id,
        )
    other = create_unavailability(
        db,
        person=second,
        start_date=date(2026, 10, 12),
        end_date=date(2026, 10, 16),
        note="",
        actor_user_id=user.id,
    )
    adjacent = create_unavailability(
        db,
        person=first,
        start_date=leave.end_date + timedelta(days=1),
        end_date=leave.end_date + timedelta(days=2),
        note="",
        actor_user_id=user.id,
    )
    assert other.person_id != adjacent.person_id

    cancel_unavailability(db, row=leave, person=first, actor_user_id=user.id)
    assert first.id not in active_for_people_on_date(db, {first.id}, date(2026, 10, 12))
    assert db.get(PersonUnavailability, leave.id).cancelled_at is not None
    assert len(list(db.scalars(select(PersonUnavailability)))) == 3
