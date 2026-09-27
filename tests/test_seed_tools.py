from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from sqlalchemy import func, select

from app.core.enums import Role
from app.external_calendar.importer import apply_bundle
from app.external_calendar.schemas import ImportBundle
from app.identity.models import RoleGrant, User, UserPersonLink
from scripts.create_staging_accounts import provision
from scripts.export_redeputy_seed import export_bundle


def test_redeputy_export_is_read_only_sanitized_and_neutral(tmp_path) -> None:  # type: ignore[no-untyped-def]
    source = tmp_path / "redeputy.sqlite3"
    with sqlite3.connect(source) as db:
        db.executescript(
            """
            CREATE TABLE crew_people (
                id INTEGER PRIMARY KEY,
                canonical_display_name TEXT NOT NULL,
                is_active INTEGER NOT NULL
            );
            CREATE TABLE roster_day_assignments (
                assignee_label TEXT,
                position_label TEXT
            );
            CREATE TABLE workday_role_catalogue (
                display_label TEXT,
                is_active INTEGER NOT NULL
            );
            INSERT INTO crew_people VALUES (1, 'Real Reference Name', 1);
            INSERT INTO roster_day_assignments VALUES ('Real Reference Name', 'Gimble');
            INSERT INTO workday_role_catalogue VALUES ('Side 1', 1);
            """
        )

    bundle = export_bundle(source)

    validated = ImportBundle.model_validate(bundle)
    assert validated.people[0].display_name == "Staging Crew 01"
    assert validated.people[0].email == "staging.crew01@example.invalid"
    assert {row.name for row in validated.positions} == {"Gimbal", "Side 1"}
    assert validated.people[0].capabilities[0].signal.value == "WORKED"
    with sqlite3.connect(source) as db:
        assert db.execute("SELECT canonical_display_name FROM crew_people").fetchone()[0] == (
            "Real Reference Name"
        )


def test_staging_account_provision_is_idempotent(db) -> None:  # type: ignore[no-untyped-def]
    actor = User(email="seed-admin@example.test", display_name="Seed Admin", credential_hash="unused")
    db.add(actor)
    db.flush()
    db.add(RoleGrant(user_id=actor.id, role=Role.ADMIN.value, region_id=None, status="ACTIVE"))
    db.commit()
    bundle = ImportBundle.model_validate(
        {
            "version": "1",
            "regions": [{"name": "Northern"}],
            "crew_groups": [{"name": "Camera Crew"}],
            "positions": [{"name": "Side 1", "crew_group": "Camera Crew"}],
            "people": [
                {
                    "display_name": "Staging Person",
                    "email": "staging.person@example.invalid",
                    "home_region": "Northern",
                    "crew_groups": ["Camera Crew"],
                    "capabilities": [
                        {"crew_group": "Camera Crew", "position": "Side 1", "signal": "WORKED"}
                    ],
                }
            ],
        }
    )
    apply_bundle(db, bundle.model_dump(mode="json"), actor.id)
    db.commit()

    assert provision(
        db, bundle=bundle, pin="123456", actor=actor, limit=5, reset_existing_pin=False
    ) == [("staging.person@example.invalid", "created")]
    assert provision(
        db, bundle=bundle, pin="654321", actor=actor, limit=5, reset_existing_pin=False
    ) == [("staging.person@example.invalid", "existing")]
    assert db.scalar(select(func.count()).select_from(UserPersonLink)) == 1
    assert (
        db.scalar(
            select(func.count()).select_from(RoleGrant).where(RoleGrant.role == Role.EMPLOYEE.value)
        )
        == 1
    )


def test_committed_staging_bundle_contains_only_synthetic_people() -> None:
    payload = json.loads(Path("seed/redeputy-staging-neutral.json").read_text(encoding="utf-8"))
    bundle = ImportBundle.model_validate(payload)
    assert len(bundle.regions) == 2
    assert len(bundle.positions) == 16
    assert len(bundle.people) == 12
    assert all(person.email and person.email.endswith("@example.invalid") for person in bundle.people)
    assert all(person.capabilities or person.display_name == "Emery New" for person in bundle.people)
