from __future__ import annotations

import json
from datetime import UTC, date, datetime, time, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from pywebpush import WebPushException
from sqlalchemy import select

from app.auth.security import hash_credential
from app.branding.models import SystemBranding
from app.catalog.models import BasePosition, Region
from app.identity.models import Person, User, UserPersonLink
from app.notifications.models import NotificationDelivery, NotificationEvent, PushSubscription
from app.notifications.service import audience_user_ids, generate_reminders, process_event, save_subscription
from app.rostering.models import Assignment, Workday, WorkdayRevision


def _user(db, status: str = "ACTIVE") -> User:  # type: ignore[no-untyped-def]
    user = User(
        email=f"{status.lower()}@example.test",
        display_name=status.title(),
        credential_hash=hash_credential("123456"),
        credential_kind="pin",
        status=status,
    )
    db.add(user)
    db.commit()
    return user


def _subscription(db, user: User) -> PushSubscription:  # type: ignore[no-untyped-def]
    return save_subscription(
        db,
        user_id=user.id,
        value={
            "endpoint": f"https://push.example.test/{user.id}",
            "keys": {"p256dh": "public-material", "auth": "auth-material"},
        },
        label="Test browser",
    )


def test_delivery_is_encrypted_and_idempotent(db) -> None:  # type: ignore[no-untyped-def]
    user = _user(db)
    subscription = _subscription(db, user)
    assert "push.example.test" not in json.dumps(subscription.encrypted_subscription)
    event = NotificationEvent(
        event_key="direct:1",
        event_type="ROSTER_PUBLISHED",
        audience_user_id=user.id,
        payload={},
    )
    db.add(event)
    db.commit()
    sent: list[str] = []

    process_event(db, event, sender=lambda _subscription, payload: sent.append(payload))
    process_event(db, event, sender=lambda _subscription, payload: sent.append(payload))
    delivery = db.scalar(
        select(NotificationDelivery).where(NotificationDelivery.event_key == event.event_key)
    )
    assert len(sent) == 1
    assert delivery.status == "DELIVERED" and delivery.attempt_count == 1
    assert event.status == "PROCESSED"


def test_delivery_uses_configured_product_name(db) -> None:  # type: ignore[no-untyped-def]
    user = _user(db)
    _subscription(db, user)
    db.add(SystemBranding(id=1, product_name="Track Crew"))
    event = NotificationEvent(
        event_key="branding:1",
        event_type="UNKNOWN_EVENT",
        audience_user_id=user.id,
        payload={},
    )
    db.add(event)
    db.commit()
    sent: list[str] = []

    def sender(_subscription, payload):  # type: ignore[no-untyped-def]
        assert not db.in_transaction()
        sent.append(payload)

    process_event(db, event, sender=sender)

    assert json.loads(sent[0]) == {
        "title": "Track Crew update",
        "body": "Open Track Crew to view the authoritative roster details.",
        "url": "/month",
        "event_key": "branding:1",
    }


def test_permanent_endpoint_failure_deactivates_subscription(db) -> None:  # type: ignore[no-untyped-def]
    user = _user(db)
    subscription = _subscription(db, user)
    event = NotificationEvent(
        event_key="direct:gone",
        event_type="ROSTER_PUBLISHED",
        audience_user_id=user.id,
        payload={},
    )
    db.add(event)
    db.commit()

    def gone(_subscription, _payload):  # type: ignore[no-untyped-def]
        raise WebPushException("gone", response=SimpleNamespace(status_code=410))

    process_event(db, event, sender=gone)
    db.refresh(subscription)
    delivery = db.scalar(
        select(NotificationDelivery).where(NotificationDelivery.event_key == event.event_key)
    )
    assert subscription.active is False
    assert delivery.status == "PERMANENT_FAILURE"
    assert event.status == "PROCESSED"


def test_transient_failure_stays_retryable_and_disabled_user_is_skipped(db) -> None:  # type: ignore[no-untyped-def]
    user = _user(db)
    _subscription(db, user)
    event = NotificationEvent(
        event_key="direct:retry",
        event_type="ROSTER_PUBLISHED",
        audience_user_id=user.id,
        payload={},
    )
    db.add(event)
    db.commit()
    process_event(db, event, sender=lambda *_: (_ for _ in ()).throw(RuntimeError("offline")))
    delivery = db.scalar(
        select(NotificationDelivery).where(NotificationDelivery.event_key == event.event_key)
    )
    assert delivery.status == "RETRY" and event.status == "RETRY"

    disabled = _user(db, "DISABLED")
    _subscription(db, disabled)
    disabled_event = NotificationEvent(
        event_key="direct:disabled",
        event_type="ROSTER_PUBLISHED",
        audience_user_id=disabled.id,
        payload={},
    )
    db.add(disabled_event)
    db.commit()
    process_event(db, disabled_event, sender=lambda *_: (_ for _ in ()).throw(AssertionError()))
    assert disabled_event.status == "PROCESSED"


