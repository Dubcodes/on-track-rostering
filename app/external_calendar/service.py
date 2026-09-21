from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import date, datetime
from difflib import SequenceMatcher

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.policy import Actor, require_manage_region
from app.catalog.models import Track
from app.core.time import parse_time, utcnow
from app.external_calendar.models import ExternalCalendarEvent, ExternalEventObservation, ExternalTrackMapping
from app.rostering.models import Workday, WorkdayRevision
from app.rostering.service import create_workday

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


def suggested_track(
    db: Session, source_name: str, *, tracks: list[Track] | None = None
) -> Track | None:
    """Return a display-only suggestion; it never creates an authoritative mapping."""
    source = normalized_key(source_name)
    if tracks is None:
        tracks = list(db.scalars(select(Track).where(Track.lifecycle == "ACTIVE")))
    exact = [track for track in tracks if normalized_key(track.name) == source]
    if len(exact) == 1:
        return exact[0]
    ranked = sorted(
        ((SequenceMatcher(None, source, normalized_key(track.name)).ratio(), track) for track in tracks),
        key=lambda item: item[0],
        reverse=True,
    )
    if len(ranked) == 1 or (ranked and ranked[0][0] >= 0.90 and ranked[0][0] - ranked[1][0] >= 0.08):
        return ranked[0][1]
    return None


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
    identity_conflict = False
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
    elif event is not None and (
        event.event_date != value.event_date or (track is not None and event.track_id != track.id)
    ):
        # Stable provider identity proves continuity, but date/Track changes remain
        # review evidence and never move an operational Workday silently.
        identity_conflict = True
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
        conflict = identity_conflict
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
                # Diagnostics expose structure, not arbitrary provider values that may contain
                # credentials or unrelated personal data.
                "raw_keys": sorted((row.raw_payload or {}).keys()),
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
        if mapping.track_id != track_id:
            raise ValueError(
                "This source identity is already mapped to another Track; remapping requires a separate reviewed workflow."
            )
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
    pending = list(
        db.scalars(
            select(ExternalEventObservation).where(
                ExternalEventObservation.provider == provider,
                ExternalEventObservation.event_id.is_(None),
            )
        )
    )
    for observation in pending:
        if normalized_key(observation.source_track_name) != key:
            continue
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
        enriched = False
        conflict = False
        for field in CANONICAL_FIELDS:
            raw = facts.get(field)
            incoming = parse_time(str(raw)) if raw and field.endswith("_time") else raw
            if incoming is not None and getattr(event, field) is None:
                setattr(event, field, incoming)
                enriched = True
            elif incoming is not None and incoming != getattr(event, field):
                conflict = True
            if incoming is not None and incoming == getattr(event, field):
                provenance[field] = sorted(set(provenance.get(field, [])) | {provider})
        event.field_provenance = provenance
        if enriched:
            observation.reconciliation_state = "ENRICHED"
        if conflict:
            observation.reconciliation_state = "CONFLICT"
    return mapping


def adopt_external_event(
    db: Session,
    event_id: uuid.UUID,
    actor: Actor,
) -> tuple[Workday, bool]:
    """Create and link one private Workday draft inside the caller's transaction."""
    event = db.scalar(
        select(ExternalCalendarEvent)
        .where(ExternalCalendarEvent.id == event_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if event is None or event.track_id is None:
        raise ValueError("Map this event to a Track first.")
    track = db.get(Track, event.track_id)
    if track is None or track.lifecycle != "ACTIVE":
        raise ValueError("The mapped Track is not active.")
    require_manage_region(actor, track.region_id)
    existing = db.scalar(select(Workday).where(Workday.external_event_id == event.id))
    if existing:
        return existing, False
    workday = create_workday(
        db,
        region_id=track.region_id,
        category="RACE_DAY" if event.event_kind == "RACE" else "TRIALS",
        work_date=event.event_date,
        track_id=track.id,
        title="Race Day" if event.event_kind == "RACE" else "Trials",
        actor_user_id=actor.user_id,
        commit=False,
    )
    workday.external_event_id = event.id
    draft = db.get(WorkdayRevision, workday.current_draft_revision_id)
    assert draft is not None
    draft.first_trial_time = event.first_trial_time
    draft.first_race_time = event.first_race_time
    draft.last_race_time = event.last_race_time
    draft.race_count = event.race_count
    db.flush()
    return workday, True
