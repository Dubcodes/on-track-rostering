from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

TRANSPORT_UNASSIGNED = "UNASSIGNED"
TRANSPORT_VEHICLE = "VEHICLE"
TRANSPORT_SELF = "SELF_TRAVEL"
TRANSPORT_NOT_REQUIRED = "NOT_REQUIRED"
TRANSPORT_CUSTOM = "CUSTOM"

TRANSPORT_MODES = {
    TRANSPORT_UNASSIGNED,
    TRANSPORT_VEHICLE,
    TRANSPORT_SELF,
    TRANSPORT_NOT_REQUIRED,
    TRANSPORT_CUSTOM,
}

TRANSPORT_LABELS = {
    TRANSPORT_UNASSIGNED: "No transport assigned yet",
    TRANSPORT_VEHICLE: "Vehicle",
    TRANSPORT_SELF: "Making own way",
    TRANSPORT_NOT_REQUIRED: "No transport required",
    TRANSPORT_CUSTOM: "Custom transport",
}


def transport_display(mode: str, vehicle_name: str | None, custom_text: str) -> str:
    if mode == TRANSPORT_VEHICLE:
        return vehicle_name or TRANSPORT_LABELS[TRANSPORT_UNASSIGNED]
    if mode == TRANSPORT_CUSTOM:
        return custom_text.strip() or TRANSPORT_LABELS[TRANSPORT_UNASSIGNED]
    return TRANSPORT_LABELS.get(mode, TRANSPORT_LABELS[TRANSPORT_UNASSIGNED])


@dataclass(frozen=True)
class StandardTravelCalculation:
    travel_date: date
    travel_start: time
    travel_finish: time
    race_start: time
    race_clear: time
    pack_up_done: time
    race_finish: time


def _clock_after(value: time, *, reference: datetime) -> datetime:
    result = datetime.combine(reference.date(), value)
    if result < reference:
        result += timedelta(days=1)
    return result


def ceil_to_quarter(value: datetime) -> datetime:
    """Round up to a quarter hour. No race-run allowance is applied."""
    if value.second or value.microsecond or value.minute % 15:
        value += timedelta(minutes=15 - value.minute % 15)
    return value.replace(second=0, microsecond=0)


def calculate_standard_travel(
    *,
    race_date: date,
    on_track_time: time,
    last_race_time: time,
    departure_time: time = time(12),
    travel_to_hotel_minutes: int,
    hotel_to_track_minutes: int,
    pack_up_minutes: int = 60,
    return_travel_minutes: int,
) -> StandardTravelCalculation:
    if min(
        travel_to_hotel_minutes,
        hotel_to_track_minutes,
        pack_up_minutes,
        return_travel_minutes,
    ) < 0:
        raise ValueError("Travel and pack-up durations cannot be negative.")
    travel_date = race_date - timedelta(days=1)
    travel_start_at = datetime.combine(travel_date, departure_time)
    travel_finish_at = travel_start_at + timedelta(minutes=travel_to_hotel_minutes)
    on_track_at = datetime.combine(race_date, on_track_time)
    race_start_at = on_track_at - timedelta(minutes=hotel_to_track_minutes)
    last_race_at = _clock_after(last_race_time, reference=on_track_at)
    race_clear_at = ceil_to_quarter(last_race_at)
    pack_up_done_at = race_clear_at + timedelta(minutes=pack_up_minutes)
    race_finish_at = pack_up_done_at + timedelta(minutes=return_travel_minutes)
    return StandardTravelCalculation(
        travel_date=travel_date,
        travel_start=travel_start_at.time(),
        travel_finish=travel_finish_at.time(),
        race_start=race_start_at.time(),
        race_clear=race_clear_at.time(),
        pack_up_done=pack_up_done_at.time(),
        race_finish=race_finish_at.time(),
    )
