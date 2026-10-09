from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, time, timedelta

import pytest

from app.auth.policy import actor_for, can_crew_view, can_manage_region
from app.auth.security import hash_credential
from app.auth.service import activate_invitation, activate_pending_grants, create_invitation, grant_role
from app.catalog.models import Region
from app.core.enums import Role
from app.identity.contractor_access import (
    latest_future_participation_date,
    refresh_contractor_access,
)
from app.identity.models import Person, RoleGrant, User, UserPersonLink
from app.rostering.models import Assignment, Workday, WorkdayRevision


def _contractor(db):  # type: ignore[no-untyped-def]
    region = Region(name="Contractor Region")
    manager = User(
        email="manager@example.test",
        display_name="Manager",
        credential_hash=hash_credential("123456"),
    )
    person = Person(
        display_name="Temporary Camera Operator",
        email="contractor@example.com",
        home_region_id=region.id,
    )
    user = User(
        email="contractor@example.com",
        display_name="Temporary Camera Operator",
        credential_hash=hash_credential("654321"),
    )
    db.add_all([region, manager, person, user])
    db.flush()
    db.add_all(
        [
            UserPersonLink(user_id=user.id, person_id=person.id),
            RoleGrant(user_id=user.id, role=Role.CONTRACTOR.value, region_id=region.id),
            RoleGrant(user_id=manager.id, role=Role.MANAGER.value, region_id=region.id),
        ]
    )
    db.commit()
    return region, manager, person, user


def _published_day(
    db,
    *,
    region_id,
    actor_id,
    person_id,
    work_date: date,
    positions: int = 1,
    published: bool = True,
    generated: bool = False,
):  # type: ignore[no-untyped-def]
    parent = None
    if generated:
        parent = Workday(region_id=region_id, created_by_user_id=actor_id)
        db.add(parent)
        db.flush()
    workday = Workday(
        region_id=region_id,
        created_by_user_id=actor_id,
        generated_from_workday_id=parent.id if parent else None,
    )
    db.add(workday)
    db.flush()
    revision = WorkdayRevision(
        workday_id=workday.id,
        revision_number=1,
        state="PUBLISHED" if published else "DRAFT",
        work_date=work_date,
        title="Contractor day",
        track_name_snapshot="Track",
        created_by_user_id=actor_id,
        published_by_user_id=actor_id if published else None,
    )
    db.add(revision)
    db.flush()
    if published:
        workday.current_published_revision_id = revision.id
    else:
        workday.current_draft_revision_id = revision.id
    db.add_all(
        [
            Assignment(
                revision_id=revision.id,
                slot_key=uuid.uuid4(),
                display_name_snapshot=f"Position {index + 1}",
                person_id=person_id,
                person_name_snapshot="Temporary Camera Operator",
                status="ASSIGNED",
                start_time=time(8),
                end_time=time(17),
            )
            for index in range(positions)
        ]
    )
    db.commit()
    return workday


def test_contractor_expiry_uses_authoritative_latest_day_and_never_shortens(db) -> None:  # type: ignore[no-untyped-def]
    region, manager, person, user = _contractor(db)
    now = datetime(2026, 10, 1, 0, 0, tzinfo=UTC)
    first = _published_day(
        db,
        region_id=region.id,
        actor_id=manager.id,
        person_id=person.id,
        work_date=date(2026, 11, 10),
        positions=2,
    )
    _published_day(
        db,
        region_id=region.id,
        actor_id=manager.id,
        person_id=person.id,
        work_date=date(2026, 12, 1),
        published=False,
    )
    _published_day(
        db,
        region_id=region.id,
        actor_id=manager.id,
        person_id=person.id,
        work_date=date(2026, 11, 30),
        generated=True,
    )

    assert latest_future_participation_date(db, user.id, today=now.date()) == date(2026, 11, 10)
    initial = refresh_contractor_access(db, user, now=now, reason="test")
    assert initial.expires_at is not None
    assert initial.expires_at.astimezone(UTC).date() == date(2026, 12, 10)
    original_expiry = initial.expires_at
    db.commit()

    first.status = "CANCELLED"
    db.commit()
    earlier = refresh_contractor_access(db, user, now=now + timedelta(days=1), reason="test")
    assert earlier.expires_at == original_expiry

    _published_day(
        db,
        region_id=region.id,
        actor_id=manager.id,
        person_id=person.id,
        work_date=date(2026, 11, 20),
    )
    later = refresh_contractor_access(db, user, now=now + timedelta(days=2), reason="test")
    assert later.expires_at is not None and later.expires_at > original_expiry
    assert later.expires_at.astimezone(UTC).date() == date(2026, 12, 20)


