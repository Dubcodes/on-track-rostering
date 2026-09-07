from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import timedelta

from email_validator import EmailNotValidError, validate_email
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.audit.service import record_audit
from app.auth.security import credential_error, hash_credential, token_hash
from app.core.enums import Role
from app.core.time import utcnow
from app.identity.models import Invitation, RoleGrant, User, UserPersonLink


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
) -> tuple[Invitation, str]:
    email = validated_email(email)
    if role not in {Role.CONTRACTOR.value, Role.EMPLOYEE.value, Role.SUB_MANAGER.value}:
        raise ValueError("Invitations may only grant Contractor, Employee, or Sub-Manager access.")
    if db.scalar(select(User.id).where(User.email == email)):
        raise ValueError("An account already uses that email.")
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
    db.commit()
    return invite, raw


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
    )
    db.add(user)
    db.flush()
    db.add(RoleGrant(user_id=user.id, role=Role.ADMIN.value, region_id=None, granted_by_user_id=None))
    record_audit(db, "admin.bootstrapped", "user", user.id, user.id)
    db.commit()
    return user
