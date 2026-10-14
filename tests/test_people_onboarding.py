from datetime import timedelta

from app.accounts.onboarding import apply_metadata_rows, onboarding_state, parse_metadata_csv
from app.catalog.models import Region
from app.core.time import utcnow
from app.identity.models import Invitation, Person, User, UserPersonLink


def test_person_metadata_worksheet_uses_uuid_and_does_not_create_user(db) -> None:  # type: ignore[no-untyped-def]
    region = Region(name="Onboarding Region")
    person = Person(display_name="Imported Person")
    db.add_all([region, person])
    db.commit()
    raw = (
        "person_id,display_name,email,primary_region,account_state\n"
        f"{person.id},Imported Person,CREW@Example.com,Onboarding Region,NEEDS_EMAIL\n"
    )
    rows = parse_metadata_csv(db, raw, None)
    assert rows[0].state == "READY"
    assert person.email is None and person.home_region_id is None
    assert apply_metadata_rows(db, rows) == 1
    assert (person.email, person.home_region_id) == ("crew@example.com", region.id)
    assert db.query(User).count() == 0
    assert onboarding_state(db, person) == "READY_TO_INVITE"


def test_person_onboarding_state_prioritizes_link_and_scope_validation(db) -> None:  # type: ignore[no-untyped-def]
    region = Region(name="Scoped Region")
    person = Person(display_name="Linked Person", email="linked@example.test")
    user = User(email="linked@example.test", display_name="Linked", credential_hash="unused")
    db.add_all([region, person, user])
    db.flush()
    person.home_region_id = region.id
    db.add(UserPersonLink(user_id=user.id, person_id=person.id))
    db.commit()
    assert onboarding_state(db, person) == "ACCOUNT_LINKED"
    raw = (
        "person_id,display_name,email,primary_region,account_state\n"
        "not-a-uuid,Linked Person,bad,Unknown,READY_TO_INVITE\n"
    )
    rows = parse_metadata_csv(db, raw, {region.id})
    assert rows[0].state == "ERROR"

    linked_raw = (
        "person_id,display_name,email,primary_region,account_state\n"
        f"{person.id},Linked Person,linked@example.test,Scoped Region,ACCOUNT_LINKED\n"
    )
    linked_rows = parse_metadata_csv(db, linked_raw, {region.id})
    assert linked_rows[0].state == "ERROR"
    assert "linked account" in linked_rows[0].message


def test_person_onboarding_invitation_and_archive_states(db) -> None:  # type: ignore[no-untyped-def]
    region = Region(name="Invitation State Region")
    db.add(region)
    db.flush()
    person = Person(
        display_name="Invitation State Person",
        email="invitation-state@example.com",
        home_region_id=region.id,
    )
    creator = User(email="creator@example.com", display_name="Creator", credential_hash="unused")
    db.add_all([person, creator])
    db.flush()
    invitation = Invitation(
        token_hash="pending-token-hash",
        email=person.email,
        display_name=person.display_name,
        person_id=person.id,
        role="EMPLOYEE",
        region_id=region.id,
        created_by_user_id=creator.id,
        expires_at=utcnow() + timedelta(hours=1),
    )
    db.add(invitation)
    db.flush()
    assert onboarding_state(db, person) == "INVITATION_PENDING"
    invitation.expires_at = utcnow() - timedelta(minutes=1)
    db.flush()
    assert onboarding_state(db, person) == "INVITATION_EXPIRED"
    person.lifecycle = "ARCHIVED"
    assert onboarding_state(db, person) == "ARCHIVED"
