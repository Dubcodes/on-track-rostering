from __future__ import annotations

import csv
import io
import uuid
from dataclasses import dataclass
from datetime import UTC

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth.service import validated_email
from app.catalog.models import Region
from app.core.time import utcnow
from app.identity.models import Invitation, Person, User, UserPersonLink

ONBOARDING_STATES = {
    "NEEDS_REGION", "NEEDS_EMAIL", "READY_TO_INVITE", "INVITATION_PENDING",
    "INVITATION_EXPIRED", "ACCOUNT_LINKED", "ARCHIVED",
}

ONBOARDING_STATE_ORDER = (
    "NEEDS_REGION", "NEEDS_EMAIL", "READY_TO_INVITE", "INVITATION_PENDING",
    "INVITATION_EXPIRED", "ACCOUNT_LINKED", "ARCHIVED",
)


@dataclass(frozen=True)
class MetadataRow:
    line: int
    person: Person | None
    person_id: str
    email: str
    region: Region | None
    state: str
    message: str


def onboarding_state(db: Session, person: Person) -> str:
    if person.lifecycle != "ACTIVE":
        return "ARCHIVED"
    if db.scalar(select(UserPersonLink.user_id).where(UserPersonLink.person_id == person.id)):
        return "ACCOUNT_LINKED"
    if person.home_region_id is None:
        return "NEEDS_REGION"
    if not person.email:
        return "NEEDS_EMAIL"
    invitation = db.scalar(
        select(Invitation)
        .where(
            Invitation.person_id == person.id,
            Invitation.consumed_at.is_(None),
            Invitation.revoked_at.is_(None),
        )
        .order_by(Invitation.created_at.desc())
    )
    if invitation:
        expires_at = invitation.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        return "INVITATION_PENDING" if expires_at > utcnow() else "INVITATION_EXPIRED"
    return "READY_TO_INVITE"


def onboarding_records(
    db: Session,
    *,
    allowed_region_ids: set[uuid.UUID] | None,
    query: str = "",
    region: str = "",
    state: str = "",
) -> list[dict[str, object]]:
    """Return the scoped, filtered Person onboarding view shared by Crew workflows."""
    statement = select(Person)
    if allowed_region_ids is not None:
        statement = statement.where(Person.home_region_id.in_(allowed_region_ids))
    records: list[dict[str, object]] = [
        {"person": person, "state": onboarding_state(db, person)}
        for person in db.scalars(statement.order_by(Person.display_name))
    ]
    term = query.strip().casefold()
    selected_region = region.strip()
    selected_state = state.strip().upper()
    if term:
        records = [
            item for item in records
            if term in item["person"].display_name.casefold()
            or term in (item["person"].email or "").casefold()
        ]
    if selected_region:
        records = [
            item for item in records
            if (selected_region == "NONE" and item["person"].home_region_id is None)
            or str(item["person"].home_region_id) == selected_region
        ]
    if selected_state:
        records = [item for item in records if item["state"] == selected_state]
    return records


def parse_metadata_csv(db: Session, raw: str, allowed_region_ids: set[uuid.UUID] | None) -> list[MetadataRow]:
    reader = csv.DictReader(io.StringIO(raw.lstrip("\ufeff")))
    required = {"person_id", "display_name", "email", "primary_region", "account_state"}
    if not reader.fieldnames or not required.issubset(reader.fieldnames):
        raise ValueError("Use the exported metadata worksheet columns.")
    regions = list(db.scalars(select(Region).where(Region.lifecycle == "ACTIVE")))
    regions_by_name = {" ".join(row.name.casefold().split()): row for row in regions}
    result: list[MetadataRow] = []
    seen: set[uuid.UUID] = set()
    for line, row in enumerate(reader, 2):
        errors: list[str] = []
        try:
            person_id = uuid.UUID((row.get("person_id") or "").strip())
        except ValueError:
            person_id = None
            errors.append("Invalid Person UUID.")
        person = db.get(Person, person_id) if person_id else None
        if person is None and person_id:
            errors.append("Unknown Person UUID.")
        elif person and person.lifecycle != "ACTIVE":
            errors.append("Person is archived.")
        elif person and allowed_region_ids is not None and person.home_region_id not in allowed_region_ids:
            errors.append("Person is outside your Region scope.")
        if person and (row.get("display_name") or "").strip() != person.display_name:
            errors.append("Display name does not match the Person UUID.")
        if person_id in seen:
            errors.append("Duplicate person_id row.")
        if person_id:
            seen.add(person_id)
        email = (row.get("email") or "").strip()
        if email:
            try:
                email = validated_email(email)
            except ValueError:
                errors.append("Invalid email address.")
                email = email.casefold()
        if email and person:
            if db.scalar(
                select(Person.id).where(func.lower(Person.email) == email, Person.id != person.id).limit(1)
            ) or db.scalar(select(User.id).where(func.lower(User.email) == email).limit(1)):
                errors.append("Normalized email is already in use.")
        supplied_state = (row.get("account_state") or "").strip().upper()
        if supplied_state not in ONBOARDING_STATES:
            errors.append("Unknown account state.")
        elif person and onboarding_state(db, person) == "ACCOUNT_LINKED":
            errors.append("Person already has a linked account.")
        region_name = " ".join((row.get("primary_region") or "").casefold().split())
        region = regions_by_name.get(region_name) if region_name else None
        if region_name and region is None:
            errors.append("Unknown primary Region.")
        if region and allowed_region_ids is not None and region.id not in allowed_region_ids:
            errors.append("Primary Region is outside your scope.")
        result.append(MetadataRow(
            line, person, str(person_id or row.get("person_id") or ""), email, region,
            "ERROR" if errors else "READY", " ".join(errors) or "Ready to apply.",
        ))
    normalized = [row.email for row in result if row.email]
    duplicates = {email for email in normalized if normalized.count(email) > 1}
    if duplicates:
        result = [
            MetadataRow(
                row.line, row.person, row.person_id, row.email, row.region,
                "ERROR" if row.email in duplicates else row.state,
                "Duplicate normalized email." if row.email in duplicates else row.message,
            )
            for row in result
        ]
    return result


def apply_metadata_rows(db: Session, rows: list[MetadataRow]) -> int:
    if not rows or any(row.state == "ERROR" for row in rows):
        raise ValueError("Resolve every Preview error before applying metadata.")
    for row in rows:
        assert row.person
        row.person.email = row.email or None
        row.person.home_region_id = row.region.id if row.region else None
    db.commit()
    return len(rows)
