from __future__ import annotations

import hashlib
import uuid

from email_validator import EmailNotValidError, validate_email
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.core.enums import Lifecycle
from app.identity.models import Person, User, UserPersonLink


def validated_email(value: str) -> str:
    """Return the one canonical representation used by Person and User identities."""
    try:
        return validate_email(value, check_deliverability=False).normalized.casefold()
    except EmailNotValidError as exc:
        raise ValueError("Enter a valid email address.") from exc


def _email_lock_key(email: str) -> int:
    raw = int.from_bytes(hashlib.sha256(email.encode("utf-8")).digest()[:8], "big")
    return raw - (1 << 64) if raw >= (1 << 63) else raw


def lock_work_email(db: Session, email: str) -> None:
    """Serialize normalized-email identity writes on PostgreSQL, including absent rows."""
    if db.get_bind().dialect.name == "postgresql":
        db.execute(
            text("SELECT pg_advisory_xact_lock(:key)"),
            {"key": _email_lock_key(email)},
        )


def validate_person_work_email(
    db: Session,
    value: str | None,
    *,
    person_id: uuid.UUID | None = None,
    lifecycle: str = Lifecycle.ACTIVE.value,
    lock: bool = True,
) -> str | None:
    """Validate an active Person email without merging or claiming an unlinked User.

    An unlinked User with the same normalized email is intentionally allowed so the
    existing reconciliation workflow can link it. A User already linked to this
    Person is also valid, but its login email must remain aligned.
    """
    if value is None or not value.strip():
        if person_id:
            linked_user = db.scalar(
                select(User)
                .join(UserPersonLink, UserPersonLink.user_id == User.id)
                .where(UserPersonLink.person_id == person_id)
            )
            if linked_user:
                raise ValueError("A Person with a linked account must retain its matching work email.")
        return None
    try:
        email = validated_email(value)
    except ValueError:
        existing = db.get(Person, person_id) if person_id else None
        legacy_email = (existing.email or "").strip().casefold() if existing else ""
        supplied_email = value.strip().casefold()
        if not legacy_email or supplied_email != legacy_email:
            raise
        # Preserve an unchanged legacy value so an unrelated Person edit is not
        # blocked; authorized diagnostics still flag it for deliberate repair.
        email = legacy_email
    if lock:
        lock_work_email(db, email)
    if lifecycle == Lifecycle.ACTIVE.value:
        duplicate_query = select(Person.id).where(
            Person.lifecycle == Lifecycle.ACTIVE.value,
            func.lower(Person.email) == email,
        )
        if person_id:
            duplicate_query = duplicate_query.where(Person.id != person_id)
        duplicate = db.scalar(
            duplicate_query.limit(1)
        )
        if duplicate:
            raise ValueError(
                "Another active Person already uses that work email. Find and manage them in Crew."
            )
    if person_id:
        linked_user = db.scalar(
            select(User)
            .join(UserPersonLink, UserPersonLink.user_id == User.id)
            .where(UserPersonLink.person_id == person_id)
        )
        if linked_user and linked_user.email.casefold() != email:
            raise ValueError("Work email must match this Person's linked account email.")
    account = db.scalar(select(User).where(func.lower(User.email) == email).limit(1))
    if account:
        link = db.get(UserPersonLink, account.id)
        if link and link.person_id != person_id:
            raise ValueError("That email belongs to an account linked to a different Person.")
    return email


def person_email_issues(db: Session, person: Person) -> tuple[str, ...]:
    """Describe legacy identity conflicts without changing historical records."""
    if not person.email:
        linked = db.scalar(
            select(User.id)
            .join(UserPersonLink, UserPersonLink.user_id == User.id)
            .where(UserPersonLink.person_id == person.id)
            .limit(1)
        )
        return ("Linked account has no matching Person work email.",) if linked else ()
    try:
        email = validated_email(person.email)
    except ValueError:
        return ("Person work email is invalid.",)
    issues: list[str] = []
    duplicate = db.scalar(
        select(Person.id)
        .where(
            Person.lifecycle == Lifecycle.ACTIVE.value,
            func.lower(Person.email) == email,
            Person.id != person.id,
        )
        .limit(1)
    )
    if person.lifecycle == Lifecycle.ACTIVE.value and duplicate:
        issues.append("Another active Person uses this work email.")
    linked_user = db.scalar(
        select(User)
        .join(UserPersonLink, UserPersonLink.user_id == User.id)
        .where(UserPersonLink.person_id == person.id)
    )
    if linked_user and linked_user.email.casefold() != email:
        issues.append("Person work email does not match the linked account email.")
    account = db.scalar(select(User).where(func.lower(User.email) == email).limit(1))
    if account:
        link = db.get(UserPersonLink, account.id)
        if link and link.person_id != person.id:
            issues.append("Work email belongs to an account linked to another Person.")
    return tuple(issues)
