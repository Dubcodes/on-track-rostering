from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy.orm import sessionmaker

from app.core.config import Settings
from app.core.time import utcnow
from app.operations.heartbeat import (
    record_scheduler_completed,
    record_scheduler_failed,
    run_scheduler_iteration,
    scheduler_health,
)
from app.operations.readiness import ReadinessCheck, ReadinessResult, evaluate_readiness


def _production_settings(**updates: object) -> Settings:
    values: dict[str, object] = {
        "secret_key": "production-secret-key-material-that-is-not-the-default",
        "credential_pepper": "configured-pepper",
        "cookie_secure": True,
        "allowed_hosts": ("roster.example.nz",),
        "webauthn_origin": "https://roster.example.nz",
        "webauthn_rp_id": "example.nz",
        "vapid_public_key": "",
        "vapid_private_key": "",
        "vapid_subject": "mailto:ops@example.nz",
        "build_id": "a" * 40,
        "mfa_required_admin": True,
        "mfa_required_manager": True,
    }
    values.update(updates)
    return Settings(**values)


def _statuses(result) -> dict[str, str]:  # type: ignore[no-untyped-def]
    return {check.name: check.status for check in result.checks}


def test_readiness_accepts_secure_configuration_and_disabled_web_push(db) -> None:  # type: ignore[no-untyped-def]
    now = datetime(2026, 10, 10, 8, tzinfo=UTC)
    record_scheduler_completed(db, {"notifications": {"status": "disabled"}}, now=now)
    result = evaluate_readiness(db, settings=_production_settings(), now=now)
    statuses = _statuses(result)
    assert result.ready
    assert statuses["Web Push keys"] == "WARN"
    assert statuses["Background scheduler"] == "PASS"
    assert statuses["Admin MFA policy"] == "PASS"


def test_readiness_rejects_insecure_and_partial_configuration(db) -> None:  # type: ignore[no-untyped-def]
    settings = _production_settings(
        secret_key="development-only-change-this-secret-key",
        credential_pepper="",
        cookie_secure=False,
        allowed_hosts=("localhost", "testserver"),
        webauthn_origin="http://localhost:8000",
        webauthn_rp_id="localhost",
        vapid_public_key="public-only",
        build_id="short",
        mfa_required_admin=False,
        mfa_required_manager=False,
    )
    result = evaluate_readiness(db, settings=settings)
    statuses = _statuses(result)
    assert not result.ready
    for name in (
        "Application secret",
        "Credential pepper",
        "Secure cookies",
        "Allowed hosts",
        "WebAuthn origin",
        "Web Push keys",
        "Build identity",
        "Background scheduler",
    ):
        assert statuses[name] == "FAIL"
    assert statuses["Admin MFA policy"] == "WARN"
    assert statuses["Manager MFA policy"] == "WARN"


def test_readiness_rejects_placeholder_contact_when_web_push_is_enabled(db) -> None:  # type: ignore[no-untyped-def]
    now = datetime(2026, 10, 10, 8, tzinfo=UTC)
    record_scheduler_completed(db, {}, now=now)
    result = evaluate_readiness(
        db,
        settings=_production_settings(
            vapid_public_key="public",
            vapid_private_key="private",
            vapid_subject="mailto:operations@example.invalid",
        ),
        now=now,
    )
    assert _statuses(result)["Web Push contact"] == "FAIL"
    assert not result.ready


def test_scheduler_health_distinguishes_stale_and_failed_runs(db) -> None:  # type: ignore[no-untyped-def]
    now = datetime(2026, 10, 10, 8, tzinfo=UTC)
    record_scheduler_completed(db, {}, now=now - timedelta(minutes=16))
    assert scheduler_health(db, interval_seconds=300, now=now).status == "stale"
    record_scheduler_failed(db, RuntimeError("token=not-for-output"), now=now)
    health = scheduler_health(db, interval_seconds=300, now=now)
    assert health.status == "error"
    assert "not-for-output" not in (health.last_error or "")


def test_scheduler_iteration_persists_success_and_failure_independently(db) -> None:  # type: ignore[no-untyped-def]
    factory = sessionmaker(bind=db.get_bind(), expire_on_commit=False)
    logger = SimpleNamespace(exception=lambda *_args, **_kwargs: None)
    now = datetime(2026, 10, 10, 8, tzinfo=UTC)

    result = run_scheduler_iteration(
        lambda _db, *, now: {"notifications": {"status": "ok"}},
        session_factory=factory,
        logger=logger,
        now=now,
    )
    assert result["notifications"] == {"status": "ok"}
    db.expire_all()
    assert scheduler_health(db, interval_seconds=300, now=utcnow()).status == "ok"

    def fail(_db, *, now):  # type: ignore[no-untyped-def]
        raise RuntimeError("password=must-not-persist")

    with pytest.raises(RuntimeError, match="must-not-persist"):
        run_scheduler_iteration(
            fail,
            session_factory=factory,
            logger=logger,
            now=now,
        )
    db.expire_all()
    health = scheduler_health(db, interval_seconds=300, now=utcnow())
    assert health.status == "error"
    assert "must-not-persist" not in (health.last_error or "")


def test_scheduler_health_endpoint_is_minimal_and_status_sensitive(db, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from app import main as main_module

    factory = sessionmaker(bind=db.get_bind(), expire_on_commit=False)
    monkeypatch.setattr(main_module, "SessionLocal", factory)
    monkeypatch.setattr(
        main_module,
        "get_settings",
        lambda: _production_settings(scheduler_interval_seconds=300),
    )
    now = utcnow()
    record_scheduler_completed(db, {}, now=now)
    response = main_module.scheduler_health_endpoint()
    assert response.status_code == 200
    assert set(json.loads(response.body)) == {"status", "last_completed_at"}

    record_scheduler_completed(db, {}, now=now - timedelta(minutes=16))
    response = main_module.scheduler_health_endpoint()
    assert response.status_code == 503
    assert "error" not in json.loads(response.body)


def test_production_check_cli_exit_status_and_safe_output(db, monkeypatch, capsys) -> None:  # type: ignore[no-untyped-def]
    from app import cli

    factory = sessionmaker(bind=db.get_bind(), expire_on_commit=False)
    monkeypatch.setattr(cli, "SessionLocal", factory)
    passing = ReadinessResult((ReadinessCheck("Database", "PASS", "Reachable."),))
    monkeypatch.setattr(cli, "evaluate_readiness", lambda _db: passing)
    assert cli.production_check_command(SimpleNamespace()) == 0
    assert capsys.readouterr().out == "PASS: Database — Reachable.\n"

    failing = ReadinessResult((ReadinessCheck("Application secret", "FAIL", "Replace it."),))
    monkeypatch.setattr(cli, "evaluate_readiness", lambda _db: failing)
    assert cli.production_check_command(SimpleNamespace()) == 1
    output = capsys.readouterr().out
    assert output == "FAIL: Application secret — Replace it.\n"
    assert "development-only-change-this-secret-key" not in output
