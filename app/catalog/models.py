from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.core.enums import DeclinePolicy, Lifecycle
from app.core.time import utcnow


class Region(Base):
    __tablename__ = "regions"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    lifecycle: Mapped[str] = mapped_column(String(16), default=Lifecycle.ACTIVE.value)
    decline_policy: Mapped[str] = mapped_column(String(32), default=DeclinePolicy.MANAGER_REVIEW.value)
    lead_minutes_race_day: Mapped[int] = mapped_column(default=120)
    statutory_holiday_region: Mapped[str | None] = mapped_column(String(80), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Track(Base):
    __tablename__ = "tracks"
    __table_args__ = (
        UniqueConstraint("region_id", "name"),
        CheckConstraint(
            "length(display_colour) = 7 AND substr(display_colour, 1, 1) = '#'", name="valid_hex_colour"
        ),
    )
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    region_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("regions.id"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    display_colour: Mapped[str] = mapped_column(String(7), default="#2E7D6A")
    map_reference: Mapped[str | None] = mapped_column(String(500), nullable=True)
    lifecycle: Mapped[str] = mapped_column(String(16), default=Lifecycle.ACTIVE.value)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class CrewGroup(Base):
    __tablename__ = "crew_groups"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    lifecycle: Mapped[str] = mapped_column(String(16), default=Lifecycle.ACTIVE.value)


class PersonCrewGroup(Base):
    __tablename__ = "person_crew_groups"
    person_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("people.id", ondelete="CASCADE"), primary_key=True
    )
    crew_group_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("crew_groups.id", ondelete="CASCADE"), primary_key=True
    )


class BasePosition(Base):
    __tablename__ = "base_positions"
    __table_args__ = (UniqueConstraint("crew_group_id", "name"),)
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    crew_group_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("crew_groups.id"), nullable=True, index=True
    )
    name: Mapped[str] = mapped_column(String(100), index=True)
    lifecycle: Mapped[str] = mapped_column(String(16), default=Lifecycle.ACTIVE.value)


class Vehicle(Base):
    __tablename__ = "vehicles"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    category: Mapped[str] = mapped_column(String(80), default="Operational")
    model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    home_region_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("regions.id"), nullable=True)
    fuel_type: Mapped[str | None] = mapped_column(String(40), nullable=True)
    lifecycle: Mapped[str] = mapped_column(String(16), default=Lifecycle.ACTIVE.value)
