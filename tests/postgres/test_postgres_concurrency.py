from __future__ import annotations

import os
import threading
import uuid
from datetime import date

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.auth.security import hash_credential
from app.auth.service import activate_pending_grants
from app.catalog.models import BasePosition, Region
from app.identity.models import Person, RoleGrant, User, UserPersonLink
from app.notifications.models import NotificationEvent
from app.rostering.models import OpenPositionApplication, Workday, WorkdayRevision
from app.rostering.service import (
    AssignmentInput,
    PublishConflict,
    add_assignment,
    create_workday,
    decline_published_assignment,
    ensure_draft,
    publish,
)

POSTGRES_URL = os.environ.get("ONTRACK_TEST_DATABASE_URL", "")
pytestmark = pytest.mark.skipif(not POSTGRES_URL, reason="real PostgreSQL test URL is not configured")


@pytest.fixture
def pg_factory():  # type: ignore[no-untyped-def]
    engine = create_engine(POSTGRES_URL, pool_pre_ping=True)
    assert engine.dialect.name == "postgresql"
    factory = sessionmaker(engine, expire_on_commit=False)
    yield factory
    engine.dispose()


def _authority(factory) -> tuple[uuid.UUID, uuid.UUID]:  # type: ignore[no-untyped-def]
    suffix = uuid.uuid4().hex[:10]
    with factory() as db:
        region = Region(name=f"PG Region {suffix}")
        user = User(
            email=f"pg-{suffix}@example.com",
            display_name="PostgreSQL Manager",
            credential_hash=hash_credential("123456"),
            credential_kind="pin",
        )
        db.add_all([region, user])
        db.flush()
        db.add(RoleGrant(user_id=user.id, role="MANAGER", region_id=region.id))
        db.commit()
        return user.id, region.id


def test_concurrent_publish_preserves_one_authoritative_winner(pg_factory) -> None:  # type: ignore[no-untyped-def]
    user_id, region_id = _authority(pg_factory)
    with pg_factory() as db:
        workday = create_workday(
            db,
            region_id=region_id,
            category="RACE_DAY",
            work_date=date.today(),
            track_id=None,
            title="Concurrent publish",
            actor_user_id=user_id,
        )
        workday_id, draft_id = workday.id, workday.current_draft_revision_id
    barrier = threading.Barrier(2)
    results: list[str] = []

    def attempt() -> None:
        with pg_factory() as db:
            barrier.wait()
            try:
                publish(db, workday_id, draft_id, user_id)
            except PublishConflict:
                results.append("stale")
            else:
                results.append("published")

    threads = [threading.Thread(target=attempt) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=15)
    assert sorted(results) == ["published", "stale"]
    with pg_factory() as db:
        workday = db.get(Workday, workday_id)
        revision = db.get(WorkdayRevision, draft_id)
        assert workday.current_published_revision_id == draft_id
        assert workday.current_draft_revision_id is None
        assert revision.state == "PUBLISHED"
        assert db.scalar(
            select(func.count()).select_from(NotificationEvent).where(
                NotificationEvent.event_key == f"roster-published:{workday_id}:{draft_id}"
            )
        ) == 1


def test_publish_failure_rolls_back_revision_pointer_and_outbox(pg_factory, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    user_id, region_id = _authority(pg_factory)
    with pg_factory() as db:
        workday = create_workday(
            db,
            region_id=region_id,
            category="RACE_DAY",
            work_date=date.today(),
            track_id=None,
            title="Atomic failure",
            actor_user_id=user_id,
        )
        workday_id, draft_id = workday.id, workday.current_draft_revision_id
    from app.rostering import service as roster_service

    monkeypatch.setattr(
        roster_service,
        "record_event",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("forced outbox failure")),
    )
    with pg_factory() as db, pytest.raises(RuntimeError, match="forced outbox failure"):
        publish(db, workday_id, draft_id, user_id)
    with pg_factory() as db:
        workday = db.get(Workday, workday_id)
        revision = db.get(WorkdayRevision, draft_id)
        assert workday.current_published_revision_id is None
        assert workday.current_draft_revision_id == draft_id
        assert revision.state == "DRAFT"
        assert db.get(NotificationEvent, f"roster-published:{workday_id}:{draft_id}") is None


