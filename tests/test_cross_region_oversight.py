from __future__ import annotations

import uuid
from datetime import date, time

from app.audit.read import permitted_audit_records
from app.audit.service import record_audit
from app.auth.policy import actor_for
from app.auth.security import hash_credential
from app.catalog.models import Region
from app.core.enums import Role
from app.hours.service import group_people, published_hours
from app.identity.models import Person, RoleGrant, User, UserPersonLink
from app.rostering.models import Assignment, Workday, WorkdayRevision


def _user(email: str, name: str) -> User:
    return User(
        email=email,
        display_name=name,
        credential_hash=hash_credential("123456"),
        credential_kind="pin",
    )


def _published_workday(
    db,  # type: ignore[no-untyped-def]
    *,
    region: Region,
    creator: User,
    work_date: date,
    people: list[tuple[Person, str, time, time]],
    category: str = "RACE_DAY",
    generated_from_workday_id: uuid.UUID | None = None,
) -> tuple[Workday, WorkdayRevision]:
    workday = Workday(
        region_id=region.id,
        category=category,
        created_by_user_id=creator.id,
        generated_from_workday_id=generated_from_workday_id,
    )
    db.add(workday)
    db.flush()
    revision = WorkdayRevision(
        workday_id=workday.id,
        revision_number=1,
        state="PUBLISHED",
        work_date=work_date,
        title=f"{region.name} event",
        track_name_snapshot=f"{region.name} Track",
        start_time=min(item[2] for item in people),
        end_time=max(item[3] for item in people),
        created_by_user_id=creator.id,
        published_by_user_id=creator.id,
    )
    db.add(revision)
    db.flush()
    for index, (person, position, start, end) in enumerate(people, 1):
        db.add(
            Assignment(
                revision_id=revision.id,
                slot_key=uuid.uuid4(),
                slot_index=index,
                display_name_snapshot=position,
                person_id=person.id,
                person_name_snapshot=person.display_name,
                status="ASSIGNED",
                start_time=start,
                end_time=end,
            )
        )
    workday.current_published_revision_id = revision.id
    db.flush()
    return workday, revision


def test_manager_hours_follow_employee_home_region_across_workday_regions(db) -> None:  # type: ignore[no-untyped-def]
    northern = Region(name="Northern")
    central = Region(name="Central")
    christchurch = Region(name="Christchurch")
    northern_manager = _user("north-manager@example.test", "Northern Manager")
    southern_manager = _user("south-manager@example.test", "Christchurch Manager")
    employee_user = _user("north-employee@example.test", "Northern Employee")
    northern_person = Person(display_name="Northern Employee")
    christchurch_person = Person(display_name="Christchurch Employee")
    db.add_all(
        [
            northern,
            central,
            christchurch,
            northern_manager,
            southern_manager,
            employee_user,
            northern_person,
            christchurch_person,
        ]
    )
    db.flush()
    northern_person.home_region_id = northern.id
    christchurch_person.home_region_id = christchurch.id
    db.add_all(
        [
            RoleGrant(
                user_id=northern_manager.id,
                role=Role.MANAGER.value,
                region_id=northern.id,
            ),
            RoleGrant(
                user_id=employee_user.id,
                role=Role.EMPLOYEE.value,
                region_id=northern.id,
            ),
            UserPersonLink(
                user_id=employee_user.id,
                person_id=northern_person.id,
            ),
        ]
    )
    _published_workday(
        db,
        region=northern,
        creator=northern_manager,
        work_date=date(2026, 11, 2),
        people=[(northern_person, "CCU", time(8), time(16))],
    )
    central_workday, _central_revision = _published_workday(
        db,
        region=central,
        creator=southern_manager,
        work_date=date(2026, 11, 3),
        people=[(northern_person, "VT", time(7), time(15))],
    )
    christchurch_workday, christchurch_revision = _published_workday(
        db,
        region=christchurch,
        creator=southern_manager,
        work_date=date(2026, 11, 4),
        people=[(northern_person, "CCU 1", time(22), time(8))],
    )
    db.add(
        Assignment(
            revision_id=christchurch_revision.id,
            display_name_snapshot="CCU 2",
            person_id=northern_person.id,
            person_name_snapshot=northern_person.display_name,
            status="ASSIGNED",
            start_time=time(23),
            end_time=time(7),
        )
    )
    _published_workday(
        db,
        region=northern,
        creator=northern_manager,
        work_date=date(2026, 11, 5),
        people=[(christchurch_person, "VT", time(8), time(16))],
    )
    _published_workday(
        db,
        region=christchurch,
        creator=southern_manager,
        work_date=date(2026, 11, 3),
        people=[(northern_person, "Travel", time(12), time(16))],
        category="TRAVEL_DAY",
        generated_from_workday_id=christchurch_workday.id,
    )
    db.commit()

    manager_rows = published_hours(
        db,
        actor=actor_for(db, northern_manager),
        start=date(2026, 11, 2),
        end=date(2026, 11, 15),
        management=True,
    )
    assert {row["region"] for row in manager_rows} == {
        "Northern",
        "Central",
        "Christchurch",
    }
    assert sum(int(row["minutes"]) for row in manager_rows) == 1800
    assert len([row for row in manager_rows if row["date"] == date(2026, 11, 4)]) == 1
    assert next(row for row in manager_rows if row["date"] == date(2026, 11, 4))["minutes"] == 600
    assert {row["person"] for row in manager_rows} == {"Northern Employee"}
    grouped = group_people(
        manager_rows, start=date(2026, 11, 2), end=date(2026, 11, 15)
    )[0]
    assert grouped["duration"] == "30h"
    assert any("Christchurch" in bucket["location"] for bucket in grouped["buckets"])

    personal_rows = published_hours(
        db,
        actor=actor_for(db, employee_user),
        start=date(2026, 11, 2),
        end=date(2026, 11, 15),
        management=False,
    )
    assert sum(int(row["minutes"]) for row in personal_rows) == 1800

    replacement = WorkdayRevision(
        workday_id=christchurch_workday.id,
        revision_number=2,
        state="PUBLISHED",
        work_date=christchurch_revision.work_date,
        title=christchurch_revision.title,
        track_name_snapshot=christchurch_revision.track_name_snapshot,
        start_time=time(22),
        end_time=time(8),
        created_by_user_id=southern_manager.id,
        published_by_user_id=southern_manager.id,
    )
    db.add(replacement)
    db.flush()
    christchurch_workday.current_published_revision_id = replacement.id
    central_workday.status = "ABANDONED"
    db.commit()
    updated_rows = published_hours(
        db,
        actor=actor_for(db, northern_manager),
        start=date(2026, 11, 2),
        end=date(2026, 11, 15),
        management=True,
    )
    # Removing the parent assignment also removes its generated Travel
    # participation; abandoning Central removes that publication as well.
    assert sum(int(row["minutes"]) for row in updated_rows) == 480


