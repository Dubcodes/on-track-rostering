from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import timedelta

from email_validator import EmailNotValidError, validate_email
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.audit.service import record_audit
from app.auth.policy import Actor, can_administer_person, can_administer_user, can_grant_role
from app.auth.security import credential_error, hash_credential, token_hash
from app.core.enums import Role
from app.core.time import utcnow
from app.identity.models import (
    Invitation,
    Person,
    RoleGrant,
    SignupRequest,
    TrustedDevice,
    User,
    UserPersonLink,
)


def normalise_email(email: str) -> str:
    return email.strip().casefold()


def validated_email(email: str) -> str:
    try:
        return validate_email(email, check_deliverability=False).normalized.casefold()
    except EmailNotValidError as exc:
        raise ValueError("Enter a valid email address.") from exc


def create_invitation(
    db: Session,
    *,
    email: str,
    display_name: str,
    person_id: uuid.UUID | None,
    role: str,
    region_id: uuid.UUID | None,
    actor_user_id: uuid.UUID,
    commit: bool = True,
) -> tuple[Invitation, str]:
    email = validated_email(email)
    if role not in {Role.CONTRACTOR.value, Role.EMPLOYEE.value, Role.SUB_MANAGER.value}:
        raise ValueError("Invitations may only grant Contractor, Employee, or Sub-Manager access.")
    if db.scalar(select(User.id).where(User.email == email)):
        raise ValueError("An account already uses that email.")
    if role != Role.ADMIN.value and region_id is None:
        raise ValueError("A region is required for this invitation.")
    if person_id:
        person = db.get(Person, person_id)
        if not person:
            raise ValueError("The selected crew identity does not exist.")
        if db.scalar(select(UserPersonLink.user_id).where(UserPersonLink.person_id == person_id)):
            raise ValueError("The selected crew identity is already linked to an account.")
    now = utcnow()
    db.execute(
        update(Invitation)
        .where(Invitation.email == email, Invitation.consumed_at.is_(None), Invitation.revoked_at.is_(None))
        .values(revoked_at=now)
    )
    raw = secrets.token_urlsafe(40)
    invite = Invitation(
        token_hash=token_hash(raw),
        email=email,
        display_name=display_name.strip(),
        person_id=person_id,
        role=role,
        region_id=region_id,
        created_by_user_id=actor_user_id,
        expires_at=now + timedelta(days=1),
    )
    db.add(invite)
    db.flush()
    record_audit(
        db,
        "invitation.created",
        "invitation",
        invite.id,
        actor_user_id,
        region_id=region_id,
        detail={"email_hash": hashlib.sha256(email.encode()).hexdigest()[:16], "role": role},
    )
    if commit:
        db.commit()
    return invite, raw


def activate_pending_grants(
    db: Session, user: User, secret: str = "", *, strong_auth: bool = False
) -> int:
    # A passkey/TOTP can prove a strong login but cannot upgrade the stored primary credential policy.
    _ = strong_auth
    pending = list(
        db.scalars(
            select(RoleGrant)
            .where(RoleGrant.user_id == user.id, RoleGrant.status == "PENDING")
            .with_for_update()
        )
    )
    now = utcnow()
    activated = 0
    admin_eligible = bool(user.credential_admin_eligible)
    if secret and not credential_error(secret, Role.ADMIN.value):
        user.credential_admin_eligible = True
        admin_eligible = True
    for grant in pending:
        if grant.role == Role.ADMIN.value and not admin_eligible:
            continue
        grant.status = "ACTIVE"
        grant.activated_at = now
        activated += 1
        record_audit(
            db,
            "role_grant.activated",
            "role_grant",
            grant.id,
            user.id,
            region_id=grant.region_id,
            detail={"role": grant.role},
        )
    if activated:
        user.auth_epoch += 1
        db.commit()
    return activated


def grant_role(
    db: Session,
    *,
    actor: Actor,
    target_user: User,
    role: str,
    region_id: uuid.UUID | None,
) -> RoleGrant:
    if not can_grant_role(actor, role, region_id):
        raise PermissionError("You cannot grant that role and scope.")
    if not actor.is_admin and region_id is not None and not can_administer_user(
        db, actor, target_user, region_id
    ):
        raise PermissionError("That account is outside your regional administration scope.")
    existing = db.scalar(
        select(RoleGrant).where(
            RoleGrant.user_id == target_user.id,
            RoleGrant.role == role,
            RoleGrant.region_id == region_id,
            RoleGrant.status != "REVOKED",
        )
    )
    if existing:
        raise ValueError("That role grant is already active or pending.")
    grant = RoleGrant(
        user_id=target_user.id,
        role=role,
        region_id=region_id,
        status="PENDING",
        granted_by_user_id=actor.user_id,
    )
    db.add(grant)
    db.flush()
    record_audit(
        db,
        "role_grant.pending",
        "role_grant",
        grant.id,
        actor.user_id,
        region_id=region_id,
        detail={"role": role, "target_user_id": str(target_user.id)},
    )
    db.commit()
    return grant


