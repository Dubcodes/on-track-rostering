from __future__ import annotations

from datetime import date, time
from decimal import Decimal

from app.auth.policy import actor_for
from app.auth.security import hash_credential
from app.catalog.models import Region
from app.core.enums import Role
from app.hours.service import fortnight_bounds, group_people, published_hours
from app.identity.models import Person, RoleGrant, User, UserPersonLink
from app.rostering.models import AllowanceIndicator, Assignment, Workday, WorkdayRevision


def _published_day(
    db,  # type: ignore[no-untyped-def]
    *,
    creator: User,
    region: Region,
    person: Person,
    work_date: date,
    start: time,
    end: time,
) -> WorkdayRevision:
    workday = Workday(region_id=region.id, created_by_user_id=creator.id)
    db.add(workday)
    db.flush()
    revision = WorkdayRevision(
        workday_id=workday.id,
        revision_number=1,
        state="PUBLISHED",
        work_date=work_date,
        track_name_snapshot=region.name + " Track",
        title="Published day",
        start_time=start,
        end_time=end,
        created_by_user_id=creator.id,
        published_by_user_id=creator.id,
    )
    db.add(revision)
    db.flush()
    db.add(
        Assignment(
            revision_id=revision.id,
            display_name_snapshot="CCU 1",
            person_id=person.id,
            person_name_snapshot=person.display_name,
            status="ASSIGNED",
        )
    )
    workday.current_published_revision_id = revision.id
    db.flush()
    return revision


def test_fortnight_boundary_previous_and_next() -> None:
    assert fortnight_bounds(0, date(2026, 9, 8)) == (date(2026, 8, 31), date(2026, 9, 13))
    assert fortnight_bounds(-1, date(2026, 9, 8)) == (date(2026, 8, 17), date(2026, 8, 30))
    assert fortnight_bounds(1, date(2026, 9, 8)) == (date(2026, 9, 14), date(2026, 9, 27))


def test_hours_use_full_published_span_and_allowances_do_not_change_total(db) -> None:  # type: ignore[no-untyped-def]
    region = Region(name="Northern")
    creator = User(
        email="manager@example.test",
        display_name="Manager",
        credential_hash=hash_credential("123456"),
        credential_kind="pin",
    )
    person = Person(display_name="Amy", home_region_id=None)
    employee = User(
        email="amy@example.test",
        display_name="Amy",
        credential_hash=hash_credential("654321"),
        credential_kind="pin",
    )
    db.add_all([region, creator, person, employee])
    db.flush()
    person.home_region_id = region.id
    db.add_all(
        [
            UserPersonLink(user_id=employee.id, person_id=person.id),
            RoleGrant(user_id=employee.id, role=Role.EMPLOYEE.value, region_id=region.id),
        ]
    )
    labour_day = _published_day(
        db,
        creator=creator,
        region=region,
        person=person,
        work_date=date(2026, 10, 26),
        start=time(7, 30),
        end=time(19, 30),
    )
    _published_day(
        db,
        creator=creator,
        region=region,
        person=person,
        work_date=date(2026, 10, 27),
        start=time(23),
        end=time(2),
    )
    db.add(
        AllowanceIndicator(
            revision_id=labour_day.id,
            person_id=person.id,
            kind="RESCHEDULED",
            entitlement_hours=Decimal("2.00"),
            explanation="Manually confirmed rescheduled-day entitlement.",
        )
    )
    db.commit()

    rows = published_hours(
        db,
        actor=actor_for(db, employee),
        start=date(2026, 10, 19),
        end=date(2026, 11, 1),
        management=False,
    )
    assert [row["minutes"] for row in rows] == [720, 180]
    assert group_people(rows)[0]["duration"] == "15h"
    assert rows[0]["holiday"] == "Labour Day"
    assert {item["kind"] for item in rows[0]["allowances"]} == {"LUNCH", "RESCHEDULED"}
    assert "no automatic break deduction" in rows[0]["raw"]


def test_management_hours_are_region_scoped_for_viewer(db) -> None:  # type: ignore[no-untyped-def]
    north, south = Region(name="Northern"), Region(name="Southern")
    creator = User(
        email="creator@example.test",
        display_name="Creator",
        credential_hash=hash_credential("123456"),
        credential_kind="pin",
    )
    viewer = User(
        email="viewer@example.test",
        display_name="Viewer",
        credential_hash=hash_credential("112233"),
        credential_kind="pin",
    )
    amy, ben = Person(display_name="Amy"), Person(display_name="Ben")
    db.add_all([north, south, creator, viewer, amy, ben])
    db.flush()
    db.add(RoleGrant(user_id=viewer.id, role=Role.VIEWER.value, region_id=north.id))
    _published_day(
        db,
        creator=creator,
        region=north,
        person=amy,
        work_date=date(2026, 9, 1),
        start=time(7),
        end=time(15),
    )
    _published_day(
        db,
        creator=creator,
        region=south,
        person=ben,
        work_date=date(2026, 9, 2),
        start=time(7),
        end=time(15),
    )
    db.commit()

    rows = published_hours(
        db,
        actor=actor_for(db, viewer),
        start=date(2026, 8, 31),
        end=date(2026, 9, 13),
        management=True,
    )
    assert [row["person"] for row in rows] == ["Amy"]
