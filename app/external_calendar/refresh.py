from __future__ import annotations

import argparse
import logging
import sys
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit.service import record_audit
from app.core.config import get_settings
from app.core.database import SessionLocal
from app.core.time import utcnow
from app.external_calendar.adapters import CalendarProviderAdapter
from app.external_calendar.models import ExternalProviderState
from app.external_calendar.providers import provider_adapters
from app.external_calendar.service import reconcile_observation

logger = logging.getLogger(__name__)


@dataclass
class RefreshResult:
    provider: str
    status: str
    observations: int = 0
    created: int = 0
    matched: int = 0
    enriched: int = 0
    conflicts: int = 0
    unresolved: int = 0
    duplicates: int = 0
    warnings: list[str] = field(default_factory=list)
    components: dict[str, str] = field(default_factory=dict)


def ensure_provider_states(db: Session) -> dict[str, ExternalProviderState]:
    settings = get_settings()
    states = {row.provider: row for row in db.scalars(select(ExternalProviderState))}
    for provider in (*provider_adapters(), "API"):
        if provider not in states:
            state = ExternalProviderState(
                provider=provider,
                enabled=settings.racing_sources_enabled_default if provider != "API" else False,
                status=(
                    "NOT_CONFIGURED"
                    if provider == "API"
                    else ("READY" if settings.racing_sources_enabled_default else "DISABLED")
                ),
            )
            db.add(state)
            states[provider] = state
    db.flush()
    return states


def refresh_provider(
    db: Session,
    provider: str,
    *,
    actor_user_id: uuid.UUID | None = None,
    adapter: CalendarProviderAdapter | None = None,
) -> RefreshResult:
    provider = provider.strip().upper()
    adapters = provider_adapters()
    adapter = adapter or adapters.get(provider)
    if adapter is None:
        raise ValueError("Provider is not configured.")
    ensure_provider_states(db)
    db.commit()
    state = db.scalar(
        select(ExternalProviderState)
        .where(ExternalProviderState.provider == provider)
        .with_for_update()
    )
    assert state is not None
    if not state.enabled:
        return RefreshResult(provider, "DISABLED", warnings=["Provider is disabled."])
    now = utcnow()
    if state.status == "FETCHING" and state.last_attempt_at and now - state.last_attempt_at < timedelta(minutes=10):
        raise ValueError("A refresh is already in progress for this provider.")
    settings = get_settings()
    start = now.date() - timedelta(days=settings.racing_source_lookback_days)
    end = now.date() + timedelta(days=settings.racing_source_horizon_days)
    state.status = "FETCHING"
    state.last_attempt_at = now
    db.commit()
    started = time.monotonic()
    try:
        payload = adapter.fetch(start, end)
        normalized = adapter.normalize(payload, start, end)
        result = RefreshResult(provider, "OK", components=normalized.components)
        for observation in normalized.observations:
            try:
                _event, outcome = reconcile_observation(db, observation)
            except (TypeError, ValueError) as exc:
                result.warnings.append(f"Rejected one malformed observation: {type(exc).__name__}.")
                continue
            result.observations += 1
            if _event is None:
                result.unresolved += 1
            elif outcome == "CREATED":
                result.created += 1
            elif outcome == "ENRICHED":
                result.enriched += 1
            elif outcome == "CONFLICT":
                result.conflicts += 1
            elif outcome == "DUPLICATE":
                result.duplicates += 1
            elif outcome == "MATCHED":
                result.matched += 1
        result.warnings.extend(normalized.warnings)
        if result.warnings or result.conflicts or result.unresolved or any(
            value not in {"OK", "OK_FALLBACK"} for value in result.components.values()
        ):
            result.status = "PARTIAL"
        state = db.get(ExternalProviderState, provider)
        assert state is not None
        state.status = result.status
        state.last_success_at = utcnow()
        state.observations_found = result.observations
        state.events_created = result.created
        state.events_enriched = result.enriched
        state.warning_count = len(result.warnings) + result.conflicts + result.unresolved
        record_audit(
            db,
            "external_provider.refreshed",
            "external_provider",
            provider,
            actor_user_id,
            detail=asdict(result),
        )
        db.commit()
        logger.info(
            "external_calendar_refresh provider=%s start=%s end=%s duration_ms=%d observations=%d created=%d matched=%d enriched=%d conflicts=%d unresolved=%d warnings=%d status=%s",
            provider,
            start,
            end,
            int((time.monotonic() - started) * 1000),
            result.observations,
            result.created,
            result.matched,
            result.enriched,
            result.conflicts,
            result.unresolved,
            len(result.warnings),
            result.status,
        )
        return result
    except Exception as exc:
        db.rollback()
        state = db.get(ExternalProviderState, provider)
        assert state is not None
        state.status = "ERROR"
        state.warning_count = 1
        result = RefreshResult(provider, "ERROR", warnings=[f"{type(exc).__name__}: {exc}"])
        record_audit(
            db,
            "external_provider.refresh_failed",
            "external_provider",
            provider,
            actor_user_id,
            detail={
                "provider": provider,
                "error_class": type(exc).__name__,
                "message": str(exc)[:200],
            },
        )
        db.commit()
        logger.warning(
            "external_calendar_refresh_failed provider=%s start=%s end=%s duration_ms=%d error_class=%s",
            provider,
            start,
            end,
            int((time.monotonic() - started) * 1000),
            type(exc).__name__,
        )
        return result


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Refresh public racing calendar planning evidence.")
    parser.add_argument("provider", choices=("LOVE_RACING", "HRNZ", "ALL"))
    args = parser.parse_args(argv)
    providers = ("LOVE_RACING", "HRNZ") if args.provider == "ALL" else (args.provider,)
    exit_code = 0
    with SessionLocal() as db:
        ensure_provider_states(db)
        db.commit()
        for provider in providers:
            result = refresh_provider(db, provider)
            print(
                f"{provider}: {result.status}; observations={result.observations}; "
                f"created={result.created}; enriched={result.enriched}; warnings={len(result.warnings)}"
            )
            if result.status == "ERROR":
                exit_code = 1
    return exit_code


if __name__ == "__main__":
    sys.exit(_main())
