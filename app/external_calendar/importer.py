from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import date, time

from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.catalog.models import BasePosition, CrewGroup, PersonCrewGroup, Region, Track
from app.catalog.service import allocate_palette_slot
from app.core.enums import CapabilitySignal
from app.core.time import parse_time
from app.external_calendar.models import ExternalCalendarEvent
from app.external_calendar.schemas import ImportBundle
from app.external_calendar.service import (
    ProviderObservation,
    confirm_track_mapping,
    mapped_track,
    normalized_key,
    reconcile_observation,
)
from app.identity.models import Person
from app.rostering.models import PositionCapability

FORBIDDEN_KEY_PARTS = {
    "password",
    "pin",
    "credential",
    "secret",
    "token",
    "bank",
    "payroll",
}


@dataclass
class ImportPlan:
    counts: dict[str, dict[str, int]] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    valid: bool = True

    def bump(self, section: str, result: str) -> None:
        values = self.counts.setdefault(section, {})
        values[result] = values.get(result, 0) + 1

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def parse_bundle(raw: str) -> dict[str, object]:
    try:
        bundle = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON at line {exc.lineno}.") from exc
    if not isinstance(bundle, dict):
        raise ValueError("Import bundle must be a JSON object.")

    def inspect(value: object) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                words = {
                    word
                    for word in re.split(r"[^a-z0-9]+", re.sub(r"([a-z])([A-Z])", r"\1_\2", str(key)).casefold())
                    if word
                }
                if words & FORBIDDEN_KEY_PARTS:
                    raise ValueError(f"Import bundle contains forbidden field: {key}.")
                inspect(child)
        elif isinstance(value, list):
            for child in value:
                inspect(child)

    inspect(bundle)
    try:
        typed = ImportBundle.model_validate(bundle)
    except ValidationError as exc:
        first = exc.errors(include_url=False)[0]
        location = ".".join(str(part) for part in first["loc"])
        raise ValueError(f"Invalid import field {location}: {first['msg']}.") from exc
    return typed.model_dump(mode="json", exclude_none=True)


def _named(rows: list[object], name: str):  # type: ignore[no-untyped-def]
    key = normalized_key(name)
    return [row for row in rows if normalized_key(row.name) == key]


