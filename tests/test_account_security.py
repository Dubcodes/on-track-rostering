from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import select

from app.auth.policy import actor_for
from app.auth.security import create_device, hash_credential
from app.auth.service import (
    activate_invitation,
    activate_pending_grants,
    approve_signup,
    create_invitation,
    grant_role,
    revoke_role_grant,
)
from app.catalog.models import Region
from app.core.enums import Role
from app.core.time import utcnow
from app.identity.models import Person, RoleGrant, SignupRequest, User, UserPersonLink


def _user(db, email: str, secret: str = "123456") -> User:  # type: ignore[no-untyped-def]
    user = User(
        email=email,
        display_name=email.split("@", 1)[0],
        credential_hash=hash_credential(secret),
        credential_kind="pin",
    )
    db.add(user)
    db.flush()
    return user


def test_pending_grant_never_authorizes_and_activates_on_primary_login(db) -> None:  # type: ignore[no-untyped-def]
    region = Region(name="Northern")
    db.add(region)
    user = _user(db, "candidate@example.test")
    grant = RoleGrant(
        user_id=user.id,
        role=Role.SUB_MANAGER.value,
        region_id=region.id,
        status="PENDING",
    )
    db.add(grant)
    db.commit()

    assert Role.SUB_MANAGER.value not in actor_for(db, user).roles_for(region.id)
    _, _, standard_device = create_device(db, user)
    assert standard_device.elevated is False

    assert activate_pending_grants(db, user, "123456") == 1
    db.refresh(grant)
    assert grant.status == "ACTIVE" and grant.activated_at is not None
    assert Role.SUB_MANAGER.value in actor_for(db, user).roles_for(region.id)
    _, _, elevated_device = create_device(db, user)
    assert elevated_device.elevated is True


def test_manager_can_only_stage_submanager_in_managed_region(db) -> None:  # type: ignore[no-untyped-def]
    managed, other = Region(name="Managed"), Region(name="Other")
    db.add_all([managed, other])
    manager = _user(db, "manager@example.test")
    target = _user(db, "target@example.test")
    person = Person(display_name="Target", home_region_id=managed.id)
    db.add(person)
    db.flush()
    db.add(UserPersonLink(user_id=target.id, person_id=person.id))
    db.add(RoleGrant(user_id=manager.id, role=Role.MANAGER.value, region_id=managed.id))
    db.commit()
    actor = actor_for(db, manager)

    grant = grant_role(
        db,
        actor=actor,
        target_user=target,
        role=Role.SUB_MANAGER.value,
        region_id=managed.id,
    )
    assert grant.status == "PENDING"
    with pytest.raises(PermissionError):
        grant_role(
            db,
            actor=actor,
            target_user=target,
            role=Role.EMPLOYEE.value,
            region_id=managed.id,
        )
    with pytest.raises(PermissionError):
        grant_role(
            db,
            actor=actor,
            target_user=target,
            role=Role.SUB_MANAGER.value,
            region_id=other.id,
        )


def test_final_active_admin_grant_cannot_be_revoked(db) -> None:  # type: ignore[no-untyped-def]
    admin = _user(db, "admin@example.test", "12345678")
    grant = RoleGrant(user_id=admin.id, role=Role.ADMIN.value, status="ACTIVE")
    db.add(grant)
    db.commit()
    actor = actor_for(db, admin)

    with pytest.raises(ValueError, match="final active Admin"):
        revoke_role_grant(db, actor=actor, grant=grant)
    assert db.scalar(select(RoleGrant.status).where(RoleGrant.id == grant.id)) == "ACTIVE"


def test_revoking_active_grant_invalidates_all_sessions(db) -> None:  # type: ignore[no-untyped-def]
    admin = _user(db, "admin@example.test", "12345678")
    target = _user(db, "manager@example.test")
    region = Region(name="Northern")
    db.add(region)
    db.flush()
    admin_grant = RoleGrant(user_id=admin.id, role=Role.ADMIN.value, status="ACTIVE")
    target_grant = RoleGrant(
        user_id=target.id, role=Role.MANAGER.value, region_id=region.id, status="ACTIVE"
    )
    db.add_all([admin_grant, target_grant])
    db.commit()
    _, _, device = create_device(db, target)
    old_epoch = target.auth_epoch

    revoke_role_grant(db, actor=actor_for(db, admin), grant=target_grant)
    db.refresh(target)
    db.refresh(device)
    assert target.auth_epoch == old_epoch + 1
    assert device.revoked_at is not None