def test_cross_region_audit_projects_only_managed_employee_impacts(db) -> None:  # type: ignore[no-untyped-def]
    northern = Region(name="Northern")
    central = Region(name="Central")
    christchurch = Region(name="Christchurch")
    northern_manager = _user("north-audit@example.test", "Northern Manager")
    central_manager = _user("central-audit@example.test", "Central Manager")
    christchurch_manager = _user("chch-audit@example.test", "Christchurch Manager")
    admin = _user("admin-audit@example.test", "Admin")
    northern_person = Person(display_name="Northern Employee")
    christchurch_person = Person(display_name="Christchurch Employee")
    db.add_all(
        [
            northern,
            central,
            christchurch,
            northern_manager,
            central_manager,
            christchurch_manager,
            admin,
            northern_person,
            christchurch_person,
        ]
    )
    db.flush()
    northern_person.home_region_id = northern.id
    christchurch_person.home_region_id = christchurch.id
    db.add_all(
        [
            RoleGrant(user_id=northern_manager.id, role="MANAGER", region_id=northern.id),
            RoleGrant(user_id=central_manager.id, role="MANAGER", region_id=central.id),
            RoleGrant(
                user_id=christchurch_manager.id,
                role="MANAGER",
                region_id=christchurch.id,
            ),
            RoleGrant(user_id=admin.id, role="ADMIN", region_id=None),
        ]
    )
    workday = Workday(region_id=christchurch.id, created_by_user_id=christchurch_manager.id)
    db.add(workday)
    db.flush()
    slot_north, slot_chch = uuid.uuid4(), uuid.uuid4()
    previous = WorkdayRevision(
        workday_id=workday.id,
        revision_number=1,
        state="PUBLISHED",
        work_date=date(2026, 11, 7),
        title="Christchurch Race Day",
        track_name_snapshot="Riccarton",
        start_time=time(8),
        end_time=time(17),
        created_by_user_id=christchurch_manager.id,
        published_by_user_id=christchurch_manager.id,
    )
    db.add(previous)
    db.flush()
    db.add_all(
        [
            Assignment(
                revision_id=previous.id,
                slot_key=slot_north,
                display_name_snapshot="CCU 1",
                person_id=northern_person.id,
                person_name_snapshot=northern_person.display_name,
                status="ASSIGNED",
                start_time=time(8),
                end_time=time(17),
            ),
            Assignment(
                revision_id=previous.id,
                slot_key=slot_chch,
                display_name_snapshot="VT",
                person_id=christchurch_person.id,
                person_name_snapshot=christchurch_person.display_name,
                status="ASSIGNED",
                accommodation_name="Confidential hotel",
            ),
        ]
    )
    current = WorkdayRevision(
        workday_id=workday.id,
        revision_number=2,
        state="PUBLISHED",
        work_date=date(2026, 11, 7),
        title="Christchurch Race Day",
        track_name_snapshot="Riccarton",
        start_time=time(8),
        end_time=time(17),
        created_by_user_id=christchurch_manager.id,
        published_by_user_id=christchurch_manager.id,
    )
    db.add(current)
    db.flush()
    db.add_all(
        [
            Assignment(
                revision_id=current.id,
                slot_key=slot_north,
                display_name_snapshot="CCU 2",
                person_id=northern_person.id,
                person_name_snapshot=northern_person.display_name,
                status="ASSIGNED",
                start_time=time(7, 30),
                end_time=time(17),
            ),
            Assignment(
                revision_id=current.id,
                slot_key=slot_chch,
                display_name_snapshot="VT",
                person_id=christchurch_person.id,
                person_name_snapshot=christchurch_person.display_name,
                status="ASSIGNED",
                accommodation_name="Another confidential hotel",
            ),
        ]
    )
    workday.current_published_revision_id = current.id
    event = record_audit(
        db,
        "workday.published",
        "workday",
        workday.id,
        christchurch_manager.id,
        region_id=christchurch.id,
        detail={"revision": 2, "revision_id": str(current.id)},
    )
    local_event = record_audit(
        db,
        "regional.visible",
        "vehicle",
        uuid.uuid4(),
        northern_manager.id,
        region_id=northern.id,
    )
    db.commit()

    northern_records = permitted_audit_records(
        db,
        actor=actor_for(db, northern_manager),
        events=[event, event, local_event],
    )
    assert [record.event.id for record in northern_records] == [event.id, local_event.id]
    cross_record = northern_records[0]
    assert cross_record.cross_region_employee_oversight is True
    assert cross_record.visible_detail == {}
    assert {impact.person_id for impact in cross_record.impacts} == {northern_person.id}
    rendered = " ".join(impact.summary for impact in cross_record.impacts)
    assert "Position changed" in rendered and "Start" in rendered
    assert "Christchurch Employee" not in rendered
    assert "Confidential hotel" not in rendered

    mis_scoped_event = record_audit(
        db,
        "workday.published",
        "workday",
        workday.id,
        christchurch_manager.id,
        region_id=northern.id,
        detail={"revision": 2, "revision_id": str(current.id)},
    )
    db.commit()
    mis_scoped_records = permitted_audit_records(
        db, actor=actor_for(db, northern_manager), events=[mis_scoped_event]
    )
    assert len(mis_scoped_records) == 1
    assert mis_scoped_records[0].region_id == christchurch.id
    assert mis_scoped_records[0].cross_region_employee_oversight is True
    assert {impact.person_id for impact in mis_scoped_records[0].impacts} == {
        northern_person.id
    }

    central_records = permitted_audit_records(
        db, actor=actor_for(db, central_manager), events=[event]
    )
    assert central_records == []
    christchurch_records = permitted_audit_records(
        db, actor=actor_for(db, christchurch_manager), events=[event]
    )
    assert len(christchurch_records) == 1
    assert christchurch_records[0].cross_region_employee_oversight is False
    assert {impact.person_id for impact in christchurch_records[0].impacts} == {
        northern_person.id,
        christchurch_person.id,
    }
    assert christchurch_records[0].visible_detail["revision"] == 2
    admin_records = permitted_audit_records(
        db, actor=actor_for(db, admin), events=[event]
    )
    assert len(admin_records) == 1

    forged_draft = WorkdayRevision(
        workday_id=workday.id,
        revision_number=99,
        state="DRAFT",
        work_date=current.work_date,
        title="Private future roster",
        track_name_snapshot="Private draft track",
        start_time=time(6),
        end_time=time(18),
        created_by_user_id=christchurch_manager.id,
    )
    db.add(forged_draft)
    db.flush()
    db.add(
        Assignment(
            revision_id=forged_draft.id,
            slot_key=uuid.uuid4(),
            display_name_snapshot="Private draft position",
            person_id=northern_person.id,
            person_name_snapshot=northern_person.display_name,
            status="ASSIGNED",
        )
    )
    forged_draft_event = record_audit(
        db,
        "workday.published",
        "workday",
        workday.id,
        christchurch_manager.id,
        region_id=christchurch.id,
        detail={"revision": 99, "revision_id": str(forged_draft.id)},
    )
    declined_event = record_audit(
        db,
        "assignment.declined",
        "workday",
        workday.id,
        christchurch_manager.id,
        region_id=christchurch.id,
        detail={
            "revision_id": str(current.id),
            "person_id": str(northern_person.id),
            "person_name_snapshot": "Northern Employee at decline",
            "summary": "Declined CCU 2; position is open.",
        },
    )
    northern_person.display_name = "Northern Employee renamed later"
    db.commit()
    assert permitted_audit_records(
        db, actor=actor_for(db, northern_manager), events=[forged_draft_event]
    ) == []
    local_draft_records = permitted_audit_records(
        db,
        actor=actor_for(db, christchurch_manager),
        events=[forged_draft_event],
    )
    assert len(local_draft_records) == 1
    assert local_draft_records[0].revision is None
    assert local_draft_records[0].impacts == ()
    decline_records = permitted_audit_records(
        db, actor=actor_for(db, northern_manager), events=[declined_event]
    )
    assert len(decline_records) == 1
    assert decline_records[0].impacts[0].person_name == "Northern Employee at decline"

    # A legacy status row without a stable publication reference must not be
    # projected through a later current revision to grant cross-region access.
    unanchored_status_event = record_audit(
        db,
        "workday.status_changed",
        "workday",
        workday.id,
        christchurch_manager.id,
        region_id=christchurch.id,
        detail={"from": "SCHEDULED", "to": "CANCELLED"},
    )
    db.commit()
    assert permitted_audit_records(
        db, actor=actor_for(db, northern_manager), events=[unanchored_status_event]
    ) == []
    local_status_records = permitted_audit_records(
        db,
        actor=actor_for(db, christchurch_manager),
        events=[unanchored_status_event],
    )
    assert len(local_status_records) == 1
    assert local_status_records[0].impacts == ()

    removed = WorkdayRevision(
        workday_id=workday.id,
        revision_number=3,
        state="PUBLISHED",
        work_date=current.work_date,
        title=current.title,
        track_name_snapshot=current.track_name_snapshot,
        start_time=current.start_time,
        end_time=current.end_time,
        created_by_user_id=christchurch_manager.id,
        published_by_user_id=christchurch_manager.id,
    )
    db.add(removed)
    db.flush()
    db.add(
        Assignment(
            revision_id=removed.id,
            slot_key=slot_north,
            display_name_snapshot="CCU 2",
            person_name_snapshot="",
            status="OPEN",
        )
    )
    workday.current_published_revision_id = removed.id
    removal_event = record_audit(
        db,
        "workday.published",
        "workday",
        workday.id,
        christchurch_manager.id,
        region_id=christchurch.id,
        detail={"revision": 3, "revision_id": str(removed.id)},
    )
    db.commit()
    removal_records = permitted_audit_records(
        db, actor=actor_for(db, northern_manager), events=[removal_event]
    )
    assert len(removal_records) == 1
    assert any(
        impact.person_id == northern_person.id and "Removed from CCU 2" in impact.summary
        for impact in removal_records[0].impacts
    )

    added = WorkdayRevision(
        workday_id=workday.id,
        revision_number=4,
        state="PUBLISHED",
        work_date=removed.work_date,
        title=removed.title,
        track_name_snapshot=removed.track_name_snapshot,
        start_time=removed.start_time,
        end_time=removed.end_time,
        created_by_user_id=christchurch_manager.id,
        published_by_user_id=christchurch_manager.id,
    )
    db.add(added)
    db.flush()
    db.add(
        Assignment(
            revision_id=added.id,
            slot_key=slot_north,
            display_name_snapshot="CCU 2",
            person_id=northern_person.id,
            person_name_snapshot=northern_person.display_name,
            status="ASSIGNED",
        )
    )
    workday.current_published_revision_id = added.id
    addition_event = record_audit(
        db,
        "workday.published",
        "workday",
        workday.id,
        christchurch_manager.id,
        region_id=christchurch.id,
        detail={"revision": 4, "revision_id": str(added.id)},
    )
    db.commit()
    addition_records = permitted_audit_records(
        db, actor=actor_for(db, northern_manager), events=[addition_event]
    )
    assert len(addition_records) == 1
    assert any(
        impact.person_id == northern_person.id and "Assigned to CCU 2" in impact.summary
        for impact in addition_records[0].impacts
    )
