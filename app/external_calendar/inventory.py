from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.catalog.models import BasePosition, CrewGroup, Region, Track
from app.core.database import SessionLocal
from app.external_calendar.models import ExternalEventObservation, ExternalTrackMapping
from app.external_calendar.service import normalized_key, suggested_track
from app.identity.models import User


@dataclass(frozen=True)
class SourceInventoryItem:
    provider: str
    source_name: str
    source_key: str
    observation_count: int
    disciplines: tuple[str, ...]
    event_kinds: tuple[str, ...]
    first_event_date: date | None
    last_event_date: date | None
    venue_confidence: str
    mapping_state: str
    suggested_track_id: str | None = None
    suggested_track_name: str | None = None
    suggested_region_name: str | None = None
    current_track_name: str | None = None
    current_region_name: str | None = None

    @property
    def club_only(self) -> bool:
        return self.venue_confidence == "CLUB_ONLY"


def source_inventory(
    db: Session,
    *,
    provider: str | None = None,
    unmatched_only: bool = True,
) -> list[SourceInventoryItem]:
    statement = select(ExternalEventObservation)
    if provider:
        statement = statement.where(ExternalEventObservation.provider == provider.upper())
    if unmatched_only:
        statement = statement.where(ExternalEventObservation.mapping_state == "UNMATCHED")
    grouped: dict[tuple[str, str], list[ExternalEventObservation]] = {}
    for observation in db.scalars(statement):
        key = (observation.provider, normalized_key(observation.source_track_name))
        grouped.setdefault(key, []).append(observation)

    regions = {row.id: row for row in db.scalars(select(Region))}
    tracks = list(db.scalars(select(Track)))
    active_tracks = [
        row
        for row in tracks
        if row.lifecycle == "ACTIVE"
        and regions.get(row.region_id)
        and regions[row.region_id].lifecycle == "ACTIVE"
    ]
    tracks_by_id = {row.id: row for row in tracks}
    mappings = {
        (row.provider, row.external_track_key): row
        for row in db.scalars(select(ExternalTrackMapping))
    }

    items: list[SourceInventoryItem] = []
    for (item_provider, source_key), observations in grouped.items():
        event_dates: list[date] = []
        disciplines: set[str] = set()
        event_kinds: set[str] = set()
        confidences: set[str] = set()
        for observation in observations:
            facts = observation.parsed_facts or {}
            if facts.get("event_date"):
                try:
                    event_dates.append(date.fromisoformat(str(facts["event_date"])))
                except ValueError:
                    pass
            if facts.get("discipline"):
                disciplines.add(str(facts["discipline"]))
            if facts.get("event_kind"):
                event_kinds.add(str(facts["event_kind"]))
            if facts.get("venue_confidence"):
                confidences.add(str(facts["venue_confidence"]))
        confidence = (
            "CLUB_ONLY"
            if confidences == {"CLUB_ONLY"}
            else "EXPLICIT"
            if "EXPLICIT" in confidences
            else "UNKNOWN"
        )
        suggestion = (
            None
            if confidence == "CLUB_ONLY"
            else suggested_track(db, observations[0].source_track_name, tracks=active_tracks)
        )
        suggestion_region = regions.get(suggestion.region_id) if suggestion else None
        mapping = mappings.get((item_provider, source_key))
        mapped_track = tracks_by_id.get(mapping.track_id) if mapping else None
        mapped_region = regions.get(mapped_track.region_id) if mapped_track else None
        items.append(
            SourceInventoryItem(
                provider=item_provider,
                source_name=observations[0].source_track_name,
                source_key=source_key,
                observation_count=len(observations),
                disciplines=tuple(sorted(disciplines)),
                event_kinds=tuple(sorted(event_kinds)),
                first_event_date=min(event_dates) if event_dates else None,
                last_event_date=max(event_dates) if event_dates else None,
                venue_confidence=confidence,
                mapping_state="UNMATCHED" if unmatched_only else observations[0].mapping_state,
                suggested_track_id=str(suggestion.id) if suggestion else None,
                suggested_track_name=suggestion.name if suggestion else None,
                suggested_region_name=suggestion_region.name if suggestion_region else None,
                current_track_name=mapped_track.name if mapped_track else None,
                current_region_name=mapped_region.name if mapped_region else None,
            )
        )
    return sorted(items, key=lambda item: (item.provider, item.source_name.casefold()))