def test_signup_approval_requires_explicit_available_person_link(db) -> None:  # type: ignore[no-untyped-def]
    region = Region(name="Northern")
    admin = _user(db, "admin@example.test", "12345678")
    person = Person(display_name="Existing Crew", email="new@example.com")
    signup = SignupRequest(
        email="new@example.com",
        display_name="New Account",
        requested_region_id=region.id,
    )
    db.add_all([region, person, signup])
    db.flush()
    db.add(RoleGrant(user_id=admin.id, role=Role.ADMIN.value, status="ACTIVE"))
    db.commit()

    raw = approve_signup(
        db,
        signup=signup,
        actor=actor_for(db, admin),
        role=Role.EMPLOYEE.value,
        region_id=region.id,
        person_id=person.id,
        create_person=False,
    )
    assert raw and signup.status == "APPROVED"
    assert signup.approved_person_id == person.id and signup.invitation_id is not None


def test_expired_and_revoked_invitation_body_tokens_are_rejected(db) -> None:  # type: ignore[no-untyped-def]
    admin = _user(db, "invite-admin@example.test", "12345678")
    region = Region(name="Invitations")
    db.add(region)
    db.flush()
    db.add(RoleGrant(user_id=admin.id, role=Role.ADMIN.value))
    db.commit()
    expired, expired_raw = create_invitation(
        db,
        email="expired@example.com",
        display_name="Expired",
        person_id=None,
        role=Role.EMPLOYEE.value,
        region_id=region.id,
        actor_user_id=admin.id,
    )
    expired.expires_at = utcnow() - timedelta(seconds=1)
    db.commit()
    with pytest.raises(ValueError, match="invalid, expired, or already used"):
        activate_invitation(db, expired_raw, "Expired", "123456")

    revoked, revoked_raw = create_invitation(
        db,
        email="revoked@example.com",
        display_name="Revoked",
        person_id=None,
        role=Role.EMPLOYEE.value,
        region_id=region.id,
        actor_user_id=admin.id,
    )
    revoked.revoked_at = utcnow()
    db.commit()
    with pytest.raises(ValueError, match="invalid, expired, or already used"):
        activate_invitation(db, revoked_raw, "Revoked", "123456")


def test_signup_link_collision_and_unrelated_manager_scope_are_rejected(db) -> None:  # type: ignore[no-untyped-def]
    north, south = Region(name="Northern"), Region(name="Southern")
    db.add_all([north, south])
    db.flush()
    manager = _user(db, "manager@example.test")
    linked_user = _user(db, "linked@example.test")
    person = Person(display_name="Already Linked")
    signup = SignupRequest(
        email="candidate@example.com",
        display_name="Candidate",
        requested_region_id=south.id,
    )
    in_scope_signup = SignupRequest(
        email="in-scope@example.com",
        display_name="In Scope",
        requested_region_id=north.id,
    )
    db.add_all([person, signup, in_scope_signup])
    db.flush()
    db.add_all(
        [
            RoleGrant(user_id=manager.id, role=Role.MANAGER.value, region_id=north.id),
            UserPersonLink(user_id=linked_user.id, person_id=person.id),
        ]
    )
    db.commit()
    actor = actor_for(db, manager)
    with pytest.raises(PermissionError):
        approve_signup(
            db,
            signup=signup,
            actor=actor,
            role=Role.EMPLOYEE.value,
            region_id=south.id,
            person_id=None,
            create_person=True,
        )
    with pytest.raises(ValueError, match="already linked"):
        approve_signup(
            db,
            signup=in_scope_signup,
            actor=actor,
            role=Role.EMPLOYEE.value,
            region_id=north.id,
            person_id=person.id,
            create_person=False,
        )
