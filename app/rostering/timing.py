from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta


@dataclass(frozen=True)
class RaceDayTiming:
    on_track: time | None
    start: time | None
    finish: time | None


def floor_to_quarter(value: datetime) -> datetime:
    """Round down to a quarter hour without applying a race-run allowance."""
    return value.replace(minute=value.minute - value.minute % 15, second=0, microsecond=0)


def ceil_to_quarter(value: datetime) -> datetime:
    """Round up to a quarter hour without applying a race-run allowance."""
    if value.second or value.microsecond or value.minute % 15:
        value += timedelta(minutes=15 - value.minute % 15)
    return value.replace(second=0, microsecond=0)


def clock_after(value: time, *, reference: datetime) -> datetime:
    result = datetime.combine(reference.date(), value)
    if result < reference:
        result += timedelta(days=1)
    return result


def derive_race_day_timing(
    *,
    work_date: date,
    first_race_time: time | None,
    last_race_time: time | None,
    setup_lead_minutes: int,
    track_travel_minutes: int | None,
    pack_up_minutes: int,
    return_travel_minutes: int | None,
    on_track_time: time | None = None,
    on_track_is_override: bool = False,
    start_time: time | None = None,
    start_is_override: bool = False,
    finish_time: time | None = None,
    finish_is_override: bool = False,
) -> RaceDayTiming:
    durations = (
        setup_lead_minutes,
        pack_up_minutes,
        *(() if track_travel_minutes is None else (track_travel_minutes,)),
        *(() if return_travel_minutes is None else (return_travel_minutes,)),
    )
    if any(value < 0 or value > 1440 for value in durations):
        raise ValueError("Race Day timing durations must be between 0 and 1440 minutes.")

    first_race_at = (
        datetime.combine(work_date, first_race_time) if first_race_time is not None else None
    )
    if not on_track_is_override:
        on_track_time = (
            floor_to_quarter(first_race_at) - timedelta(minutes=setup_lead_minutes)
        ).time() if first_race_at is not None else None

    if not start_is_override:
        start_time = (
            datetime.combine(work_date, on_track_time)
            - timedelta(minutes=track_travel_minutes or 0)
        ).time() if on_track_time is not None else None

    if not finish_is_override:
        if last_race_time is None:
            finish_time = None
        else:
            reference = (
                datetime.combine(work_date, on_track_time)
                if on_track_time is not None
                else datetime.combine(work_date, time.min)
            )
            last_race_at = clock_after(last_race_time, reference=reference)
            finish_time = (
                ceil_to_quarter(last_race_at)
                + timedelta(minutes=pack_up_minutes)
                + timedelta(minutes=return_travel_minutes or 0)
            ).time()

    return RaceDayTiming(on_track=on_track_time, start=start_time, finish=finish_time)
