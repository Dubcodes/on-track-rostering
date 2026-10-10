from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urlsplit

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.operations.heartbeat import scheduler_health


@dataclass(frozen=True)
class ReadinessCheck:
    name: str
    status: str
    message: str


@dataclass(frozen=True)
class ReadinessResult:
    checks: tuple[ReadinessCheck, ...]

    @property
    def ready(self) -> bool:
        return all(check.status != "FAIL" for check in self.checks)


def _check(condition: bool, name: str, success: str, failure: str) -> ReadinessCheck:
    return ReadinessCheck(name, "PASS" if condition else "FAIL", success if condition else failure)


def evaluate_readiness(
    db: Session,
    *,
    settings: Settings | None = None,
    now: datetime | None = None,
) -> ReadinessResult:
    config = settings or get_settings()
    checks: list[ReadinessCheck] = []
    checks.append(
        _check(
            config.secret_key != "development-only-change-this-secret-key",
            "Application secret",
            "A non-default application secret is configured.",
            "Replace the development-only application secret.",
        )
    )
    checks.append(
        _check(
            bool(config.credential_pepper.strip()),
            "Credential pepper",
            "A credential pepper is configured.",
            "Configure a non-empty credential pepper.",
        )
    )
    checks.append(
        _check(
            config.cookie_secure,
            "Secure cookies",
            "Session cookies require HTTPS.",
            "Enable secure session cookies.",
        )
    )

    local_hosts = {"localhost", "127.0.0.1", "::1", "testserver"}
    public_hosts = {
        host.lower().strip("[]")
        for host in config.allowed_hosts
        if host.lower().strip("[]") not in local_hosts | {"*"}
    }
    checks.append(
        _check(
            bool(public_hosts),
            "Allowed hosts",
            "At least one non-local host is allowed.",
            "Configure the production hostname in allowed hosts.",
        )
    )

    origin = urlsplit(config.webauthn_origin)
    origin_host = (origin.hostname or "").lower().rstrip(".")
    rp_id = config.webauthn_rp_id.lower().rstrip(".")
    webauthn_ok = (
        origin.scheme == "https"
        and bool(origin_host)
        and origin.path in {"", "/"}
        and not origin.query
        and not origin.fragment
        and rp_id not in local_hosts
        and (origin_host == rp_id or origin_host.endswith(f".{rp_id}"))
    )
    checks.append(
        _check(
            webauthn_ok,
            "WebAuthn origin",
            "WebAuthn uses HTTPS with a compatible relying-party hostname.",
            "Use an HTTPS WebAuthn origin and matching non-local relying-party ID.",
        )
    )

    public_vapid = bool(config.vapid_public_key.strip())
    private_vapid = bool(config.vapid_private_key.strip())
    if public_vapid != private_vapid:
        checks.append(
            ReadinessCheck(
                "Web Push keys", "FAIL", "Configure both VAPID keys or leave both unset."
            )
        )
    elif public_vapid:
        checks.append(ReadinessCheck("Web Push keys", "PASS", "Both VAPID keys are configured."))
    else:
        checks.append(
            ReadinessCheck(
                "Web Push keys", "WARN", "Web Push is deliberately disabled because no VAPID keys are configured."
            )
        )
    placeholder_subject = (
        not config.vapid_subject.strip()
        or ".invalid" in config.vapid_subject.lower()
        or "example." in config.vapid_subject.lower()
    )
    checks.append(
        ReadinessCheck(
            "Web Push contact",
            "FAIL" if placeholder_subject and public_vapid and private_vapid else (
                "WARN" if placeholder_subject else "PASS"
            ),
            (
                "Replace the placeholder VAPID contact before enabling Web Push."
                if placeholder_subject
                else "The VAPID contact is configured."
            ),
        )
    )
    checks.append(
        _check(
            bool(re.fullmatch(r"[0-9a-fA-F]{40}", config.build_id.strip())),
            "Build identity",
            "The running image reports a full commit SHA.",
            "Build and run an image carrying a full commit SHA.",
        )
    )

    database_ok = True
    try:
        db.execute(text("SELECT 1"))
    except Exception:
        database_ok = False
        checks.append(ReadinessCheck("Database", "FAIL", "The database check failed."))
    else:
        checks.append(ReadinessCheck("Database", "PASS", "The database is reachable."))

    health = (
        scheduler_health(db, interval_seconds=config.scheduler_interval_seconds, now=now)
        if database_ok
        else None
    )
    checks.append(
        ReadinessCheck(
            "Background scheduler",
            "PASS" if health and health.status == "ok" else "FAIL",
            (
                "The scheduler heartbeat is healthy."
                if health and health.status == "ok"
                else (
                    f"The scheduler heartbeat is {health.label.lower()}."
                    if health
                    else "Scheduler health could not be checked because the database is unavailable."
                )
            ),
        )
    )
    checks.append(
        ReadinessCheck(
            "Admin MFA policy",
            "PASS" if config.mfa_required_admin else "WARN",
            (
                "MFA is required for Admin accounts."
                if config.mfa_required_admin
                else "MFA is not required for Admin accounts."
            ),
        )
    )
    checks.append(
        ReadinessCheck(
            "Manager MFA policy",
            "PASS" if config.mfa_required_manager else "WARN",
            (
                "MFA is required for Manager accounts."
                if config.mfa_required_manager
                else "MFA is not required for Manager accounts."
            ),
        )
    )
    return ReadinessResult(tuple(checks))
