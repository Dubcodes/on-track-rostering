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
from app.notifications.models import NotificationDelivery, NotificationEvent, PushSubscription
from app.notifications.service import encrypt_subscription, process_pending
from app.rostering.models import OpenPositionApplication, Workday, WorkdayRevision
from app.rostering.service import (
    AssignmentInput,
    DraftConflict,
    PublishConflict,
    add_assignment,
    create_workday,
    decline_published_assignment,
    ensure_draft,
    publish,
    remove_assignment,
    update_assignment,
    update_draft_details,
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
        workday_id, draft_id, expected_version = (
            workday.id,
            workday.current_draft_revision_id,
            workday.lock_version,
        )
    barrier = threading.Barrier(2)
    results: list[str] = []

    def attempt() -> None:
        with pg_factory() as db:
            barrier.wait()
            try:
                publish(db, workday_id, draft_id, user_id, expected_version)
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


def test_simultaneous_first_edit_resolves_to_one_shared_draft(pg_factory) -> None:  # type: ignore[no-untyped-def]
    user_id, region_id = _authority(pg_factory)
    with pg_factory() as db:
        workday = create_workday(
            db,
            region_id=region_id,
            category="RACE_DAY",
            work_date=date.today(),
            track_id=None,
            title="Shared draft creation",
            actor_user_id=user_id,
        )
        workday_id = workday.id
        first_id = workday.current_draft_revision_id
        expected_version = workday.lock_version
        db.commit()
        publish(db, workday_id, first_id, user_id, expected_version)
    barrier = threading.Barrier(2)
    draft_ids: list[uuid.UUID] = []
    errors: list[Exception] = []

    def open_editor() -> None:
        try:
            with pg_factory() as db:
                workday = db.get(Workday, workday_id)
                barrier.wait()
                draft_ids.append(ensure_draft(db, workday, user_id).id)
        except Exception as exc:  # pragma: no cover - assertion reports unexpected thread failures
            errors.append(exc)

    threads = [threading.Thread(target=open_editor) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=15)
    assert errors == []
    assert len(draft_ids) == 2 and len(set(draft_ids)) == 1
    with pg_factory() as db:
        workday = db.get(Workday, workday_id)
        current = db.get(WorkdayRevision, workday.current_draft_revision_id)
        assert current.id == draft_ids[0]
        assert current.revision_number == 2
        assert db.scalar(
            select(func.count()).select_from(WorkdayRevision).where(
                WorkdayRevision.workday_id == workday_id,
                WorkdayRevision.state == "DRAFT",
            )
        ) == 1


def test_postgres_stale_detail_edit_preserves_winner(pg_factory) -> None:  # type: ignore[no-untyped-def]
    user_id, region_id = _authority(pg_factory)
    with pg_factory() as db:
        workday = create_workday(
            db,
            region_id=region_id,
            category="RACE_DAY",
            work_date=date.today(),
            track_id=None,
            title="Before",
            actor_user_id=user_id,
        )
        draft = db.get(WorkdayRevision, workday.current_draft_revision_id)
        stale_version = workday.lock_version
        common = {
            "workday_id": workday.id,
            "draft_id": draft.id,
            "work_date": date.today(),
            "track_id": None,
            "start_time": None,
            "end_time": None,
            "on_track_time": None,
            "first_trial_time": None,
            "first_race_time": None,
            "last_race_time": None,
            "race_count": None,
            "change_reason": "",
        }
        update_draft_details(
            db, expected_version=stale_version, title="Manager A", day_note="Winner", **common
        )
        with pytest.raises(DraftConflict):
            update_draft_details(
                db,
                expected_version=stale_version,
                title="Manager B stale",
                day_note="Loser",
                **common,
            )
        db.rollback()
        db.refresh(draft)
        assert (draft.title, draft.day_note) == ("Manager A", "Winner")


def test_postgres_stale_assignment_mutation_is_rejected(pg_factory) -> None:  # type: ignore[no-untyped-def]
    user_id, region_id = _authority(pg_factory)
    suffix = uuid.uuid4().hex[:10]
    with pg_factory() as db:
        person = Person(display_name=f"PG Person {suffix}", home_region_id=region_id)
        position = BasePosition(name=f"PG Position {suffix}")
        db.add_all([person, position])
        db.commit()
        workday = create_workday(
            db,
            region_id=region_id,
            category="RACE_DAY",
            work_date=date.today(),
            track_id=None,
            title="Assignment race",
            actor_user_id=user_id,
        )
        draft = db.get(WorkdayRevision, workday.current_draft_revision_id)
        assignment = add_assignment(
            db,
            workday_id=workday.id,
            draft_id=draft.id,
            expected_version=workday.lock_version,
            item=AssignmentInput(position.id, 1, person.id, "ASSIGNED"),
        )
        stale_version = workday.lock_version
        update_assignment(
            db,
            workday_id=workday.id,
            draft_id=draft.id,
            expected_version=stale_version,
            assignment_id=assignment.id,
            person_id=person.id,
            status="ASSIGNED",
            note="Manager A",
            note_private=True,
        )
        with pytest.raises(DraftConflict):
            remove_assignment(
                db,
                workday_id=workday.id,
                draft_id=draft.id,
                expected_version=stale_version,
                assignment_id=assignment.id,
            )
        db.rollback()
        db.refresh(assignment)
        assert assignment.note == "Manager A"


def test_postgres_publish_invalidates_open_editor_version(pg_factory) -> None:  # type: ignore[no-untyped-def]
    user_id, region_id = _authority(pg_factory)
    with pg_factory() as db:
        workday = create_workday(
            db,
            region_id=region_id,
            category="RACE_DAY",
            work_date=date.today(),
            track_id=None,
            title="Publish invalidation",
            actor_user_id=user_id,
        )
        workday_id = workday.id
        draft_id = workday.current_draft_revision_id
        open_editor_version = workday.lock_version
        db.commit()
        publish(db, workday_id, draft_id, user_id, open_editor_version)
        with pytest.raises(DraftConflict):
            update_draft_details(
                db,
                workday_id=workday_id,
                draft_id=draft_id,
                expected_version=open_editor_version,
                work_date=date.today(),
                track_id=None,
                title="Stale edit",
                start_time=None,
                end_time=None,
                on_track_time=None,
                first_trial_time=None,
                first_race_time=None,
                last_race_time=None,
                race_count=None,
                day_note="",
                change_reason="",
            )
        db.rollback()
        draft = db.get(WorkdayRevision, draft_id)
        db.refresh(draft)
        assert draft.state == "PUBLISHED" and draft.title == "Publish invalidation"


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
        workday_id, draft_id, expected_version = (
            workday.id,
            workday.current_draft_revision_id,
            workday.lock_version,
        )
    from app.rostering import service as roster_service

    monkeypatch.setattr(
        roster_service,
        "record_event",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("forced outbox failure")),
    )
    with pg_factory() as db, pytest.raises(RuntimeError, match="forced outbox failure"):
        publish(db, workday_id, draft_id, user_id, expected_version)
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
            workday_id=workday.id,
            draft_id=draft.id,
            expected_version=workday.lock_version,
            item=AssignmentInput(
                base_position_id=position.id,
                slot_index=2,
                person_id=None,
                status="OPEN",
            ),
        )
        publish(db, workday.id, draft.id, user_id, workday.lock_version)
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
            workday_id=workday.id,
            draft_id=first.id,
            expected_version=workday.lock_version,
            item=AssignmentInput(
                base_position_id=position.id,
                slot_index=1,
                person_id=person.id,
                status="ASSIGNED",
            ),
        )
        publish(db, workday.id, first.id, user_id, workday.lock_version)
        stale_draft = ensure_draft(db, workday, user_id)
        decline_published_assignment(
            db,
            workday_id=workday.id,
            slot_key=assignment.slot_key,
            person_id=person.id,
            actor_user_id=user_id,
        )
        with pytest.raises(PublishConflict):
            publish(db, workday.id, stale_draft.id, user_id, workday.lock_version)
        db.expire_all()
        assert db.get(WorkdayRevision, stale_draft.id).state == "DRAFT"
        assert db.get(Workday, workday.id).current_published_revision_id != first.id