def test_concurrent_pending_grant_activation_is_single_and_consistent(pg_factory) -> None:  # type: ignore[no-untyped-def]
    user_id, region_id = _authority(pg_factory)
    with pg_factory() as db:
        user = db.get(User, user_id)
        pending = RoleGrant(
            user_id=user.id,
            role="SUB_MANAGER",
            region_id=region_id,
            status="PENDING",
        )
        db.add(pending)
        db.commit()
        grant_id, starting_epoch = pending.id, user.auth_epoch
    barrier = threading.Barrier(2)
    results: list[int] = []

    def activate() -> None:
        with pg_factory() as db:
            user = db.get(User, user_id)
            barrier.wait()
            results.append(activate_pending_grants(db, user, "123456"))

    threads = [threading.Thread(target=activate) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=15)
    assert sorted(results) == [0, 1]
    with pg_factory() as db:
        assert db.get(RoleGrant, grant_id).status == "ACTIVE"
        assert db.get(User, user_id).auth_epoch == starting_epoch + 1


def test_postgres_identity_grant_and_application_constraints(pg_factory) -> None:  # type: ignore[no-untyped-def]
    user_id, region_id = _authority(pg_factory)
    suffix = uuid.uuid4().hex[:10]
    with pg_factory() as db:
        linked_user = User(
            email=f"linked-{suffix}@example.com",
            display_name="Linked account",
            credential_hash=hash_credential("123456"),
            credential_kind="pin",
        )
        person = Person(display_name="Constraint person", home_region_id=region_id)
        position = BasePosition(name=f"Constraint Position {suffix}")
        db.add_all([linked_user, person, position])
        db.flush()
        db.add(UserPersonLink(user_id=linked_user.id, person_id=person.id))
        workday = create_workday(
            db,
            region_id=region_id,
            category="RACE_DAY",
            work_date=date.today(),
            track_id=None,
            title="Application constraint",
            actor_user_id=user_id,
        )
        draft = db.get(WorkdayRevision, workday.current_draft_revision_id)
        slot = add_assignment(
            db,
            draft,
            AssignmentInput(
                base_position_id=position.id,
                slot_index=2,
                person_id=None,
                status="OPEN",
            ),
        )
        publish(db, workday.id, draft.id, user_id)
        application = OpenPositionApplication(
            revision_id=draft.id,
            slot_key=slot.slot_key,
            person_id=person.id,
        )
        db.add(application)
        db.commit()

        with pytest.raises(IntegrityError), db.begin_nested():
            db.add(UserPersonLink(user_id=user_id, person_id=person.id))
            db.flush()
        with pytest.raises(IntegrityError), db.begin_nested():
            db.add(RoleGrant(user_id=user_id, role="MANAGER", region_id=region_id))
            db.flush()
        with pytest.raises(IntegrityError), db.begin_nested():
            db.add(
                OpenPositionApplication(
                    revision_id=draft.id,
                    slot_key=slot.slot_key,
                    person_id=person.id,
                )
            )
            db.flush()
        assert db.scalar(
            select(func.count()).select_from(OpenPositionApplication).where(
                OpenPositionApplication.revision_id == draft.id,
                OpenPositionApplication.slot_key == slot.slot_key,
                OpenPositionApplication.person_id == person.id,
            )
        ) == 1


def test_decline_detaches_stale_manager_draft_without_losing_it(pg_factory) -> None:  # type: ignore[no-untyped-def]
    user_id, region_id = _authority(pg_factory)
    suffix = uuid.uuid4().hex[:10]
    with pg_factory() as db:
        person = Person(display_name="Declining person", home_region_id=region_id)
        position = BasePosition(name=f"Decline Position {suffix}")
        db.add_all([person, position])
        db.commit()
        workday = create_workday(
            db,
            region_id=region_id,
            category="RACE_DAY",
            work_date=date.today(),
            track_id=None,
            title="Decline race",
            actor_user_id=user_id,
        )
        first = db.get(WorkdayRevision, workday.current_draft_revision_id)
        assignment = add_assignment(
            db,
            first,
            AssignmentInput(
                base_position_id=position.id,
                slot_index=1,
                person_id=person.id,
                status="ASSIGNED",
            ),
        )
        publish(db, workday.id, first.id, user_id)
        stale_draft = ensure_draft(db, workday, user_id)
        decline_published_assignment(
            db,
            workday_id=workday.id,
            slot_key=assignment.slot_key,
            person_id=person.id,
            actor_user_id=user_id,
        )
        with pytest.raises(PublishConflict):
            publish(db, workday.id, stale_draft.id, user_id)
        db.expire_all()
        assert db.get(WorkdayRevision, stale_draft.id).state == "DRAFT"
        assert db.get(Workday, workday.id).current_published_revision_id != first.id
