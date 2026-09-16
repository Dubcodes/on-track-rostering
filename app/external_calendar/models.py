from __future__ import annotations

import uuid
from datetime import date, datetime, time

from sqlalchemy import JSON, Boolean, Date, DateTime, ForeignKey, String, Time, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.core.time import utcnow


class ExternalCalendarEvent(Base):
    __tablename__ = "external_calendar_events"
    __table_args__ = (UniqueConstraint("event_date", "track_id", "discipline", "event_kind"),)
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    event_date: Mapped[date] = mapped_column(Date, index=True)
    track_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("tracks.id"), nullable=True, index=True)
    external_track_name: Mapped[str | None] = mapped_column(String(160), nullable=True)
    discipline: Mapped[str] = mapped_column(String(24))
    event_kind: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(24), default="SCHEDULED")
    first_trial_time: Mapped[time | None] = mapped_column(Time, nullable=True)
    first_race_time: Mapped[time | None] = mapped_column(Time, nullable=True)
    last_race_time: Mapped[time | None] = mapped_column(Time, nullable=True)
    race_count: Mapped[int | None] = mapped_column(nullable=True)
    field_provenance: Mapped[dict[str, list[str]]] = mapped_column(JSON, default=dict)
    presentation_provider: Mapped[str | None] = mapped_column(String(40), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class ExternalEventObservation(Base):
    __tablename__ = "external_event_observations"
    __table_args__ = (UniqueConstraint("provider", "provider_event_id", "payload_hash"),)
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    event_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("external_calendar_events.id"), nullable=True, index=True
    )
    provider: Mapped[str] = mapped_column(String(40), index=True)
    provider_event_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    payload_hash: Mapped[str] = mapped_column(String(64))
    source_track_name: Mapped[str] = mapped_column(String(160))
    parsed_facts: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    raw_payload: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    mapping_state: Mapped[str] = mapped_column(String(24), default="UNMATCHED")
    reconciliation_state: Mapped[str] = mapped_column(String(24), default="REVIEW")


class ExternalTrackMapping(Base):
    __tablename__ = "external_track_mappings"
    __table_args__ = (UniqueConstraint("provider", "external_track_key"),)
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    provider: Mapped[str] = mapped_column(String(40))
    external_track_key: Mapped[str] = mapped_column(String(160))
    external_track_name: Mapped[str] = mapped_column(String(160))
    track_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tracks.id"), index=True)
    confirmed_by_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    confirmed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ExternalProviderState(Base):
    __tablename__ = "external_provider_states"
    provider: Mapped[str] = mapped_column(String(40), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(40), default="NOT_CONFIGURED")
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    observations_found: Mapped[int] = mapped_column(default=0)
    events_created: Mapped[int] = mapped_column(default=0)
    events_enriched: Mapped[int] = mapped_column(default=0)
    warning_count: Mapped[int] = mapped_column(default=0)


class CalendarDisplayPreference(Base):
    __tablename__ = "calendar_display_preferences"
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    show_thoroughbred: Mapped[bool] = mapped_column(Boolean, default=True)
    show_harness: Mapped[bool] = mapped_column(Boolean, default=True)
    show_trials: Mapped[bool] = mapped_column(Boolean, default=False)
    minimal_external_detail: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