def preview_bundle(db: Session, bundle: dict[str, object]) -> ImportPlan:
    plan = ImportPlan()
    regions = list(db.scalars(select(Region)))
    tracks = list(db.scalars(select(Track)))
    groups = list(db.scalars(select(CrewGroup)))
    positions = list(db.scalars(select(BasePosition)))
    people = list(db.scalars(select(Person)))
    bundle_regions = {normalized_key(str(row.get("name", ""))) for row in bundle.get("regions", [])}
    bundle_groups = {normalized_key(str(row.get("name", ""))) for row in bundle.get("crew_groups", [])}
    bundle_positions = {
        (normalized_key(str(row.get("crew_group", ""))), normalized_key(str(row.get("name", ""))))
        for row in bundle.get("positions", [])
    }
    for row in bundle.get("regions", []):
        matches = _named(regions, str(row.get("name", "")))
        plan.bump("regions", "match" if len(matches) == 1 else "conflict" if matches else "create")
    for row in bundle.get("tracks", []):
        region = _named(regions, str(row.get("region", "")))
        matches = [
            item
            for item in tracks
            if region
            and item.region_id == region[0].id
            and normalized_key(item.name) == normalized_key(str(row.get("name", "")))
        ]
        region_known = len(region) == 1 or normalized_key(str(row.get("region", ""))) in bundle_regions
        plan.bump("tracks", "match" if len(matches) == 1 else "conflict" if not region_known else "create")
    for row in bundle.get("crew_groups", []):
        plan.bump("crew_groups", "match" if _named(groups, str(row.get("name", ""))) else "create")
    for row in bundle.get("positions", []):
        group = _named(groups, str(row.get("crew_group", "")))
        matches = [
            item
            for item in positions
            if group
            and item.crew_group_id == group[0].id
            and normalized_key(item.name) == normalized_key(str(row.get("name", "")))
        ]
        group_known = len(group) == 1 or normalized_key(str(row.get("crew_group", ""))) in bundle_groups
        plan.bump("positions", "match" if matches else "conflict" if not group_known else "create")
    for row in bundle.get("people", []):
        referenced_regions = [str(row["home_region"])] if row.get("home_region") else []
        referenced_groups = [str(value) for value in row.get("crew_groups", [])]
        referenced_groups.extend(
            str(value["crew_group"]) for value in row.get("capabilities", [])
        )
        missing_region = any(
            not _named(regions, name) and normalized_key(name) not in bundle_regions
            for name in referenced_regions
        )
        missing_group = any(
            not _named(groups, name) and normalized_key(name) not in bundle_groups
            for name in referenced_groups
        )
        missing_position = False
        for capability in row.get("capabilities", []):
            group_name = str(capability["crew_group"])
            position_name = str(capability["position"])
            matched_groups = _named(groups, group_name)
            existing_position = any(
                matched_groups
                and item.crew_group_id == matched_groups[0].id
                and normalized_key(item.name) == normalized_key(position_name)
                for item in positions
            )
            bundled_position = (
                normalized_key(group_name),
                normalized_key(position_name),
            ) in bundle_positions
            missing_position = missing_position or not (existing_position or bundled_position)
        if missing_region or missing_group or missing_position:
            plan.bump("people", "conflict")
            plan.warnings.append(
                f"Unknown Region, Crew Group, or Position reference for: {row.get('display_name', '')}"
            )
            continue
        email = normalized_key(str(row.get("email", "")))
        matches = [item for item in people if email and normalized_key(item.email or "") == email]
        same_names = [
            item
            for item in people
            if normalized_key(item.display_name) == normalized_key(str(row.get("display_name", "")))
        ]
        result = (
            "match"
            if len(matches) == 1
            else "ambiguous"
            if len(matches) > 1 or (not email and same_names)
            else "create"
        )
        plan.bump("people", result)
        if result == "ambiguous":
            plan.warnings.append(f"Ambiguous person: {row.get('display_name', '')}")
    seen_external: set[tuple[str, str, str]] = set()
    for row in bundle.get("external_events", []):
        provider = str(row["provider"]).upper()
        duplicate_key = (
            provider,
            str(row.get("provider_event_id", "")),
            json.dumps(row, sort_keys=True),
        )
        if duplicate_key in seen_external:
            plan.bump("external_events", "duplicate")
            continue
        seen_external.add(duplicate_key)
        track = mapped_track(db, provider, str(row["track"]))
        if track is None:
            track_matches = [
                item
                for item in tracks
                if normalized_key(item.name) == normalized_key(str(row["track"]))
            ]
            if len(track_matches) == 1:
                track = track_matches[0]
        if track is None:
            plan.bump("external_events", "unresolved")
            continue
        existing = db.scalar(
            select(ExternalCalendarEvent).where(
                ExternalCalendarEvent.event_date == date.fromisoformat(str(row["date"])),
                ExternalCalendarEvent.track_id == track.id,
                ExternalCalendarEvent.discipline == row["discipline"],
                ExternalCalendarEvent.event_kind == row["event_kind"],
            )
        )
        if existing is None:
            plan.bump("external_events", "create")
            continue
        conflict = False
        enrich = False
        for fact_name in (
            "first_trial_time",
            "first_race_time",
            "last_race_time",
            "race_count",
            "status",
        ):
            incoming = dict(row.get("facts", {})).get(fact_name)
            if fact_name.endswith("_time") and incoming is not None and not isinstance(incoming, time):
                incoming = parse_time(str(incoming))
            current = getattr(existing, fact_name)
            enrich = enrich or (incoming is not None and current is None)
            conflict = conflict or (incoming is not None and current is not None and incoming != current)
        plan.bump("external_events", "conflict" if conflict else "enrich" if enrich else "match")
    plan.valid = not any(
        (section != "external_events" and values.get("conflict", 0))
        or values.get("ambiguous", 0)
        for section, values in plan.counts.items()
    )
    return plan


