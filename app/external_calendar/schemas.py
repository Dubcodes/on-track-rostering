from __future__ import annotations

from datetime import date, time
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator

from app.core.enums import CapabilitySignal
from app.core.time import parse_time


class ImportRow(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class RegionImport(ImportRow):
    name: str = Field(min_length=1, max_length=100)
    lifecycle: Literal["ACTIVE", "ARCHIVED"] = "ACTIVE"


class TrackImport(ImportRow):
    name: str = Field(min_length=1, max_length=120)
    region: str = Field(min_length=1, max_length=100)
    map_reference: str | None = Field(default=None, max_length=500)
    lifecycle: Literal["ACTIVE", "ARCHIVED"] = "ACTIVE"


class CrewGroupImport(ImportRow):
    name: str = Field(min_length=1, max_length=100)
    lifecycle: Literal["ACTIVE", "ARCHIVED"] = "ACTIVE"


class PositionImport(ImportRow):
    name: str = Field(min_length=1, max_length=100)
    crew_group: str = Field(min_length=1, max_length=100)
    lifecycle: Literal["ACTIVE", "ARCHIVED"] = "ACTIVE"


class CapabilityImport(ImportRow):
    crew_group: str = Field(min_length=1, max_length=100)
    position: str = Field(min_length=1, max_length=100)
    signal: CapabilitySignal = CapabilitySignal.MANAGER_ALLOW


class PersonImport(ImportRow):
    display_name: str = Field(min_length=1, max_length=120)
    email: str | None = Field(default=None, max_length=320)
    home_region: str | None = Field(default=None, max_length=100)
    crew_groups: list[str] = Field(default_factory=list, max_length=50)
    capabilities: list[CapabilityImport] = Field(default_factory=list, max_length=100)


class ExternalFactsImport(ImportRow):
    first_trial_time: time | None = None
    first_race_time: time | None = None
    last_race_time: time | None = None
    race_count: int | None = Field(default=None, ge=0, le=100)
    status: str | None = Field(default=None, min_length=1, max_length=24)

    @field_validator("first_trial_time", "first_race_time", "last_race_time", mode="before")
    @classmethod
    def friendly_time(cls, value: object) -> object:
        if value in (None, "") or isinstance(value, time):
            return value
        parsed = parse_time(str(value))
        if parsed is None:
            raise ValueError("time must be HH:MM, H:MM, HHMM, or HMM")
        return parsed

    @field_serializer("first_trial_time", "first_race_time", "last_race_time")
    def serialize_time(self, value: time | None) -> str | None:
        return value.strftime("%H:%M") if value else None


class ExternalEventImport(ImportRow):
    provider: str = Field(min_length=1, max_length=40)
    provider_event_id: str | None = Field(default=None, max_length=160)
    date: date
    track: str = Field(min_length=1, max_length=160)
    discipline: Literal["THOROUGHBRED", "HARNESS"]
    event_kind: Literal["RACE", "TRIAL"]
    facts: ExternalFactsImport = Field(default_factory=ExternalFactsImport)
    raw: dict[str, object] | None = None


class ExternalTrackMappingImport(ImportRow):
    provider: str = Field(min_length=1, max_length=40)
    external_track_name: str = Field(min_length=1, max_length=160)
    track: str = Field(min_length=1, max_length=120)
    region: str = Field(min_length=1, max_length=100)


class ImportBundle(ImportRow):
    version: Literal["1"] = "1"
    regions: list[RegionImport] = Field(default_factory=list, max_length=500)
    tracks: list[TrackImport] = Field(default_factory=list, max_length=1000)
    crew_groups: list[CrewGroupImport] = Field(default_factory=list, max_length=500)
    positions: list[PositionImport] = Field(default_factory=list, max_length=2000)
    people: list[PersonImport] = Field(default_factory=list, max_length=10000)
    external_track_mappings: list[ExternalTrackMappingImport] = Field(
        default_factory=list, max_length=5000
    )
    external_events: list[ExternalEventImport] = Field(default_factory=list, max_length=10000)
