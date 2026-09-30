from datetime import date, timedelta

import pytest
from sqlalchemy import select

from app.auth.security import hash_credential
from app.catalog.models import Region
from app.core.enums import AssignmentStatus, RevisionState, WorkdayStatus
from app.identity.models import Person, User
from app.rostering.models import Assignment, Workday, WorkdayRevision
from app.unavailability.models import PersonUnavailability
from app.unavailability.service import (
    active_for_people_on_date,
    cancel_unavailability,
    create_unavailability,
    roster_conflicts,
    unavailability_label,
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


def test_unavailability_note_limit_and_date_labels(db) -> None:  # type: ignore[no-untyped-def]
    region = Region(name="Leave Validation Region")
    user = User(
        email="leave-validation@example.test",
        display_name="Leave Validation Manager",
        credential_hash=hash_credential("123456"),
        credential_kind="pin",
    )
    db.add_all([region, user])
    db.flush()
    person = Person(display_name="Leave Validation Crew", home_region_id=region.id)
    db.add(person)
    db.commit()

    with pytest.raises(ValueError, match="1000 characters"):
        create_unavailability(
            db,
            person=person,
            start_date=date(2026, 10, 12),
            end_date=date(2026, 10, 16),
            note="x" * 1001,
            actor_user_id=user.id,
        )
    assert not list(db.scalars(select(PersonUnavailability)))
    assert unavailability_label(date(2026, 10, 12), date(2026, 10, 16)) == (
        "On leave · 12–16 Oct"
    )
    assert unavailability_label(date(2026, 9, 29), date(2026, 10, 2)) == (
        "On leave · 29 Sep–2 Oct"
    )
    assert unavailability_label(date(2026, 12, 29), date(2027, 1, 2)) == (
        "On leave · 29 Dec 2026–2 Jan 2027"
    )


def test_roster_conflicts_use_published_or_never_published_draft(db) -> None:  # type: ignore[no-untyped-def]
    region = Region(name="Conflict Region")
    user = User(
        email="conflict-manager@example.test",
        display_name="Conflict Manager",
        credential_hash=hash_credential("123456"),
        credential_kind="pin",
    )
    db.add_all([region, user])
    db.flush()
    person = Person(display_name="Conflict Crew", home_region_id=region.id)
    db.add(person)
    db.flush()

    published_workday = Workday(
        region_id=region.id,
        status=WorkdayStatus.SCHEDULED.value,
        created_by_user_id=user.id,
    )
    db.add(published_workday)
    db.flush()
    published = WorkdayRevision(
        workday_id=published_workday.id,
        revision_number=1,
        state=RevisionState.PUBLISHED.value,
        work_date=date(2026, 10, 14),
        track_name_snapshot="Published Track",
        created_by_user_id=user.id,
    )
    db.add(published)
    db.flush()
    db.add_all(
        [
            Assignment(
                revision_id=published.id,
                display_name_snapshot="Camera",
                person_id=person.id,
                person_name_snapshot=person.display_name,
                status=AssignmentStatus.ASSIGNED.value,
            ),
            Assignment(
                revision_id=published.id,
                display_name_snapshot="Director",
                person_id=person.id,
                person_name_snapshot=person.display_name,
                status=AssignmentStatus.ASSIGNED.value,
            ),
        ]
    )
    private_draft = WorkdayRevision(
        workday_id=published_workday.id,
        revision_number=2,
        state=RevisionState.DRAFT.value,
        work_date=date(2026, 10, 15),
        track_name_snapshot="Private Draft Track",
        created_by_user_id=user.id,
    )
    db.add(private_draft)
    db.flush()
    published_workday.current_published_revision_id = published.id
    published_workday.current_draft_revision_id = private_draft.id

    draft_workday = Workday(
        region_id=region.id,
        status=WorkdayStatus.SCHEDULED.value,
        created_by_user_id=user.id,
    )
    db.add(draft_workday)
    db.flush()
    first_draft = WorkdayRevision(
        workday_id=draft_workday.id,
        revision_number=1,
        state=RevisionState.DRAFT.value,
        work_date=date(2026, 10, 16),
        track_name_snapshot="First Draft Track",
        created_by_user_id=user.id,
    )
    db.add(first_draft)
    db.flush()
    db.add(
        Assignment(
            revision_id=first_draft.id,
            display_name_snapshot="Operator",
            person_id=person.id,
            person_name_snapshot=person.display_name,
            status=AssignmentStatus.ASSIGNED.value,
        )
    )
    draft_workday.current_draft_revision_id = first_draft.id

    for status in (WorkdayStatus.CANCELLED.value, WorkdayStatus.ABANDONED.value):
        inactive = Workday(
            region_id=region.id,
            status=status,
            created_by_user_id=user.id,
        )
        db.add(inactive)
        db.flush()
        inactive_revision = WorkdayRevision(
            workday_id=inactive.id,
            revision_number=1,
            state=RevisionState.PUBLISHED.value,
            work_date=date(2026, 10, 17),
            track_name_snapshot=f"{status.title()} Track",
            created_by_user_id=user.id,
        )
        db.add(inactive_revision)
        db.flush()
        db.add(
            Assignment(
                revision_id=inactive_revision.id,
                display_name_snapshot="Should not appear",
                person_id=person.id,
                person_name_snapshot=person.display_name,
                status=AssignmentStatus.ASSIGNED.value,
            )
        )
        inactive.current_published_revision_id = inactive_revision.id
    db.commit()

    conflicts = roster_conflicts(
        db,
        person_id=person.id,
        start_date=date(2026, 10, 12),
        end_date=date(2026, 10, 18),
    )
    assert [(conflict.work_date, conflict.track_name, conflict.positions) for conflict in conflicts] == [
        (date(2026, 10, 14), "Published Track", ("Camera", "Director")),
        (date(2026, 10, 16), "First Draft Track", ("Operator",)),
    ]