def test_roster_change_audience_is_previous_and_new_assignment_union(db) -> None:  # type: ignore[no-untyped-def]
    region = Region(name="Audience")
    creator = _user(db)
    old_user = User(email="old@example.test", display_name="Old", credential_hash=hash_credential("123456"))
    new_user = User(email="new@example.test", display_name="New", credential_hash=hash_credential("123456"))
    unrelated = User(email="other@example.test", display_name="Other", credential_hash=hash_credential("123456"))
    old_person, new_person, other_person = Person(display_name="Old"), Person(display_name="New"), Person(display_name="Other")
    db.add_all([region, old_user, new_user, unrelated, old_person, new_person, other_person])
    db.flush()
    workday = Workday(region_id=region.id, created_by_user_id=creator.id)
    db.add(workday)
    db.flush()
    previous = WorkdayRevision(workday_id=workday.id, revision_number=1, state="PUBLISHED", work_date=date(2026, 10, 1), created_by_user_id=creator.id)
    current = WorkdayRevision(workday_id=workday.id, revision_number=2, state="PUBLISHED", work_date=date(2026, 10, 1), created_by_user_id=creator.id)
    db.add_all([previous, current])
    db.flush()
    db.add_all(
        [
            UserPersonLink(user_id=old_user.id, person_id=old_person.id),
            UserPersonLink(user_id=new_user.id, person_id=new_person.id),
            UserPersonLink(user_id=unrelated.id, person_id=other_person.id),
            Assignment(revision_id=previous.id, display_name_snapshot="Camera", person_id=old_person.id, person_name_snapshot="Old", status="ASSIGNED"),
            Assignment(revision_id=current.id, display_name_snapshot="Camera", person_id=new_person.id, person_name_snapshot="New", status="ASSIGNED"),
        ]
    )
    event = NotificationEvent(
        event_key="roster:union",
        event_type="ROSTER_PUBLISHED",
        region_id=region.id,
        workday_id=workday.id,
        payload={"revision_id": str(current.id), "previous_revision_id": str(previous.id)},
    )
    db.add(event)
    db.commit()
    assert audience_user_ids(db, event) == {old_user.id, new_user.id}


def test_reminders_use_person_day_start_and_are_idempotent(db) -> None:  # type: ignore[no-untyped-def]
    region = Region(name="Reminder")
    user = User(email="reminder@example.test", display_name="Reminder", credential_hash=hash_credential("123456"))
    person = Person(display_name="Crew")
    position = BasePosition(name="Camera")
    db.add_all([region, user, person, position])
    db.flush()
    db.add(UserPersonLink(user_id=user.id, person_id=person.id))
    workday = Workday(region_id=region.id, created_by_user_id=user.id)
    db.add(workday)
    db.flush()
    revision = WorkdayRevision(
        workday_id=workday.id,
        revision_number=1,
        state="PUBLISHED",
        work_date=date(2026, 10, 1),
        start_time=time(8),
        end_time=time(20),
        created_by_user_id=user.id,
    )
    db.add(revision)
    db.flush()
    db.add_all(
        [
            Assignment(revision_id=revision.id, display_name_snapshot="Camera 2", person_id=person.id, status="ASSIGNED", start_time=time(7, 30), end_time=time(12)),
            Assignment(revision_id=revision.id, display_name_snapshot="RF Camera", person_id=person.id, status="ASSIGNED", start_time=time(12), end_time=time(19, 30)),
        ]
    )
    workday.current_published_revision_id = revision.id
    db.commit()

    now = datetime(2026, 9, 20, tzinfo=UTC)
    assert generate_reminders(db, now=now) == 3
    assert generate_reminders(db, now=now) == 0
    events = list(db.scalars(select(NotificationEvent).order_by(NotificationEvent.event_type)))
    assert {event.event_type for event in events} == {"ONE_HOUR_BEFORE", "NIGHT_BEFORE", "TWO_DAYS_BEFORE"}
    one_hour = next(event for event in events if event.event_type == "ONE_HOUR_BEFORE")
    available_at = one_hour.available_at
    if available_at.tzinfo is None:
        available_at = available_at.replace(tzinfo=UTC)
    assert available_at.astimezone(ZoneInfo("Pacific/Auckland")).time() == time(6, 30)

    overnight = WorkdayRevision(
        workday_id=workday.id,
        revision_number=2,
        state="PUBLISHED",
        work_date=date(2026, 10, 1),
        start_time=time(23),
        end_time=time(5),
        created_by_user_id=user.id,
    )
    db.add(overnight)
    db.flush()
    db.add(
        Assignment(
            revision_id=overnight.id,
            display_name_snapshot="After midnight",
            person_id=person.id,
            status="ASSIGNED",
            start_time=time(1),
            end_time=time(4),
        )
    )
    workday.current_published_revision_id = overnight.id
    db.commit()
    assert generate_reminders(db, now=now) == 3
    overnight_one_hour = db.get(
        NotificationEvent,
        f"reminder:ONE_HOUR_BEFORE:{overnight.id}:{person.id}",
    )
    available_at = overnight_one_hour.available_at
    if available_at.tzinfo is None:
        available_at = available_at.replace(tzinfo=UTC)
    local_available = available_at.astimezone(ZoneInfo("Pacific/Auckland"))
    assert (local_available.date(), local_available.time()) == (date(2026, 10, 2), time(0))


