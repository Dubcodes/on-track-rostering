from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, time

import pytest
from sqlalchemy import event as sqlalchemy_event
from sqlalchemy import select
from starlette.requests import Request

from app.audit.models import HumanChange
from app.auth.network import resolve_request, same_origin
from app.auth.policy import (
    Actor,
    can_administer_region,
    can_manage_region,
    can_self_decline_assignment,
    can_view_management_detail,
)
from app.auth.security import create_device, hash_credential, resolve_device
from app.auth.service import activate_pending_grants, approve_signup
from app.catalog.models import BasePosition, Region
from app.core.config import Settings, get_settings
from app.core.enums import Role
from app.core.holidays import holiday_for_date
from app.core.time import local_today
from app.identity.models import Person, RoleGrant, SignupRequest, User, UserPersonLink
from app.positions.service import bulk_eligibility, eligibility, set_preference_signal
from app.rostering.diff import publication_diff
from app.rostering.models import Assignment, PositionCapability, Workday, WorkdayRevision
from app.rostering.participation import person_day_participation
from app.rostering.service import publish


def _user(db, email: str, secret: str = "123456") -> User:  # type: ignore[no-untyped-def]
    user = User(
        email=email,
        display_name=email.split("@", 1)[0],
        credential_hash=hash_credential(secret),
        credential_kind="pin" if secret.isdigit() else "password",
        credential_admin_eligible=len(secret) >= (8 if secret.isdigit() else 12),
    )
    db.add(user)
    db.flush()
    return user


def _request(
    *, peer: str, scheme: str = "http", host: str = "internal.test", headers: list[tuple[bytes, bytes]] | None = None
) -> Request:
    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "POST",
            "scheme": scheme,
            "path": "/login",
            "raw_path": b"/login",
            "query_string": b"",
            "headers": headers or [(b"host", host.encode())],
            "client": (peer, 43210),
            "server": (host, 80 if scheme == "http" else 443),
        }
    )


