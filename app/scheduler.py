from __future__ import annotations

import logging
import time
from contextlib import contextmanager
from datetime import timedelta

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.core.time import utcnow
from app.external_calendar.detail_refresh import refresh_due_programmes
from app.external_calendar.http import SourceHTTPClient
from app.external_calendar.refresh import ensure_provider_states, refresh_provider
from app.track_maps.service import due_tracks, refresh_automatic_map

logger = logging.getLogger(__name__)
LOCK_ID = 664_201_903


@contextmanager
def scheduler_lock(db: Session):
    if db.bind is None or db.bind.dialect.name != "postgresql":
        yield True
        return
    # Keep one dedicated connection checked out for the whole cycle. Scheduler
    # services commit their own bounded transactions; a lock taken through the
    # ORM Session could otherwise be returned to the pool at a commit boundary.
    with db.get_bind().connect() as connection:
        acquired = bool(connection.scalar(text("SELECT pg_try_advisory_lock(:key)"), {"key": LOCK_ID}))
        try:
            yield acquired
        finally:
            if acquired:
                connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": LOCK_ID})


def scheduler_tick(db: Session, *, now=None) -> dict[str, object]:  # type: ignore[no-untyped-def]
    now = now or utcnow()
    with scheduler_lock(db) as acquired:
        if not acquired:
            return {"status": "locked"}
        states = ensure_provider_states(db)
        db.commit()
        provider_results: dict[str, str] = {}
        for provider in ("LOVE_RACING", "HRNZ"):
            state = states[provider]
            if not state.enabled:
                provider_results[provider] = "DISABLED"
                continue
            due = state.next_refresh_at is None or now >= state.next_refresh_at
            if not due and state.last_success_at:
                due = now - state.last_success_at >= timedelta(hours=48)
            if due:
                result = refresh_provider(db, provider)
                provider_results[provider] = result.status
            else:
                provider_results[provider] = "NOT_DUE"
        programmes = (
            refresh_due_programmes(db, now=now)
            if states["LOVE_RACING"].enabled
            else {"checked": 0, "updated": 0, "failed": 0, "status": "DISABLED"}
        )
        settings = get_settings()
        client = SourceHTTPClient(
            timeout=settings.racing_source_timeout_seconds,
            max_bytes=settings.racing_source_max_bytes,
        )
        maps = {"checked": 0, "failed": 0}
        for track in due_tracks(db)[:10]:
            row = refresh_automatic_map(db, track, client)
            maps["checked"] += 1
            maps["failed"] += row.automatic_status == "ERROR"
        db.commit()
        return {
            "status": "ok",
            "providers": provider_results,
            "programmes": programmes,
            "maps": maps,
        }


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    interval = get_settings().scheduler_interval_seconds
    while True:
        try:
            with SessionLocal() as db:
                logger.info("scheduler_tick result=%s", scheduler_tick(db))
        except Exception:
            logger.exception("scheduler_tick_failed")
        time.sleep(interval)


if __name__ == "__main__":
    main()