def _reminder_roster(db, suffix: str, work_date: date):  # type: ignore[no-untyped-def]
    region = Region(name=f"Reminder {suffix}")
    user = User(
        email=f"reminder-{suffix}@example.test",
        display_name="Reminder",
        credential_hash=hash_credential("123456"),
    )
    person = Person(display_name=f"Crew {suffix}")
    position = BasePosition(name=f"Camera {suffix}")
    db.add_all([region, user, person, position])
    db.flush()
    db.add(UserPersonLink(user_id=user.id, person_id=person.id))
    workday = Workday(region_id=region.id, created_by_user_id=user.id)
    db.add(workday)
    db.flush()
    revision = WorkdayRevision(
        workday_id=workday.id,
        revision_number=1,
        state="PUBLISHED",
        work_date=work_date,
        start_time=time(8),
        end_time=time(17),
        created_by_user_id=user.id,
    )
    db.add(revision)
    db.flush()
    assignment = Assignment(
        revision_id=revision.id,
        base_position_id=position.id,
        display_name_snapshot="Camera",
        person_id=person.id,
        status="ASSIGNED",
    )
    db.add(assignment)
    workday.current_published_revision_id = revision.id
    db.commit()
    _subscription(db, user)
    return user, person, workday, revision, assignment


def test_superseded_removed_and_late_reminders_are_terminal_without_delivery(db) -> None:  # type: ignore[no-untyped-def]
    user, _person, workday, revision, _assignment = _reminder_roster(
        db, "stale", date(2026, 10, 1)
    )
    generated_at = datetime(2026, 9, 20, tzinfo=UTC)
    assert generate_reminders(db, now=generated_at) == 3
    night = db.get(
        NotificationEvent,
        f"reminder:NIGHT_BEFORE:{revision.id}:{_person.id}",
    )
    replacement = WorkdayRevision(
        workday_id=workday.id,
        revision_number=2,
        state="PUBLISHED",
        work_date=revision.work_date,
        created_by_user_id=user.id,
    )
    db.add(replacement)
    db.flush()
    workday.current_published_revision_id = replacement.id
    db.commit()
    sent: list[str] = []
    process_event(db, night, sender=lambda *_: sent.append("sent"), now=night.available_at)
    assert night.status == "PROCESSED" and sent == []
    assert db.scalar(
        select(NotificationDelivery).where(NotificationDelivery.event_key == night.event_key)
    ) is None

    _user2, person2, _workday2, revision2, assignment2 = _reminder_roster(
        db, "removed", date(2026, 11, 1)
    )
    assert generate_reminders(db, now=generated_at, horizon_days=60) == 3
    removed = db.get(
        NotificationEvent,
        f"reminder:TWO_DAYS_BEFORE:{revision2.id}:{person2.id}",
    )
    assignment2.person_id = None
    db.commit()
    process_event(db, removed, sender=lambda *_: sent.append("sent"), now=removed.available_at)
    assert removed.status == "PROCESSED" and sent == []

    _user3, person3, _workday3, revision3, _assignment3 = _reminder_roster(
        db, "late", date(2026, 12, 1)
    )
    assert generate_reminders(db, now=generated_at, horizon_days=90) == 3
    late = db.get(
        NotificationEvent,
        f"reminder:ONE_HOUR_BEFORE:{revision3.id}:{person3.id}",
    )
    process_event(
        db,
        late,
        sender=lambda *_: sent.append("sent"),
        now=late.available_at + timedelta(minutes=16),
    )
    assert late.status == "PROCESSED" and sent == []

    _reminder_roster(db, "ancient", date(2026, 10, 1))
    after_start = datetime(2026, 9, 30, 23, tzinfo=UTC)  # noon NZ on the Workday
    assert generate_reminders(db, now=after_start) == 0
