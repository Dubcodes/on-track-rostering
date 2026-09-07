from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Index, String, Uuid, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.core.enums import Lifecycle, Role
from app.core.time import utcnow


class User(Base):
    __tablename__ = "users"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(120))
    credential_hash: Mapped[str] = mapped_column(String(512))
    credential_kind: Mapped[str] = mapped_column(String(16), default="pin")
    status: Mapped[str] = mapped_column(String(20), default="ACTIVE")
    theme: Mapped[str] = mapped_column(String(24), default="trackside")
    auth_epoch: Mapped[int] = mapped_column(default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    grants: Mapped[list[RoleGrant]] = relationship(
        back_populates="user", cascade="all, delete-orphan", foreign_keys="RoleGrant.user_id"
    )
    person_link: Mapped[UserPersonLink | None] = relationship(back_populates="user", uselist=False)


class Person(Base):
    __tablename__ = "people"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    display_name: Mapped[str] = mapped_column(String(120), index=True)
    email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    home_region_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("regions.id"), nullable=True, index=True
    )
    lifecycle: Mapped[str] = mapped_column(String(16), default=Lifecycle.ACTIVE.value)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    user_link: Mapped[UserPersonLink | None] = relationship(back_populates="person", uselist=False)


class UserPersonLink(Base):
    __tablename__ = "user_person_links"
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    person_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("people.id"), unique=True, index=True)
    linked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    user: Mapped[User] = relationship(back_populates="person_link")
    person: Mapped[Person] = relationship(back_populates="user_link")


class RoleGrant(Base):
    __tablename__ = "role_grants"
    __table_args__ = (
        CheckConstraint(
            "(role = 'ADMIN' AND region_id IS NULL) OR role <> 'ADMIN'",
            name="admin_global_scope",
        ),
        Index(
            "uq_role_grants_global",
            "user_id",
            "role",
            unique=True,
            postgresql_where=text("region_id IS NULL"),
            sqlite_where=text("region_id IS NULL"),
        ),
        Index(
            "uq_role_grants_regional",
            "user_id",
            "role",
            "region_id",
            unique=True,
            postgresql_where=text("region_id IS NOT NULL"),
            sqlite_where=text("region_id IS NOT NULL"),
        ),
    )
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(24))
    region_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("regions.id"), nullable=True, index=True)
    granted_by_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    granted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    user: Mapped[User] = relationship(back_populates="grants", foreign_keys=[user_id])


class TrustedDevice(Base):
    __tablename__ = "trusted_devices"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    csrf_hash: Mapped[str] = mapped_column(String(64))
    label: Mapped[str] = mapped_column(String(120), default="Browser")
    auth_epoch: Mapped[int]
    elevated: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class LoginThrottle(Base):
    __tablename__ = "login_throttles"
    key_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    failure_count: Mapped[int] = mapped_column(default=0)
    window_started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    blocked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Invitation(Base):
    __tablename__ = "invitations"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    email: Mapped[str] = mapped_column(String(320), index=True)
    display_name: Mapped[str] = mapped_column(String(120))
    person_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("people.id"), nullable=True)
    role: Mapped[str] = mapped_column(String(24), default=Role.EMPLOYEE.value)
    region_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("regions.id"), nullable=True)
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    activated_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)


class SignupRequest(Base):
    __tablename__ = "signup_requests"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(320), index=True)
    display_name: Mapped[str] = mapped_column(String(120))
    requested_region_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("regions.id"), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="PENDING")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PasskeyCredential(Base):
    __tablename__ = "passkey_credentials"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    credential_id: Mapped[bytes] = mapped_column(unique=True)
    public_key: Mapped[bytes]
    sign_count: Mapped[int] = mapped_column(default=0)
    transports: Mapped[str] = mapped_column(String(200), default="")
    label: Mapped[str] = mapped_column(String(120), default="Passkey")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


Index("ix_role_grants_user_region_role", RoleGrant.user_id, RoleGrant.region_id, RoleGrant.role)
