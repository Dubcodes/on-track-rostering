from __future__ import annotations

import pytest
from sqlalchemy import select

from app.auth.policy import actor_for
from app.auth.security import create_device, hash_credential
from app.auth.service import activate_pending_grants, grant_role, revoke_role_grant
from app.catalog.models import Region
from app.core.enums import Role
from app.identity.models import RoleGrant, User


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