def inventory_template(db: Session, *, provider: str | None = None) -> dict[str, object]:
    return {
        "format": "ontrack-source-mapping-template",
        "version": "1",
        "instructions": (
            "Review each source identity, then copy confirmed decisions into the neutral "
            "external_track_mappings import section. Blank Track and Region values are intentional."
        ),
        "items": [
            {
                "provider": item.provider,
                "external_track_name": item.source_name,
                "track": "",
                "region": "",
                "metadata": {
                    "observation_count": item.observation_count,
                    "disciplines": list(item.disciplines),
                    "event_kinds": list(item.event_kinds),
                    "first_event_date": item.first_event_date.isoformat()
                    if item.first_event_date
                    else None,
                    "last_event_date": item.last_event_date.isoformat()
                    if item.last_event_date
                    else None,
                    "venue_confidence": item.venue_confidence,
                    "suggested_track": item.suggested_track_name,
                    "suggested_region": item.suggested_region_name,
                },
            }
            for item in source_inventory(db, provider=provider, unmatched_only=True)
        ],
    }


def confirmed_mapping_rows(db: Session) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for mapping in db.scalars(
        select(ExternalTrackMapping).order_by(
            ExternalTrackMapping.provider, ExternalTrackMapping.external_track_name
        )
    ):
        track = db.get(Track, mapping.track_id)
        region = db.get(Region, track.region_id) if track else None
        user = db.get(User, mapping.confirmed_by_user_id)
        rows.append(
            {
                "mapping": mapping,
                "track": track,
                "region": region,
                "confirmed_by": user.display_name if user else "Unknown administrator",
            }
        )
    return rows


def structural_master_data_bundle(db: Session) -> dict[str, object]:
    regions = list(db.scalars(select(Region).order_by(Region.name)))
    tracks = list(db.scalars(select(Track).order_by(Track.name)))
    groups = list(db.scalars(select(CrewGroup).order_by(CrewGroup.name)))
    positions = list(db.scalars(select(BasePosition).order_by(BasePosition.name)))
    region_by_id = {row.id: row for row in regions}
    group_by_id = {row.id: row for row in groups}
    mappings = confirmed_mapping_rows(db)
    return {
        "version": "1",
        "regions": [
            {"name": row.name, "lifecycle": row.lifecycle} for row in regions
        ],
        "tracks": [
            {
                "name": row.name,
                "region": region_by_id[row.region_id].name,
                "map_reference": row.map_reference,
                "lifecycle": row.lifecycle,
            }
            for row in tracks
            if row.region_id in region_by_id
        ],
        "crew_groups": [
            {"name": row.name, "lifecycle": row.lifecycle} for row in groups
        ],
        "positions": [
            {
                "name": row.name,
                "crew_group": group_by_id[row.crew_group_id].name,
                "lifecycle": row.lifecycle,
            }
            for row in positions
            if row.crew_group_id in group_by_id
        ],
        "external_track_mappings": [
            {
                "provider": row["mapping"].provider,
                "external_track_name": row["mapping"].external_track_name,
                "track": row["track"].name,
                "region": row["region"].name,
            }
            for row in mappings
            if row["track"] is not None and row["region"] is not None
        ],
    }


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Export grouped racing source identities.")
    parser.add_argument("--format", choices=("json",), default="json")
    parser.add_argument("--provider", choices=("LOVE_RACING", "HRNZ"))
    parser.add_argument("--unmatched-only", action="store_true")
    args = parser.parse_args(argv)
    with SessionLocal() as db:
        if args.unmatched_only:
            payload = inventory_template(db, provider=args.provider)
        else:
            payload = {
                "format": "ontrack-source-inventory",
                "version": "1",
                "items": [
                    asdict(item)
                    for item in source_inventory(
                        db, provider=args.provider, unmatched_only=False
                    )
                ],
            }
    print(json.dumps(payload, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(_main())


__all__ = [
    "SourceInventoryItem",
    "confirmed_mapping_rows",
    "inventory_template",
    "source_inventory",
    "structural_master_data_bundle",
]
