from __future__ import annotations

import uuid
from datetime import date, datetime, time
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Numeric,
    String,
    Text,
    Time,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.core.enums import AssignmentStatus, RevisionState, WorkdayCategory
from app.core.time import utcnow


class Operation(Base):
    __tablename__ = "operations"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(160))
    region_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("regions.id"), index=True)
    starts_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    ends_on: Mapped[date | None] = mapped_column(Date, nullable=True)


class Workday(Base):
    __tablename__ = "workdays"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    region_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("regions.id"), index=True)
    operation_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("operations.id"), nullable=True)
    category: Mapped[str] = mapped_column(String(32), default=WorkdayCategory.RACE_DAY.value)
    current_published_revision_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("workday_revisions.id", use_alter=True), nullable=True
    )
    current_draft_revision_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("workday_revisions.id", use_alter=True), nullable=True
    )
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    lock_version: Mapped[int] = mapped_column(default=1)


class WorkdayRevision(Base):
    __tablename__ = "workday_revisions"
    __table_args__ = (UniqueConstraint("workday_id", "revision_number"),)
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    workday_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workdays.id", ondelete="CASCADE"), index=True)
    revision_number: Mapped[int]
    state: Mapped[str] = mapped_column(String(16), default=RevisionState.DRAFT.value)
    based_on_revision_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("workday_revisions.id"), nullable=True
    )
    work_date: Mapped[date] = mapped_column(Date, index=True)
    track_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("tracks.id"), nullable=True)
    track_name_snapshot: Mapped[str] = mapped_column(String(120), default="To be confirmed")
    track_colour_snapshot: Mapped[str] = mapped_column(String(7), default="#667085")
    title: Mapped[str] = mapped_column(String(160), default="Race Day")
    start_time: Mapped[time | None] = mapped_column(Time, nullable=True)
    end_time: Mapped[time | None] = mapped_column(Time, nullable=True)
    on_track_time: Mapped[time | None] = mapped_column(Time, nullable=True)
    first_trial_time: Mapped[time | None] = mapped_column(Time, nullable=True)
    first_race_time: Mapped[time | None] = mapped_column(Time, nullable=True)
    last_race_time: Mapped[time | None] = mapped_column(Time, nullable=True)
    race_count: Mapped[int | None] = mapped_column(nullable=True)
    day_note: Mapped[str] = mapped_column(Text, default="")
    change_reason: Mapped[str] = mapped_column(String(500), default="")
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    published_by_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Assignment(Base):
    __tablename__ = "assignments"
    __table_args__ = (UniqueConstraint("revision_id", "slot_key"),)
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    revision_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workday_revisions.id", ondelete="CASCADE"), index=True
    )
    slot_key: Mapped[uuid.UUID] = mapped_column(Uuid, default=uuid.uuid4)
    base_position_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("base_positions.id"), nullable=True)
    slot_index: Mapped[int | None] = mapped_column(nullable=True)
    display_name_snapshot: Mapped[str] = mapped_column(String(120))
    person_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("people.id"), nullable=True, index=True)
    person_name_snapshot: Mapped[str | None] = mapped_column(String(120), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default=AssignmentStatus.TBC.value)
    start_time: Mapped[time | None] = mapped_column(Time, nullable=True)
    end_time: Mapped[time | None] = mapped_column(Time, nullable=True)
    note: Mapped[str] = mapped_column(Text, default="")
    note_private: Mapped[bool] = mapped_column(Boolean, default=True)
    vehicle_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("vehicles.id"), nullable=True)
    vehicle_name_snapshot: Mapped[str | None] = mapped_column(String(120), nullable=True)
    accommodation_name: Mapped[str | None] = mapped_column(String(160), nullable=True)


class OpenPositionApplication(Base):
    __tablename__ = "open_position_applications"
    __table_args__ = (UniqueConstraint("revision_id", "slot_key", "person_id"),)
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    revision_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workday_revisions.id", ondelete="CASCADE"), index=True
    )
    slot_key: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)
    person_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("people.id"), index=True)
    status: Mapped[str] = mapped_column(String(24), default="APPLIED")
    selected_draft_revision_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("workday_revisions.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class PositionCapability(Base):
    __tablename__ = "position_capabilities"
    __table_args__ = (UniqueConstraint("person_id", "base_position_id", "signal"),)
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    person_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("people.id", ondelete="CASCADE"), index=True)
    base_position_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("base_positions.id"), index=True)
    signal: Mapped[str] = mapped_column(String(24))
    source_revision_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("workday_revisions.id"), nullable=True
    )
    changed_by_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ProgrammeItem(Base):
    __tablename__ = "programme_items"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    revision_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workday_revisions.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(20))
    sequence: Mapped[int]
    operational_time: Mapped[time | None] = mapped_column(Time, nullable=True)
    source_time: Mapped[time | None] = mapped_column(Time, nullable=True)
    source_provider: Mapped[str | None] = mapped_column(String(80), nullable=True)
    source_retrieved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    manual_override: Mapped[bool] = mapped_column(Boolean, default=False)


class TravelLeg(Base):
    __tablename__ = "travel_legs"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    operation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("operations.id", ondelete="CASCADE"), index=True
    )
    travel_date: Mapped[date] = mapped_column(Date)
    origin: Mapped[str] = mapped_column(String(160))
    destination: Mapped[str] = mapped_column(String(160))
    starts_at: Mapped[time | None] = mapped_column(Time, nullable=True)
    ends_at: Mapped[time | None] = mapped_column(Time, nullable=True)
    vehicle_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("vehicles.id"), nullable=True)
    notes: Mapped[str] = mapped_column(Text, default="")


class AllowanceIndicator(Base):
    __tablename__ = "allowance_indicators"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    revision_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workday_revisions.id", ondelete="CASCADE"), index=True
    )
    person_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("people.id"), index=True)
    kind: Mapped[str] = mapped_column(String(40))
    entitlement_hours: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    explanation: Mapped[str] = mapped_column(String(240))