def test_trusted_proxy_resolution_and_same_origin(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from app.auth import network

    settings = get_settings().model_copy(update={"trusted_proxy_cidrs": ("10.0.0.0/24",)})
    monkeypatch.setattr(network, "get_settings", lambda: settings)
    forwarded = [
        (b"host", b"internal.test"),
        (b"x-forwarded-for", b"203.0.113.7"),
        (b"x-forwarded-proto", b"https"),
        (b"x-forwarded-host", b"roster.example.nz"),
        (b"x-forwarded-port", b"443"),
        (b"origin", b"https://roster.example.nz"),
    ]
    trusted = _request(peer="10.0.0.8", headers=forwarded)
    resolved = resolve_request(trusted)
    assert resolved.forwarded and resolved.client_address == "203.0.113.7"
    assert resolved.origin == ("https", "roster.example.nz", 443)
    assert same_origin(trusted)

    spoofed = _request(peer="198.51.100.9", headers=forwarded)
    assert not resolve_request(spoofed).forwarded
    assert resolve_request(spoofed).client_address == "198.51.100.9"
    assert not same_origin(spoofed)

    malformed = _request(
        peer="10.0.0.8",
        headers=[(b"host", b"internal.test"), *forwarded[1:4], (b"x-forwarded-port", b"443,80")],
    )
    assert not resolve_request(malformed).forwarded
    duplicate_port = _request(
        peer="10.0.0.8",
        headers=[
            (b"host", b"internal.test"),
            *forwarded[1:4],
            (b"x-forwarded-port", b"443"),
            (b"x-forwarded-port", b"80"),
        ],
    )
    assert not resolve_request(duplicate_port).forwarded


def test_trusted_proxy_uses_public_host_when_forwarded_host_is_absent(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from app.auth import network

    settings = get_settings().model_copy(update={"trusted_proxy_cidrs": ("192.168.64.0/20",)})
    monkeypatch.setattr(network, "get_settings", lambda: settings)
    headers = [
        (b"host", b"ontrackrostering.dubcodesmedia.com"),
        (b"x-forwarded-for", b"203.0.113.7"),
        (b"x-forwarded-proto", b"https"),
        (b"origin", b"https://ontrackrostering.dubcodesmedia.com"),
    ]
    request = _request(peer="192.168.64.5", headers=headers)
    assert resolve_request(request).origin == ("https", "ontrackrostering.dubcodesmedia.com", 443)
    assert resolve_request(request).forwarded
    assert same_origin(request)


@pytest.mark.parametrize(
    ("changed_headers", "origin"),
    [
        ([(b"x-forwarded-proto", b"https,http")], b"https://ontrackrostering.dubcodesmedia.com"),
        ([(b"x-forwarded-proto", b"https"), (b"x-forwarded-proto", b"http")], b"https://ontrackrostering.dubcodesmedia.com"),
        ([(b"x-forwarded-host", b"good.example,bad.example")], b"https://good.example"),
        ([(b"x-forwarded-host", b"good.example"), (b"x-forwarded-host", b"bad.example")], b"https://good.example"),
        ([(b"x-forwarded-host", b"public.example:444"), (b"x-forwarded-port", b"443")], b"https://public.example:444"),
    ],
)
def test_trusted_proxy_rejects_ambiguous_or_conflicting_forwarding(
    monkeypatch, changed_headers: list[tuple[bytes, bytes]], origin: bytes
) -> None:  # type: ignore[no-untyped-def]
    from app.auth import network

    settings = get_settings().model_copy(update={"trusted_proxy_cidrs": ("10.0.0.0/24",)})
    monkeypatch.setattr(network, "get_settings", lambda: settings)
    request = _request(
        peer="10.0.0.8",
        headers=[
            (b"host", b"ontrackrostering.dubcodesmedia.com"),
            (b"x-forwarded-for", b"203.0.113.7"),
            *changed_headers,
            (b"origin", origin),
        ],
    )
    assert not resolve_request(request).forwarded
    assert not same_origin(request)


def test_proxy_origin_rejects_mismatches_and_host_ambiguity(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from app.auth import network

    settings = get_settings().model_copy(update={"trusted_proxy_cidrs": ("10.0.0.0/24",)})
    monkeypatch.setattr(network, "get_settings", lambda: settings)
    forwarded = [
        (b"host", b"public.example:8443"),
        (b"x-forwarded-for", b"203.0.113.7"),
        (b"x-forwarded-proto", b"https"),
    ]
    assert same_origin(_request(peer="10.0.0.8", headers=[*forwarded, (b"origin", b"https://public.example:8443")]))
    assert not same_origin(_request(peer="10.0.0.8", headers=[*forwarded, (b"origin", b"https://other.example:8443")]))
    assert not same_origin(_request(peer="10.0.0.8", headers=[*forwarded, (b"origin", b"http://public.example:8443")]))

    duplicate_host = _request(
        peer="10.0.0.8",
        headers=[
            (b"host", b"public.example"),
            (b"host", b"attacker.example"),
            (b"x-forwarded-for", b"203.0.113.7"),
            (b"x-forwarded-proto", b"https"),
            (b"origin", b"https://public.example"),
        ],
    )
    assert not resolve_request(duplicate_host).forwarded
    assert not same_origin(duplicate_host)

    comma_host = _request(
        peer="10.0.0.8",
        headers=[
            (b"host", b"public.example,attacker.example"),
            (b"x-forwarded-for", b"203.0.113.7"),
            (b"x-forwarded-proto", b"https"),
            (b"origin", b"https://public.example"),
        ],
    )
    assert not resolve_request(comma_host).forwarded
    assert not same_origin(comma_host)


def test_untrusted_peer_ignores_forged_forwarding(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from app.auth import network

    settings = get_settings().model_copy(update={"trusted_proxy_cidrs": ("10.0.0.0/24",)})
    monkeypatch.setattr(network, "get_settings", lambda: settings)
    request = _request(
        peer="198.51.100.9",
        headers=[
            (b"host", b"direct.example:8000"),
            (b"x-forwarded-for", b"203.0.113.7"),
            (b"x-forwarded-proto", b"https"),
            (b"x-forwarded-host", b"forged.example"),
            (b"origin", b"http://direct.example:8000"),
        ],
    )
    resolved = resolve_request(request)
    assert not resolved.forwarded
    assert resolved.client_address == "198.51.100.9"
    assert resolved.origin == ("http", "direct.example", 8000)
    assert same_origin(request)


@pytest.mark.parametrize(
    ("environment", "expected_hosts", "expected_cidrs"),
    [
        (
            {
                "ONTRACK_ALLOWED_HOSTS": "roster.example",
                "ONTRACK_TRUSTED_PROXY_CIDRS": "192.168.64.0/20",
            },
            ("roster.example",),
            ("192.168.64.0/20",),
        ),
        (
            {
                "ONTRACK_ALLOWED_HOSTS": "roster.example, admin.roster.example",
                "ONTRACK_TRUSTED_PROXY_CIDRS": "127.0.0.1/32, 192.168.64.0/20",
            },
            ("roster.example", "admin.roster.example"),
            ("127.0.0.1/32", "192.168.64.0/20"),
        ),
    ],
)
def test_settings_accept_csv_tuple_environment_values(
    monkeypatch, environment: dict[str, str], expected_hosts: tuple[str, ...], expected_cidrs: tuple[str, ...]
) -> None:  # type: ignore[no-untyped-def]
    for name in ("ONTRACK_ALLOWED_HOSTS", "ONTRACK_TRUSTED_PROXY_CIDRS"):
        monkeypatch.delenv(name, raising=False)
    for name, value in environment.items():
        monkeypatch.setenv(name, value)
    settings = Settings(_env_file=None)
    assert settings.allowed_hosts == expected_hosts
    assert settings.trusted_proxy_cidrs == expected_cidrs


def test_admin_activation_requires_admin_grade_primary_credential_and_invalidates_old_session(db) -> None:  # type: ignore[no-untyped-def]
    weak = _user(db, "weak@example.test")
    db.add(RoleGrant(user_id=weak.id, role=Role.ADMIN.value, status="PENDING"))
    db.commit()
    raw_session, _, old_device = create_device(db, weak)
    assert activate_pending_grants(db, weak, strong_auth=True) == 0
    assert resolve_device(db, raw_session) is not None
    assert activate_pending_grants(db, weak, "12345678") == 1
    db.refresh(old_device)
    assert resolve_device(db, raw_session) is None

    strong_password = _user(db, "strong@example.test", "a sufficiently long password")
    db.add(RoleGrant(user_id=strong_password.id, role=Role.ADMIN.value, status="PENDING"))
    db.commit()
    assert activate_pending_grants(db, strong_password, "a sufficiently long password") == 1


def test_signup_person_scope_archival_missing_and_collision(db) -> None:  # type: ignore[no-untyped-def]
    north, south = Region(name="North"), Region(name="South")
    db.add_all([north, south])
    db.flush()
    manager = _user(db, "manager@example.test")
    linked_user = _user(db, "linked@example.test")
    north_person = Person(display_name="North Person", home_region_id=north.id)
    south_person = Person(display_name="South Person", home_region_id=south.id)
    archived = Person(display_name="Archived", home_region_id=north.id, lifecycle="ARCHIVED")
    db.add_all([north_person, south_person, archived])
    db.flush()
    db.add_all(
        [
            RoleGrant(user_id=manager.id, role=Role.MANAGER.value, region_id=north.id),
            UserPersonLink(user_id=linked_user.id, person_id=north_person.id),
        ]
    )
    db.commit()
    actor = Actor(manager.id, None, frozenset(), {north.id: frozenset({Role.MANAGER.value})})

    def signup(region_id: uuid.UUID = north.id) -> SignupRequest:
        row = SignupRequest(
            email=f"{uuid.uuid4().hex}@example.test",
            display_name="Candidate",
            requested_region_id=region_id,
        )
        db.add(row)
        db.commit()
        return row

    with pytest.raises(PermissionError, match="regional scope"):
        approve_signup(db, signup=signup(), actor=actor, role="EMPLOYEE", region_id=north.id, person_id=south_person.id, create_person=False)
    with pytest.raises(ValueError, match="archived"):
        approve_signup(db, signup=signup(), actor=actor, role="EMPLOYEE", region_id=north.id, person_id=archived.id, create_person=False)
    with pytest.raises(ValueError, match="does not exist"):
        approve_signup(db, signup=signup(), actor=actor, role="EMPLOYEE", region_id=north.id, person_id=uuid.uuid4(), create_person=False)
    with pytest.raises(ValueError, match="already linked"):
        approve_signup(db, signup=signup(), actor=actor, role="EMPLOYEE", region_id=north.id, person_id=north_person.id, create_person=False)
    with pytest.raises(PermissionError, match="signup request"):
        approve_signup(db, signup=signup(south.id), actor=actor, role="EMPLOYEE", region_id=north.id, person_id=None, create_person=True)


def test_role_capabilities_keep_viewer_read_only_and_submanager_out_of_administration() -> None:
    region_id = uuid.uuid4()
    viewer = Actor(uuid.uuid4(), None, frozenset(), {region_id: frozenset({"VIEWER"})})
    submanager = Actor(uuid.uuid4(), None, frozenset(), {region_id: frozenset({"SUB_MANAGER"})})
    assert can_view_management_detail(viewer, region_id)
    assert not can_manage_region(viewer, region_id)
    assert can_manage_region(submanager, region_id)
    assert not can_administer_region(submanager, region_id)


def test_self_decline_policy_is_role_and_effective_start_bound() -> None:
    region_id, person_id, user_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    workday = Workday(region_id=region_id, created_by_user_id=user_id)
    revision = WorkdayRevision(
        workday_id=workday.id,
        revision_number=1,
        work_date=date(2026, 9, 12),
        start_time=time(10),
        created_by_user_id=user_id,
    )
    row = Assignment(
        revision_id=revision.id,
        display_name_snapshot="Camera",
        person_id=person_id,
        status="ASSIGNED",
    )
    nz = get_settings().timezone
    before = datetime(2026, 9, 12, 9, 59, tzinfo=nz)
    at_start = datetime(2026, 9, 12, 10, tzinfo=nz)
    employee = Actor(
        user_id, person_id, frozenset(), {region_id: frozenset({Role.EMPLOYEE.value})}
    )
    contractor = Actor(
        user_id, person_id, frozenset(), {region_id: frozenset({Role.CONTRACTOR.value})}
    )
    viewer = Actor(
        user_id, person_id, frozenset(), {region_id: frozenset({Role.VIEWER.value})}
    )
    manager = Actor(
        user_id, person_id, frozenset(), {region_id: frozenset({Role.MANAGER.value})}
    )
    assert can_self_decline_assignment(employee, workday, revision, [row], now=before)
    assert can_self_decline_assignment(contractor, workday, revision, [row], now=before)
    assert not can_self_decline_assignment(viewer, workday, revision, [row], now=before)
    assert not can_self_decline_assignment(manager, workday, revision, [row], now=before)
    assert not can_self_decline_assignment(employee, workday, revision, [row], now=at_start)
    revision.start_time = None
    assert not can_self_decline_assignment(employee, workday, revision, [row], now=before)
    revision.work_date = date(2026, 9, 11)
    assert not can_self_decline_assignment(employee, workday, revision, [row], now=before)
    revision.work_date = date(2026, 9, 13)
    assert can_self_decline_assignment(employee, workday, revision, [row], now=before)


def test_capability_clearing_preserves_other_family_and_worked_history(db) -> None:  # type: ignore[no-untyped-def]
    person, position, actor = Person(display_name="Crew"), BasePosition(name="Camera"), _user(db, "actor@example.test")
    db.add_all([person, position])
    db.flush()
    db.add_all(
        [
            PositionCapability(person_id=person.id, base_position_id=position.id, signal="WORKED"),
            PositionCapability(person_id=person.id, base_position_id=position.id, signal="EMPLOYEE_OPT_OUT"),
            PositionCapability(person_id=person.id, base_position_id=position.id, signal="MANAGER_BLOCK"),
        ]
    )
    db.commit()
    assert eligibility(db, person.id, position.id) == (False, "Manager restricted")
    set_preference_signal(db, person.id, position.id, "CLEAR", actor.id, family="employee")
    db.commit()
    signals = set(db.scalars(select(PositionCapability.signal)))
    assert signals == {"WORKED", "MANAGER_BLOCK"}
    set_preference_signal(db, person.id, position.id, "CLEAR", actor.id, family="manager")
    db.commit()
    assert set(db.scalars(select(PositionCapability.signal))) == {"WORKED"}
    assert eligibility(db, person.id, position.id) == (True, "Worked before")


def test_bulk_eligibility_matches_single_result_for_all_precedence_signals(db) -> None:  # type: ignore[no-untyped-def]
    position = BasePosition(name="Bulk Camera")
    people = [Person(display_name=f"Bulk {index}") for index in range(6)]
    db.add_all([position, *people])
    db.flush()
    signal_sets = [
        set(),
        {"WORKED"},
        {"MANAGER_ALLOW", "WORKED"},
        {"EMPLOYEE_ALLOW"},
        {"EMPLOYEE_OPT_OUT", "MANAGER_ALLOW"},
        {"MANAGER_BLOCK", "EMPLOYEE_ALLOW"},
    ]
    for person, signals in zip(people, signal_sets, strict=True):
        db.add_all(
            PositionCapability(
                person_id=person.id,
                base_position_id=position.id,
                signal=signal,
            )
            for signal in signals
        )
    db.commit()
    statements: list[str] = []

    def count_query(_connection, _cursor, statement, _parameters, _context, _many):  # type: ignore[no-untyped-def]
        statements.append(statement)

    sqlalchemy_event.listen(db.get_bind(), "before_cursor_execute", count_query)
    try:
        bulk = bulk_eligibility(db, {person.id for person in people}, {position.id})
    finally:
        sqlalchemy_event.remove(db.get_bind(), "before_cursor_execute", count_query)
    assert len(statements) == 1
    assert all(
        bulk[(person.id, position.id)] == eligibility(db, person.id, position.id)
        for person in people
    )


def test_multi_assignment_and_overnight_person_day_semantics() -> None:
    revision = WorkdayRevision(
        workday_id=uuid.uuid4(), revision_number=1, work_date=date(2026, 9, 30), created_by_user_id=uuid.uuid4(), start_time=time(7), end_time=time(20)
    )
    rows = [
        Assignment(revision_id=revision.id, display_name_snapshot="Camera 2", start_time=time(7, 30), end_time=time(12)),
        Assignment(revision_id=revision.id, display_name_snapshot="RF Camera", start_time=time(12), end_time=time(19, 30)),
    ]
    result = person_day_participation(revision, rows)
    assert (result.start, result.end, result.minutes, result.role_summary) == (time(7, 30), time(19, 30), 720, "Camera 2 + RF Camera")

    revision.start_time, revision.end_time = time(23), time(5)
    overnight = [
        Assignment(revision_id=revision.id, display_name_snapshot="Camera", start_time=time(23), end_time=time(2)),
        Assignment(revision_id=revision.id, display_name_snapshot="RF", start_time=time(1), end_time=time(5)),
    ]
    result = person_day_participation(revision, overnight)
    assert (result.start, result.end, result.minutes) == (time(23), time(5), 360)

    revision.start_time, revision.end_time = time(8), time(18)
    overlap_and_fallback = [
        Assignment(
            revision_id=revision.id,
            display_name_snapshot="Camera",
            start_time=time(8),
            end_time=time(14),
        ),
        Assignment(
            revision_id=revision.id,
            display_name_snapshot="RF",
            start_time=time(12),
            end_time=time(16),
        ),
        Assignment(revision_id=revision.id, display_name_snapshot="Camera"),
    ]
    result = person_day_participation(revision, overlap_and_fallback)
    assert (result.start, result.end, result.minutes, result.role_summary) == (
        time(8),
        time(18),
        600,
        "Camera + RF",
    )

    identical = [
        Assignment(revision_id=revision.id, display_name_snapshot="Camera", start_time=time(9), end_time=time(17)),
        Assignment(revision_id=revision.id, display_name_snapshot="RF", start_time=time(9), end_time=time(17)),
    ]
    assert person_day_participation(revision, identical).minutes == 480


def test_publication_diff_is_complete_and_redacts_note_contents(db) -> None:  # type: ignore[no-untyped-def]
    user = _user(db, "publisher@example.test")
    region = Region(name="Diff")
    old_position = BasePosition(name="Old camera")
    new_position = BasePosition(name="New camera")
    db.add_all([region, old_position, new_position])
    db.flush()
    workday = Workday(region_id=region.id, created_by_user_id=user.id)
    db.add(workday)
    db.flush()
    previous = WorkdayRevision(
        workday_id=workday.id,
        revision_number=1,
        state="PUBLISHED",
        work_date=date(2026, 9, 30),
        track_name_snapshot="Old Track",
        title="Old title",
        start_time=time(7),
        on_track_time=time(8),
        first_trial_time=time(9),
        first_race_time=time(10),
        last_race_time=time(16),
        race_count=8,
        end_time=time(17),
        day_note="Old sensitive day detail",
        created_by_user_id=user.id,
    )
    draft = WorkdayRevision(
        workday_id=workday.id,
        revision_number=2,
        state="DRAFT",
        work_date=date(2026, 10, 1),
        track_name_snapshot="New Track",
        title="New title",
        start_time=time(7, 30),
        on_track_time=time(8, 30),
        first_trial_time=time(9, 30),
        first_race_time=time(10, 30),
        last_race_time=time(16, 30),
        race_count=9,
        end_time=time(18),
        day_note="New sensitive day detail",
        created_by_user_id=user.id,
    )
    db.add_all([previous, draft])
    db.flush()
    shared_slot, removed_slot, added_slot = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    db.add_all(
        [
            Assignment(
                revision_id=previous.id,
                slot_key=shared_slot,
                base_position_id=old_position.id,
                slot_index=1,
                display_name_snapshot="Camera",
                person_name_snapshot="Old person",
                status="TBC",
                start_time=time(7),
                end_time=time(12),
                note="Old private assignment detail",
                note_private=True,
                vehicle_name_snapshot="Van 1",
                accommodation_name="Old hotel",
            ),
            Assignment(
                revision_id=draft.id,
                slot_key=shared_slot,
                base_position_id=new_position.id,
                slot_index=2,
                display_name_snapshot="Camera 2",
                person_name_snapshot="New person",
                status="OPEN",
                start_time=time(8),
                end_time=time(13),
                note="New private assignment detail",
                note_private=False,
                vehicle_name_snapshot="Van 2",
                accommodation_name="New hotel",
            ),
            Assignment(
                revision_id=previous.id,
                slot_key=removed_slot,
                display_name_snapshot="Removed role",
                status="TBC",
            ),
            Assignment(
                revision_id=draft.id,
                slot_key=added_slot,
                display_name_snapshot="Added role",
                status="OPEN",
            ),
        ]
    )
    db.flush()

    changes = publication_diff(db, previous, draft)
    labels = {change.label for change in changes}
    assert {
        "Date",
        "Title",
        "Track",
        "Start",
        "On-track time",
        "First trial",
        "First race",
        "Last race",
        "Race count",
        "Finish",
        "Normal day notes",
        "Added role",
        "Removed role",
        "Camera 2 Base position",
        "Camera 2 Display position",
        "Camera 2 Slot number",
        "Camera 2 Vehicle",
        "Camera 2 Accommodation",
    } <= labels
    summaries = " ".join(change.summary for change in changes)
    assert "Old sensitive" not in summaries and "New sensitive" not in summaries
    assert "Old private" not in summaries and "New private" not in summaries
    note_changes = [change for change in changes if "note" in change.label.lower()]
    assert note_changes and any(change.sensitive for change in note_changes)

    workday.current_published_revision_id = previous.id
    workday.current_draft_revision_id = draft.id
    draft.based_on_revision_id = previous.id
    draft.change_reason = "Sensitive management publication reason"
    db.commit()
    publish(db, workday.id, draft.id, user.id, workday.lock_version)
    history_text = " ".join(
        db.scalars(select(HumanChange.summary).where(HumanChange.workday_id == workday.id))
    )
    assert "notes were updated" in history_text
    assert "Sensitive management" not in history_text
    assert "private assignment detail" not in history_text


def test_nz_date_boundary_and_regional_observed_holidays() -> None:
    assert local_today(datetime(2026, 9, 8, 12, 30, tzinfo=UTC)) == date(2026, 9, 9)
    assert holiday_for_date(date(2026, 2, 6)) == "Waitangi Day"
    assert "ANZAC Day (observed)" in holiday_for_date(date(2026, 4, 27))
    assert holiday_for_date(date(2026, 1, 26), "Auckland") == "Auckland Anniversary Day"
    assert holiday_for_date(date(2026, 1, 19), "Wellington") == "Wellington Anniversary Day"
    assert holiday_for_date(date(2026, 1, 19), "Auckland") == ""