def apply_bundle(db: Session, bundle: dict[str, object], actor_user_id) -> ImportPlan:  # type: ignore[no-untyped-def]
    initial = preview_bundle(db, bundle)
    if not initial.valid:
        raise ValueError("Resolve import conflicts and ambiguous people before importing.")
    for row in bundle.get("regions", []):
        name = str(row["name"]).strip()
        if not db.scalar(select(Region).where(func.lower(Region.name) == name.casefold())):
            db.add(Region(name=name, lifecycle=str(row.get("lifecycle", "ACTIVE"))))
    db.flush()
    for row in bundle.get("crew_groups", []):
        name = str(row["name"]).strip()
        if not db.scalar(select(CrewGroup).where(func.lower(CrewGroup.name) == name.casefold())):
            db.add(CrewGroup(name=name, lifecycle=str(row.get("lifecycle", "ACTIVE"))))
    db.flush()
    for row in bundle.get("tracks", []):
        region = db.scalar(
            select(Region).where(func.lower(Region.name) == str(row["region"]).strip().casefold())
        )
        name = str(row["name"]).strip()
        existing = db.scalar(
            select(Track).where(Track.region_id == region.id, func.lower(Track.name) == name.casefold())
        )
        if not existing:
            db.add(
                Track(
                    name=name,
                    region_id=region.id,
                    palette_slot=allocate_palette_slot(db, region.id),
                    map_reference=str(row.get("map_reference", "")).strip() or None,
                    lifecycle=str(row.get("lifecycle", "ACTIVE")),
                )
            )
    db.flush()
    for row in bundle.get("positions", []):
        group = db.scalar(
            select(CrewGroup).where(func.lower(CrewGroup.name) == str(row["crew_group"]).strip().casefold())
        )
        name = str(row["name"]).strip()
        if not db.scalar(
            select(BasePosition).where(
                BasePosition.crew_group_id == group.id, func.lower(BasePosition.name) == name.casefold()
            )
        ):
            db.add(
                BasePosition(name=name, crew_group_id=group.id, lifecycle=str(row.get("lifecycle", "ACTIVE")))
            )
    db.flush()
    for row in bundle.get("people", []):
        email = str(row.get("email", "")).strip().casefold() or None
        person = db.scalar(select(Person).where(func.lower(Person.email) == email)) if email else None
        if not person:
            home = (
                db.scalar(
                    select(Region).where(
                        func.lower(Region.name) == str(row.get("home_region", "")).strip().casefold()
                    )
                )
                if row.get("home_region")
                else None
            )
            person = Person(
                display_name=str(row["display_name"]).strip(),
                email=email,
                home_region_id=home.id if home else None,
            )
            db.add(person)
            db.flush()
        for group_name in row.get("crew_groups", []):
            group = db.scalar(
                select(CrewGroup).where(func.lower(CrewGroup.name) == str(group_name).casefold())
            )
            if group and not db.get(PersonCrewGroup, (person.id, group.id)):
                db.add(PersonCrewGroup(person_id=person.id, crew_group_id=group.id))
        for capability in row.get("capabilities", []):
            group = db.scalar(
                select(CrewGroup).where(
                    func.lower(CrewGroup.name) == str(capability["crew_group"]).casefold()
                )
            )
            position = (
                db.scalar(
                    select(BasePosition).where(
                        BasePosition.crew_group_id == group.id,
                        func.lower(BasePosition.name) == str(capability["position"]).casefold(),
                    )
                )
                if group
                else None
            )
            signal = str(capability.get("signal", CapabilitySignal.MANAGER_ALLOW.value))
            if (
                position
                and signal in {item.value for item in CapabilitySignal}
                and not db.scalar(
                    select(PositionCapability).where(
                        PositionCapability.person_id == person.id,
                        PositionCapability.base_position_id == position.id,
                        PositionCapability.signal == signal,
                    )
                )
            ):
                db.add(
                    PositionCapability(
                        person_id=person.id,
                        base_position_id=position.id,
                        signal=signal,
                        changed_by_user_id=actor_user_id,
                    )
                )
    db.flush()
    for row in bundle.get("external_events", []):
        facts = dict(row.get("facts", {}))
        provider = str(row["provider"])
        source_track = str(row["track"])
        if not mapped_track(db, provider, source_track):
            track_matches = list(
                db.scalars(select(Track).where(func.lower(Track.name) == source_track.casefold()))
            )
            if len(track_matches) == 1:
                confirm_track_mapping(db, provider, source_track, track_matches[0].id, actor_user_id)
        reconcile_observation(
            db,
            ProviderObservation(
                provider=str(row["provider"]),
                provider_event_id=row.get("provider_event_id"),
                event_date=date.fromisoformat(str(row["date"])),
                source_track_name=str(row["track"]),
                discipline=str(row["discipline"]),
                event_kind=str(row["event_kind"]),
                facts=facts,
                raw_payload=dict(row.get("raw", row)),
            ),
        )
    return initial
