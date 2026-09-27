"""Provision Employee accounts for already-imported neutral staging People."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.audit.service import record_audit
from app.auth.security import credential_error, hash_credential
from app.catalog.models import Region
from app.core.enums import Role
from app.core.time import utcnow
from app.external_calendar.schemas import ImportBundle
from app.identity.models import Person, RoleGrant, TrustedDevice, User, UserPersonLink


def provision(
    db: Session,
    *,
    bundle: ImportBundle,
    pin: str,
    actor: User,
    limit: int,
    reset_existing_pin: bool,
) -> list[tuple[str, str]]:
    if error := credential_error(pin, Role.EMPLOYEE.value):
        raise ValueError(error)
    if not db.scalar(
        select(RoleGrant.id).where(
            RoleGrant.user_id == actor.id,
            RoleGrant.role == Role.ADMIN.value,
            RoleGrant.status == "ACTIVE",
        )
    ):
        raise ValueError("--actor-email must identify an active Admin account")

    candidates = [row for row in bundle.people if row.email and row.home_region][:limit]
    if not candidates:
        raise ValueError("The bundle contains no People with both email and home_region")
    results: list[tuple[str, str]] = []
    for row in candidates:
        email = str(row.email).casefold()
        person = db.scalar(select(Person).where(Person.email == email))
        if not person:
            raise ValueError(f"Import the neutral bundle first; Person not found: {email}")
        region = db.scalar(select(Region).where(Region.name == row.home_region))
        if not region or person.home_region_id != region.id:
            raise ValueError(f"Imported Person has an unexpected home Region: {email}")

        user = db.scalar(select(User).where(User.email == email))
        state = "existing"
        if not user:
            user = User(
                email=email,
                display_name=person.display_name,
                credential_hash=hash_credential(pin),
                credential_kind="pin" if pin.isdigit() else "password",
                credential_admin_eligible=False,
            )
            db.add(user)
            db.flush()
            db.add(UserPersonLink(user_id=user.id, person_id=person.id))
            state = "created"
        else:
            if user.status != "ACTIVE":
                raise ValueError(f"Existing account is not active: {email}")
            link = db.scalar(select(UserPersonLink).where(UserPersonLink.user_id == user.id))
            if not link or link.person_id != person.id:
                raise ValueError(f"Existing account is not linked to the expected Person: {email}")
            if reset_existing_pin:
                if db.scalar(
                    select(RoleGrant.id).where(
                        RoleGrant.user_id == user.id,
                        RoleGrant.role == Role.ADMIN.value,
                        RoleGrant.status == "ACTIVE",
                    )
                ):
                    raise ValueError("This staging utility will not reset an Admin credential")
                user.credential_hash = hash_credential(pin)
                user.credential_kind = "pin" if pin.isdigit() else "password"
                user.credential_admin_eligible = False
                user.auth_epoch += 1
                for device in db.scalars(
                    select(TrustedDevice).where(
                        TrustedDevice.user_id == user.id, TrustedDevice.revoked_at.is_(None)
                    )
                ):
                    device.revoked_at = utcnow()
                state = "credential reset"

        grant = db.scalar(
            select(RoleGrant).where(
                RoleGrant.user_id == user.id,
                RoleGrant.role == Role.EMPLOYEE.value,
                RoleGrant.region_id == region.id,
                RoleGrant.status != "REVOKED",
            )
        )
        if not grant:
            db.add(
                RoleGrant(
                    user_id=user.id,
                    role=Role.EMPLOYEE.value,
                    region_id=region.id,
                    status="ACTIVE",
                    granted_by_user_id=actor.id,
                )
            )
        record_audit(
            db,
            "staging_account.provisioned",
            "user",
            user.id,
            actor.id,
            region_id=region.id,
            detail={"state": state, "role": Role.EMPLOYEE.value, "person_id": str(person.id)},
        )
        results.append((email, state))
    db.commit()
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--actor-email", required=True)
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL"))
    parser.add_argument("--pin", default=os.getenv("ONTRACK_STAGING_SEED_PIN"))
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--reset-existing-pin", action="store_true")
    args = parser.parse_args()
    if not args.database_url:
        parser.error("Set DATABASE_URL or pass --database-url")
    if not args.pin:
        parser.error("Set ONTRACK_STAGING_SEED_PIN or pass --pin")
    if not 1 <= args.limit <= 25:
        parser.error("--limit must be between 1 and 25")
    bundle = ImportBundle.model_validate_json(args.bundle.read_text(encoding="utf-8"))
    engine = create_engine(args.database_url, pool_pre_ping=True)
    with Session(engine) as db:
        actor = db.scalar(select(User).where(User.email == args.actor_email.strip().casefold()))
        if not actor:
            parser.error("--actor-email account was not found")
        results = provision(
            db,
            bundle=bundle,
            pin=args.pin,
            actor=actor,
            limit=args.limit,
            reset_existing_pin=args.reset_existing_pin,
        )
    for email, state in results:
        print(f"{email}: {state}")


if __name__ == "__main__":
    main()