def revoke_role_grant(db: Session, *, actor: Actor, grant: RoleGrant) -> None:
    if grant.status == "REVOKED":
        raise ValueError("That role grant is already revoked.")
    if not can_grant_role(actor, grant.role, grant.region_id):
        raise PermissionError("You cannot revoke that role and scope.")
    if grant.role == Role.ADMIN.value and grant.status == "ACTIVE":
        active_admins = db.scalar(
            select(RoleGrant.id).where(
                RoleGrant.role == Role.ADMIN.value,
                RoleGrant.status == "ACTIVE",
                RoleGrant.id != grant.id,
            ).limit(1)
        )
        if not active_admins:
            raise ValueError("The final active Admin grant cannot be revoked.")
    now = utcnow()
    was_active = grant.status == "ACTIVE"
    grant.status = "REVOKED"
    grant.revoked_at = now
    target = db.get(User, grant.user_id)
    if was_active and target:
        target.auth_epoch += 1
        for device in db.scalars(
            select(TrustedDevice).where(
                TrustedDevice.user_id == target.id, TrustedDevice.revoked_at.is_(None)
            )
        ):
            device.revoked_at = now
    record_audit(
        db,
        "role_grant.revoked",
        "role_grant",
        grant.id,
        actor.user_id,
        region_id=grant.region_id,
        detail={"role": grant.role},
    )
    db.commit()


def approve_signup(
    db: Session,
    *,
    signup: SignupRequest,
    actor: Actor,
    role: str,
    region_id: uuid.UUID,
    person_id: uuid.UUID | None,
    create_person: bool,
) -> str:
    if signup.status != "PENDING":
        raise ValueError("Signup request is no longer pending.")
    if role not in {Role.EMPLOYEE.value, Role.CONTRACTOR.value}:
        raise ValueError("Signup approval may only grant Employee or Contractor access.")
    if not (actor.is_admin or Role.MANAGER.value in actor.roles_for(region_id)):
        raise PermissionError("You cannot approve accounts for that region.")
    if not actor.is_admin and signup.requested_region_id != region_id:
        raise PermissionError("That signup request is outside this regional scope.")
    if create_person == bool(person_id):
        raise ValueError("Choose exactly one: link an existing person or create a new person.")
    if create_person:
        person = Person(
            display_name=signup.display_name,
            email=signup.email,
            home_region_id=region_id,
        )
        db.add(person)
        db.flush()
        person_id = person.id
    else:
        person = db.get(Person, person_id)
        if not person:
            raise ValueError("The selected crew identity does not exist.")
        if person.lifecycle != "ACTIVE":
            raise ValueError("The selected crew identity is archived.")
        if db.scalar(select(UserPersonLink.user_id).where(UserPersonLink.person_id == person.id)):
            raise ValueError("That crew identity is already linked to an account.")
        if not actor.is_admin and person.home_region_id is None:
            raise PermissionError("An unscoped crew identity cannot be linked through signup approval.")
        if not actor.is_admin and not can_administer_person(actor, person, region_id):
            raise PermissionError("The selected crew identity is outside this regional scope.")
    assert person_id is not None
    invitation, raw = create_invitation(
        db,
        email=signup.email,
        display_name=signup.display_name,
        person_id=person_id,
        role=role,
        region_id=region_id,
        actor_user_id=actor.user_id,
        commit=False,
    )
    signup.status = "APPROVED"
    signup.reviewed_by_user_id = actor.user_id
    signup.reviewed_at = utcnow()
    signup.approved_person_id = person_id
    signup.invitation_id = invitation.id
    record_audit(
        db,
        "signup.approved",
        "signup_request",
        signup.id,
        actor.user_id,
        region_id=region_id,
        detail={"role": role, "person_id": str(person_id)},
    )
    db.commit()
    return raw


def activate_invitation(db: Session, raw_token: str, display_name: str, secret: str) -> User:
    invite = db.scalar(
        select(Invitation).where(Invitation.token_hash == token_hash(raw_token)).with_for_update()
    )
    now = utcnow()
    if not invite or invite.consumed_at or invite.revoked_at or invite.expires_at <= now:
        raise ValueError("This invitation is invalid, expired, or already used.")
    if error := credential_error(secret, invite.role):
        raise ValueError(error)
    if db.scalar(select(User.id).where(User.email == invite.email)):
        raise ValueError("This invitation is invalid, expired, or already used.")
    user = User(
        email=invite.email,
        display_name=(display_name.strip() or invite.display_name),
        credential_hash=hash_credential(secret),
        credential_kind="pin" if secret.isdigit() else "password",
        credential_admin_eligible=not bool(credential_error(secret, Role.ADMIN.value)),
    )
    db.add(user)
    db.flush()
    if invite.person_id:
        if db.scalar(select(UserPersonLink.user_id).where(UserPersonLink.person_id == invite.person_id)):
            raise ValueError("The linked crew identity is no longer available.")
        db.add(UserPersonLink(user_id=user.id, person_id=invite.person_id))
    db.add(
        RoleGrant(
            user_id=user.id,
            role=invite.role,
            region_id=invite.region_id,
            granted_by_user_id=invite.created_by_user_id,
        )
    )
    invite.consumed_at = now
    invite.activated_user_id = user.id
    record_audit(db, "invitation.activated", "user", user.id, user.id, region_id=invite.region_id)
    db.commit()
    return user


def create_admin(db: Session, email: str, display_name: str, secret: str) -> User:
    if error := credential_error(secret, Role.ADMIN.value):
        raise ValueError(error)
    email = validated_email(email)
    if db.scalar(select(User.id).where(User.email == email)):
        raise ValueError("An account already uses that email.")
    user = User(
        email=email,
        display_name=display_name.strip(),
        credential_hash=hash_credential(secret),
        credential_kind="pin" if secret.isdigit() else "password",
        credential_admin_eligible=True,
    )
    db.add(user)
    db.flush()
    db.add(RoleGrant(user_id=user.id, role=Role.ADMIN.value, region_id=None, granted_by_user_id=None))
    record_audit(db, "admin.bootstrapped", "user", user.id, user.id)
    db.commit()
    return user
