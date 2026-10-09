from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.core.config import Settings
from app.scheduler import scheduler_tick


def _settings(*, enabled: bool) -> SimpleNamespace:
    return SimpleNamespace(
        vapid_public_key="public" if enabled else "",
        vapid_private_key="private" if enabled else "",
        racing_source_timeout_seconds=15.0,
        racing_source_max_bytes=2_000_000,
    )


def _disable_racing_and_maps(monkeypatch, calls: list[str] | None = None) -> None:  # type: ignore[no-untyped-def]
    states = {
        provider: SimpleNamespace(enabled=False, next_refresh_at=None, last_success_at=None)
        for provider in ("LOVE_RACING", "HRNZ")
    }
    monkeypatch.setattr("app.scheduler.ensure_provider_states", lambda _db: states)
    monkeypatch.setattr("app.scheduler.due_tracks", lambda _db: [])
    monkeypatch.setattr(
        "app.scheduler.refresh_all_contractor_access",
        lambda _db, *, now: (
            calls.append("contractors") if calls is not None else None
        )
        or {"checked": 0, "changed": 0, "expired": 0},
    )


def test_scheduler_default_cadence_is_five_minutes() -> None:
    assert Settings.model_fields["scheduler_interval_seconds"].default == 300


def test_scheduler_runs_notification_pipeline_and_reports_counts(db, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    now = datetime(2026, 10, 10, 8, tzinfo=UTC)
    calls: list[object] = []
    monkeypatch.setattr("app.scheduler.get_settings", lambda: _settings(enabled=True))
    _disable_racing_and_maps(monkeypatch, calls)  # type: ignore[arg-type]
    monkeypatch.setattr(
        "app.scheduler.generate_reminders",
        lambda _db, *, now: calls.append(("reminders", now)) or 2,
    )
    monkeypatch.setattr(
        "app.scheduler.generate_periodic_digests",
        lambda _db, *, now: calls.append(("digests", now)) or 1,
    )
    monkeypatch.setattr(
        "app.scheduler.process_pending",
        lambda _db, *, limit: calls.append(("processed", limit)) or 3,
    )

    result = scheduler_tick(db, now=now)

    assert result["notifications"] == {
        "status": "OK",
        "reminders": 2,
        "digests": 1,
        "processed": 3,
    }
    assert calls == [
        ("reminders", now),
        ("digests", now),
        ("processed", 50),
        "contractors",
    ]


def test_scheduler_is_quiet_when_vapid_is_disabled(db, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr("app.scheduler.get_settings", lambda: _settings(enabled=False))
    _disable_racing_and_maps(monkeypatch)
    for name in ("generate_reminders", "generate_periodic_digests", "process_pending"):
        monkeypatch.setattr(
            f"app.scheduler.{name}",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("notification call")),
        )

    result = scheduler_tick(db, now=datetime(2026, 10, 10, 8, tzinfo=UTC))

    assert result["notifications"] == {
        "status": "DISABLED",
        "reminders": 0,
        "digests": 0,
        "processed": 0,
    }


def test_notification_failure_does_not_stop_other_maintenance(db, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    calls: list[str] = []
    monkeypatch.setattr("app.scheduler.get_settings", lambda: _settings(enabled=True))
    _disable_racing_and_maps(monkeypatch, calls)
    monkeypatch.setattr(
        "app.scheduler.generate_reminders",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("notification failure")),
    )

    result = scheduler_tick(db, now=datetime(2026, 10, 10, 8, tzinfo=UTC))

    assert result["notifications"]["status"] == "ERROR"  # type: ignore[index]
    assert calls == ["contractors"]
    assert result["providers"] == {"LOVE_RACING": "DISABLED", "HRNZ": "DISABLED"}
    assert result["maps"] == {"checked": 0, "failed": 0}


def test_five_minute_ticks_preserve_provider_due_check_and_notification_idempotency(
    db, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    now = datetime(2026, 10, 10, 8, tzinfo=UTC)
    states = {
        "LOVE_RACING": SimpleNamespace(enabled=True, next_refresh_at=None, last_success_at=None),
        "HRNZ": SimpleNamespace(enabled=False, next_refresh_at=None, last_success_at=None),
    }
    refreshes: list[str] = []
    reminder_counts = iter([2, 0])
    monkeypatch.setattr("app.scheduler.get_settings", lambda: _settings(enabled=True))
    monkeypatch.setattr("app.scheduler.ensure_provider_states", lambda _db: states)

    def refresh(_db, provider: str) -> SimpleNamespace:  # type: ignore[no-untyped-def]
        refreshes.append(provider)
        states[provider].next_refresh_at = now + timedelta(hours=1)
        states[provider].last_success_at = now
        return SimpleNamespace(status="OK")

    monkeypatch.setattr("app.scheduler.refresh_provider", refresh)
    monkeypatch.setattr(
        "app.scheduler.refresh_due_programmes",
        lambda _db, *, now: {"checked": 0, "updated": 0, "failed": 0},
    )
    monkeypatch.setattr("app.scheduler.due_tracks", lambda _db: [])
    monkeypatch.setattr(
        "app.scheduler.refresh_all_contractor_access",
        lambda _db, *, now: {"checked": 0, "changed": 0, "expired": 0},
    )
    monkeypatch.setattr(
        "app.scheduler.generate_reminders", lambda _db, *, now: next(reminder_counts)
    )
    monkeypatch.setattr("app.scheduler.generate_periodic_digests", lambda _db, *, now: 0)
    monkeypatch.setattr("app.scheduler.process_pending", lambda _db, *, limit: 0)

    first = scheduler_tick(db, now=now)
    second = scheduler_tick(db, now=now + timedelta(minutes=5))

    assert refreshes == ["LOVE_RACING"]
    assert first["notifications"]["reminders"] == 2  # type: ignore[index]
    assert second["notifications"]["reminders"] == 0  # type: ignore[index]
    assert second["providers"]["LOVE_RACING"] == "NOT_DUE"  # type: ignore[index]


def test_racing_failure_occurs_after_notification_delivery(db, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    calls: list[str] = []
    states = {
        "LOVE_RACING": SimpleNamespace(enabled=True, next_refresh_at=None, last_success_at=None),
        "HRNZ": SimpleNamespace(enabled=False, next_refresh_at=None, last_success_at=None),
    }
    monkeypatch.setattr("app.scheduler.get_settings", lambda: _settings(enabled=True))
    monkeypatch.setattr("app.scheduler.generate_reminders", lambda _db, *, now: 0)
    monkeypatch.setattr("app.scheduler.generate_periodic_digests", lambda _db, *, now: 0)
    monkeypatch.setattr(
        "app.scheduler.process_pending",
        lambda _db, *, limit: calls.append("notifications") or 0,
    )
    monkeypatch.setattr("app.scheduler.ensure_provider_states", lambda _db: states)
    monkeypatch.setattr(
        "app.scheduler.refresh_provider",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("racing failure")),
    )

    with pytest.raises(RuntimeError, match="racing failure"):
        scheduler_tick(db, now=datetime(2026, 10, 10, 8, tzinfo=UTC))

    assert calls == ["notifications"]
