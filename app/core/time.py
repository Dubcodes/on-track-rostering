from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta

from app.core.config import get_settings


def utcnow() -> datetime:
    return datetime.now(UTC)


def display_datetime(value: datetime | None, fmt: str = "%d %b %Y %H:%M") -> str:
    if value is None:
        return ""
    return value.astimezone(get_settings().timezone).strftime(fmt)


def display_time(value: time | None) -> str:
    return value.strftime("%H:%M") if value else ""


def worked_minutes(day: date, start: time | None, end: time | None) -> int:
    if start is None or end is None:
        return 0
    start_at = datetime.combine(day, start)
    end_at = datetime.combine(day, end)
    if end_at < start_at:
        end_at += timedelta(days=1)
    return int((end_at - start_at).total_seconds() // 60)