def test_manual_extension_expiry_and_reactivation_preserve_person_and_roster(db) -> None:  # type: ignore[no-untyped-def]
    region, manager, person, user = _contractor(db)
    workday = _published_day(
        db,
        region_id=region.id,
        actor_id=manager.id,
        person_id=person.id,
        work_date=date(2026, 1, 1),
    )
    expired_anchor = datetime(2026, 1, 1, tzinfo=UTC)
    user.contractor_manual_extension_at = expired_anchor
    user.contractor_access_expires_at = expired_anchor + timedelta(days=30)
    db.commit()

    expired = refresh_contractor_access(
        db, user, now=datetime(2026, 3, 1, tzinfo=UTC), reason="test"
    )
    assert expired.status == "EXPIRED"
    assert db.get(Person, person.id) is not None
    assert db.get(Workday, workday.id) is not None

    extension = datetime(2026, 3, 1, tzinfo=UTC)
    active = refresh_contractor_access(
        db,
        user,
        now=extension,
        manual_extension_at=extension,
        actor_user_id=manager.id,
        reason="manual extension",
    )
    assert active.status == "ACTIVE"
    assert active.expires_at == extension + timedelta(days=30)


def test_contractor_invitation_links_once_and_has_personal_only_authority(db) -> None:  # type: ignore[no-untyped-def]
    region = Region(name="Invitation Region")
    manager = User(
        email="manager@example.test",
        display_name="Manager",
        credential_hash=hash_credential("123456"),
    )
    person = Person(
        display_name="Invited Contractor",
        email="invited@example.com",
        home_region_id=region.id,
    )
    db.add_all([region, manager, person])
    db.flush()
    db.add(RoleGrant(user_id=manager.id, role=Role.MANAGER.value, region_id=region.id))
    db.commit()
    _invite, raw = create_invitation(
        db,
        email=person.email or "",
        display_name=person.display_name,
        person_id=person.id,
        role=Role.CONTRACTOR.value,
        region_id=region.id,
        actor_user_id=manager.id,
    )

    contractor = activate_invitation(db, raw, person.display_name, "654321")
    link = db.get(UserPersonLink, contractor.id)
    assert link is not None and link.person_id == person.id
    assert contractor.contractor_access_expires_at is not None
    actor = actor_for(db, contractor)
    assert actor.person_id == person.id
    assert not can_crew_view(actor, region.id)
    assert not can_manage_region(actor, region.id)
    db.add_all(
        [
            RoleGrant(user_id=contractor.id, role=Role.EMPLOYEE.value, region_id=region.id),
            RoleGrant(user_id=contractor.id, role=Role.MANAGER.value, region_id=region.id),
        ]
    )
    db.commit()
    inconsistent_actor = actor_for(db, contractor)
    assert inconsistent_actor.roles_for(region.id) == frozenset({Role.CONTRACTOR.value})
    assert not can_crew_view(inconsistent_actor, region.id)
    assert not can_manage_region(inconsistent_actor, region.id)
    with pytest.raises(ValueError, match="already used"):
        activate_invitation(db, raw, person.display_name, "654321")


def test_staged_contractor_grant_requires_person_and_initializes_expiry(db) -> None:  # type: ignore[no-untyped-def]
    region = Region(name="Grant Region")
    db.add(region)
    db.flush()
    admin = User(
        email="admin@example.com",
        display_name="Admin",
        credential_hash=hash_credential("12345678"),
    )
    target = User(
        email="grant-contractor@example.com",
        display_name="Grant Contractor",
        credential_hash=hash_credential("654321"),
    )
    unlinked = User(
        email="unlinked@example.com",
        display_name="Unlinked",
        credential_hash=hash_credential("654321"),
    )
    person = Person(display_name="Grant Contractor", home_region_id=region.id)
    db.add_all([admin, target, unlinked, person])
    db.flush()
    db.add_all(
        [
            RoleGrant(user_id=admin.id, role=Role.ADMIN.value),
            UserPersonLink(user_id=target.id, person_id=person.id),
        ]
    )
    db.commit()
    admin_actor = actor_for(db, admin)
    with pytest.raises(ValueError, match="linked crew identity"):
        grant_role(
            db,
            actor=admin_actor,
            target_user=unlinked,
            role=Role.CONTRACTOR.value,
            region_id=region.id,
        )
    grant = grant_role(
        db,
        actor=admin_actor,
        target_user=target,
        role=Role.CONTRACTOR.value,
        region_id=region.id,
    )
    assert grant.status == "PENDING"
    assert activate_pending_grants(db, target, "654321") == 1
    assert target.contractor_manual_extension_at is not None
    assert target.contractor_access_expires_at is not None
