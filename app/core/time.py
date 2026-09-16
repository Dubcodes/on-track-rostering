from __future__ import annotations

import re
from datetime import UTC, date, datetime, time, timedelta

from app.core.config import get_settings


def utcnow() -> datetime:
    return datetime.now(UTC)


def local_today(now: datetime | None = None) -> date:
    """Return the product calendar date in its configured operating timezone."""
    value = now or utcnow()
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(get_settings().timezone).date()


def display_datetime(value: datetime | None, fmt: str = "%d %b %Y %H:%M") -> str:
    if value is None:
        return ""
    return value.astimezone(get_settings().timezone).strftime(fmt)


def display_time(value: time | None) -> str:
    return value.strftime("%H:%M") if value else ""


def parse_time(value: str) -> time | None:
    """Accept minute-precision clock input, not timezone/seconds or ambiguous hours."""
    value = value.strip()
    if not value:
        return None
    if re.fullmatch(r"\d{3,4}", value, flags=re.ASCII):
        value = f"{value[:-2]}:{value[-2:]}"
    if not re.fullmatch(r"\d{1,2}:\d{2}", value, flags=re.ASCII):
        raise ValueError("Enter a time such as 930 or 09:30")
    hour, minute = map(int, value.split(":"))
    try:
        return time(hour, minute)
    except ValueError as exc:
        raise ValueError("Enter a valid time between 00:00 and 23:59") from exc


def worked_minutes(day: date, start: time | None, end: time | None) -> int:
    if start is None or end is None:
        return 0
    start_at = datetime.combine(day, start)
    end_at = datetime.combine(day, end)
    if end_at < start_at:
        end_at += timedelta(days=1)
    return int((end_at - start_at).total_seconds() // 60)
