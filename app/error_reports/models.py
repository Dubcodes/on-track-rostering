from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.core.time import utcnow


class ErrorReport(Base):
    __tablename__ = "error_reports"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    reporting_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    reporting_person_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("people.id"), nullable=True, index=True
    )
    description: Mapped[str] = mapped_column(Text)
    build_id: Mapped[str] = mapped_column(String(120), default="")
    page_path: Mapped[str] = mapped_column(String(500), default="")
    status: Mapped[str] = mapped_column(String(16), default="NEW", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reviewed_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