def test_notification_claim_prevents_concurrent_delivery_and_expired_lease_recovers(pg_factory) -> None:  # type: ignore[no-untyped-def]
    from datetime import timedelta

    from app.core.time import utcnow

    user_id, region_id = _authority(pg_factory)
    suffix = uuid.uuid4().hex
    with pg_factory() as db:
        subscription = PushSubscription(
            user_id=user_id,
            endpoint_hash=suffix,
            encrypted_subscription=encrypt_subscription(
                {"endpoint": f"https://push.example/{suffix}", "keys": {"p256dh": "x", "auth": "y"}}
            ),
        )
        event = NotificationEvent(
            event_key=f"claim:{suffix}",
            event_type="ONE_HOUR_BEFORE",
            region_id=region_id,
            audience_user_id=user_id,
        )
        expired = NotificationEvent(
            event_key=f"expired:{suffix}",
            event_type="ONE_HOUR_BEFORE",
            region_id=region_id,
            audience_user_id=user_id,
            status="PROCESSING",
            claim_token=str(uuid.uuid4()),
            claimed_at=utcnow() - timedelta(minutes=6),
        )
        db.add_all([subscription, event, expired])
        db.commit()

    barrier = threading.Barrier(2)
    sent: list[str] = []
    lock = threading.Lock()

    def sender(_subscription: dict[str, object], payload: str) -> None:
        with lock:
            sent.append(payload)

    def worker() -> None:
        with pg_factory() as db:
            barrier.wait()
            process_pending(db, sender=sender)

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=15)
    assert len(sent) == 2  # one delivery for each distinct event, never duplicated
    with pg_factory() as db:
        assert db.get(NotificationEvent, event.event_key).status == "PROCESSED"
        assert db.get(NotificationEvent, expired.event_key).status == "PROCESSED"
        assert db.scalar(
            select(func.count()).select_from(NotificationDelivery).where(
                NotificationDelivery.event_key.in_([event.event_key, expired.event_key])
            )
        ) == 2
