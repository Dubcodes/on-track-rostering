from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.rostering.models import Assignment, WorkdayRevision

from app.rostering.timing import ceil_to_quarter, clock_after

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


@dataclass(frozen=True)
class EffectivePersonTravel:
    uses_standard_travel: bool
    accommodation: str | None
    hotel_to_track_minutes: int | None
    start: time | None
    finish: time | None
    finish_destination: str | None
    return_travel_minutes: int | None


def effective_person_travel(
    revision: WorkdayRevision, assignment: Assignment
) -> EffectivePersonTravel:
    """Resolve immutable revision defaults plus nullable person overrides in one place."""
    uses_standard = bool(revision.standard_travel_enabled and assignment.uses_standard_travel)
    accommodation = assignment.accommodation_name or (
        revision.default_hotel or None if uses_standard else None
    )
    hotel_minutes = (
        assignment.hotel_to_track_minutes_override
        if assignment.hotel_to_track_minutes_override is not None
        else revision.hotel_to_track_minutes
    ) if uses_standard else None
    start = assignment.start_time
    if start is None and uses_standard and revision.on_track_time is not None and hotel_minutes is not None:
        start = (
            datetime.combine(revision.work_date, revision.on_track_time)
            - timedelta(minutes=hotel_minutes)
        ).time()
    if (
        start is None
        and assignment.transport_mode in {TRANSPORT_SELF, TRANSPORT_NOT_REQUIRED}
        and revision.on_track_time is not None
        and any(
            (
                revision.first_race_time is not None,
                revision.last_race_time is not None,
                revision.race_count is not None,
            )
        )
    ):
        start = revision.on_track_time
    if start is None:
        start = revision.start_time
    finish = assignment.end_time or revision.end_time
    return_minutes = (
        assignment.return_travel_minutes_override
        if assignment.return_travel_minutes_override is not None
        else revision.return_travel_minutes
    )
    last_event = revision.last_trial_time or revision.last_race_time
    if (
        assignment.end_time is None
        and uses_standard
        and not revision.end_time_is_override
        and assignment.return_travel_minutes_override is not None
        and last_event is not None
        and revision.on_track_time is not None
    ):
        reference = datetime.combine(revision.work_date, revision.on_track_time)
        cleared = ceil_to_quarter(clock_after(last_event, reference=reference))
        finish = (
            cleared
            + timedelta(minutes=revision.pack_up_minutes)
            + timedelta(minutes=assignment.return_travel_minutes_override)
        ).time()
    return EffectivePersonTravel(
        uses_standard_travel=uses_standard,
        accommodation=accommodation,
        hotel_to_track_minutes=hotel_minutes,
        start=start,
        finish=finish,
        finish_destination=(
            assignment.finish_destination_override or revision.finish_destination or None
        ),
        return_travel_minutes=return_minutes,
    )


def calculate_standard_travel(
    *,
    race_date: date,
    on_track_time: time,
    last_race_time: time | None,
    departure_time: time = time(12),
    travel_to_hotel_minutes: int,
    hotel_to_track_minutes: int,
    pack_up_minutes: int = 60,
    return_travel_minutes: int | None,
    explicit_finish_time: time | None = None,
) -> StandardTravelCalculation:
    durations = [
        travel_to_hotel_minutes,
        hotel_to_track_minutes,
        pack_up_minutes,
        *([return_travel_minutes] if return_travel_minutes is not None else []),
    ]
    if min(durations) < 0:
        raise ValueError("Travel and pack-up durations cannot be negative.")
    if explicit_finish_time is None and (last_race_time is None or return_travel_minutes is None):
        raise ValueError("Last event and return travel are required unless Finish is supplied.")
    travel_date = race_date - timedelta(days=1)
    travel_start_at = datetime.combine(travel_date, departure_time)
    travel_finish_at = travel_start_at + timedelta(minutes=travel_to_hotel_minutes)
    on_track_at = datetime.combine(race_date, on_track_time)
    race_start_at = on_track_at - timedelta(minutes=hotel_to_track_minutes)
    if last_race_time is not None:
        last_race_at = clock_after(last_race_time, reference=on_track_at)
        race_clear_at = ceil_to_quarter(last_race_at)
        pack_up_done_at = race_clear_at + timedelta(minutes=pack_up_minutes)
    else:
        pack_up_done_at = clock_after(explicit_finish_time, reference=on_track_at)  # type: ignore[arg-type]
        race_clear_at = pack_up_done_at
    race_finish_at = (
        clock_after(explicit_finish_time, reference=on_track_at)
        if explicit_finish_time is not None
        else pack_up_done_at + timedelta(minutes=return_travel_minutes or 0)
    )
    return StandardTravelCalculation(
        travel_date=travel_date,
        travel_start=travel_start_at.time(),
        travel_finish=travel_finish_at.time(),
        race_start=race_start_at.time(),
        race_clear=race_clear_at.time(),
        pack_up_done=pack_up_done_at.time(),
        race_finish=race_finish_at.time(),
    )
