from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.core.time import utcnow


class OperationalNotice(Base):
    __tablename__ = "operational_notices"
    __table_args__ = (
        CheckConstraint("scope in ('GLOBAL', 'REGION')", name="ck_notice_scope"),
        CheckConstraint(
            "(scope = 'GLOBAL' and region_id is null) or (scope = 'REGION' and region_id is not null)",
            name="ck_notice_scope_region",
        ),
        CheckConstraint("expires_at > starts_at", name="ck_notice_window"),
        Index("ix_notice_active_window", "starts_at", "expires_at"),
    )
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    scope: Mapped[str] = mapped_column(String(12))
    region_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("regions.id"), nullable=True, index=True)
    message: Mapped[str] = mapped_column(Text)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
