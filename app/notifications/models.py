from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.core.time import utcnow


class PushSubscription(Base):
    __tablename__ = "push_subscriptions"
    __table_args__ = (UniqueConstraint("endpoint_hash"),)
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    endpoint_hash: Mapped[str] = mapped_column(String(64))
    encrypted_subscription: Mapped[dict[str, object]] = mapped_column(JSON)
    device_label: Mapped[str] = mapped_column(String(120), default="Browser")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class NotificationPreference(Base):
    __tablename__ = "notification_preferences"
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    roster_changes: Mapped[bool] = mapped_column(Boolean, default=True)
    night_before: Mapped[bool] = mapped_column(Boolean, default=True)
    two_days_before: Mapped[bool] = mapped_column(Boolean, default=False)
    one_hour_before: Mapped[bool] = mapped_column(Boolean, default=False)
    open_positions: Mapped[bool] = mapped_column(Boolean, default=True)


class NotificationEvent(Base):
    """Idempotent authoritative-event outbox; push delivery is a separate concern."""

    __tablename__ = "notification_events"
    event_key: Mapped[str] = mapped_column(String(240), primary_key=True)
    event_type: Mapped[str] = mapped_column(String(80), index=True)
    region_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("regions.id"), nullable=True, index=True)
    workday_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("workdays.id", ondelete="CASCADE"), nullable=True, index=True
    )
    assignment_slot_key: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    audience_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True
    )
    payload: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
