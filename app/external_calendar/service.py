from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.catalog.models import Track
from app.core.time import parse_time, utcnow
from app.external_calendar.models import ExternalCalendarEvent, ExternalEventObservation, ExternalTrackMapping

DISCIPLINES = {"THOROUGHBRED", "HARNESS"}
EVENT_KINDS = {"RACE", "TRIAL"}
CANONICAL_FIELDS = ("first_trial_time", "first_race_time", "last_race_time", "race_count", "status")


@dataclass(frozen=True)
class ProviderObservation:
    provider: str
    provider_event_id: str | None
    event_date: date
    source_track_name: str
    discipline: str
    event_kind: str
    facts: dict[str, object]
    raw_payload: dict[str, object]
    retrieved_at: datetime | None = None


def normalized_key(value: str) -> str:
    return " ".join(value.strip().casefold().split())


def _payload_hash(observation: ProviderObservation) -> str:
    body = json.dumps(observation.raw_payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(body.encode()).hexdigest()


def mapped_track(db: Session, provider: str, name: str) -> Track | None:
    mapping = db.scalar(
        select(ExternalTrackMapping).where(
            ExternalTrackMapping.provider == provider.upper(),
            ExternalTrackMapping.external_track_key == normalized_key(name),
        )
    )
    return db.get(Track, mapping.track_id) if mapping else None


def reconcile_observation(
    db: Session, value: ProviderObservation
) -> tuple[ExternalCalendarEvent | None, str]:
    provider = value.provider.strip().upper()
    if value.discipline not in DISCIPLINES or value.event_kind not in EVENT_KINDS:
        raise ValueError("Invalid external event discipline or kind.")
    digest = _payload_hash(value)
    existing_observation = db.scalar(
        select(ExternalEventObservation).where(
            ExternalEventObservation.provider == provider,
            ExternalEventObservation.provider_event_id == value.provider_event_id,
            ExternalEventObservation.payload_hash == digest,
        )
    )
    if existing_observation:
        return db.get(ExternalCalendarEvent, existing_observation.event_id), "DUPLICATE"
    track = mapped_track(db, provider, value.source_track_name)
    event = None
    if value.provider_event_id:
        event = db.scalar(
            select(ExternalCalendarEvent)
            .join(ExternalEventObservation)
            .where(
                ExternalEventObservation.provider == provider,
                ExternalEventObservation.provider_event_id == value.provider_event_id,
            )
            .limit(1)
        )
    if event is None and track:
        event = db.scalar(
            select(ExternalCalendarEvent).where(
                ExternalCalendarEvent.event_date == value.event_date,
                ExternalCalendarEvent.track_id == track.id,
                ExternalCalendarEvent.discipline == value.discipline,
                ExternalCalendarEvent.event_kind == value.event_kind,
            )
        )
    state = "CREATED"
    if event is None and track:
        event = ExternalCalendarEvent(
            event_date=value.event_date,
            track_id=track.id,
            external_track_name=value.source_track_name,
            discipline=value.discipline,
            event_kind=value.event_kind,
            presentation_provider=provider,
        )
        db.add(event)
        db.flush()
    elif event is not None:
        state = "MATCHED"
    parsed = {
        **value.facts,
        "event_date": value.event_date.isoformat(),
        "discipline": value.discipline,
        "event_kind": value.event_kind,
    }
    observation = ExternalEventObservation(
        event_id=event.id if event else None,
        provider=provider,
        provider_event_id=value.provider_event_id,
        retrieved_at=value.retrieved_at or utcnow(),
        payload_hash=digest,
        source_track_name=value.source_track_name,
        parsed_facts=parsed,
        raw_payload=value.raw_payload,
        mapping_state="MAPPED" if track else "UNMATCHED",
        reconciliation_state=state if event else "REVIEW",
    )
    db.add(observation)
    if event:
        provenance = {key: list(sources) for key, sources in (event.field_provenance or {}).items()}
        enriched = False
        conflict = False
        for field in CANONICAL_FIELDS:
            raw = value.facts.get(field)
            incoming = parse_time(str(raw)) if raw and field.endswith("_time") else raw
            current = getattr(event, field)
            if incoming is not None and current is None:
                setattr(event, field, incoming)
                enriched = state != "CREATED"
            elif incoming is not None and current is not None and incoming != current:
                conflict = True
            if incoming is not None and incoming == getattr(event, field):
                provenance[field] = sorted(set(provenance.get(field, [])) | {provider})
        event.field_provenance = provenance
        if enriched:
            state = observation.reconciliation_state = "ENRICHED"
        if conflict:
            state = observation.reconciliation_state = "CONFLICT"
    return event, state


def event_evidence(db: Session, event: ExternalCalendarEvent) -> dict[str, object]:
    observations = list(
        db.scalars(
            select(ExternalEventObservation)
            .where(ExternalEventObservation.event_id == event.id)
            .order_by(ExternalEventObservation.retrieved_at)
        )
    )
    return {
        "canonical": {field: getattr(event, field) for field in CANONICAL_FIELDS},
        "provenance": event.field_provenance,
        "observations": [
            {
                "provider": row.provider,
                "retrieved_at": row.retrieved_at,
                "facts": row.parsed_facts,
                "raw": row.raw_payload,
            }
            for row in observations
        ],
    }


def confirm_track_mapping(
    db: Session, provider: str, external_name: str, track_id: uuid.UUID, actor_user_id: uuid.UUID
) -> ExternalTrackMapping:
    provider, key = provider.upper(), normalized_key(external_name)
    mapping = db.scalar(
        select(ExternalTrackMapping).where(
            ExternalTrackMapping.provider == provider, ExternalTrackMapping.external_track_key == key
        )
    )
    if mapping:
        mapping.track_id = track_id
    else:
        mapping = ExternalTrackMapping(
            provider=provider,
            external_track_key=key,
            external_track_name=external_name,
            track_id=track_id,
            confirmed_by_user_id=actor_user_id,
        )
        db.add(mapping)
    db.flush()
    for observation in db.scalars(
        select(ExternalEventObservation).where(
            ExternalEventObservation.provider == provider,
            ExternalEventObservation.event_id.is_(None),
            ExternalEventObservation.source_track_name == external_name,
        )
    ):
        facts = observation.parsed_facts
        event_date = date.fromisoformat(str(facts["event_date"]))
        event = db.scalar(
            select(ExternalCalendarEvent).where(
                ExternalCalendarEvent.event_date == event_date,
                ExternalCalendarEvent.track_id == track_id,
                ExternalCalendarEvent.discipline == facts["discipline"],
                ExternalCalendarEvent.event_kind == facts["event_kind"],
            )
        )
        if not event:
            event = ExternalCalendarEvent(
                event_date=event_date,
                track_id=track_id,
                external_track_name=external_name,
                discipline=str(facts["discipline"]),
                event_kind=str(facts["event_kind"]),
                presentation_provider=provider,
            )
            db.add(event)
            db.flush()
        observation.event_id = event.id
        observation.mapping_state = "MAPPED"
        observation.reconciliation_state = "MATCHED"
        provenance = dict(event.field_provenance or {})
        for field in CANONICAL_FIELDS:
            raw = facts.get(field)
            incoming = parse_time(str(raw)) if raw and field.endswith("_time") else raw
            if incoming is not None and getattr(event, field) is None:
                setattr(event, field, incoming)
            if incoming is not None and incoming == getattr(event, field):
                provenance[field] = sorted(set(provenance.get(field, [])) | {provider})
        event.field_provenance = provenance
    return mapping
