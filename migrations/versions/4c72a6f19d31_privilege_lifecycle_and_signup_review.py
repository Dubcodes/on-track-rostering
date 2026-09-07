"""privilege lifecycle, fresh authentication, and signup review

Revision ID: 4c72a6f19d31
Revises: 8b31d0a7c9e2
Create Date: 2026-09-08
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "4c72a6f19d31"
down_revision: str | Sequence[str] | None = "8b31d0a7c9e2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "role_grants",
        sa.Column("status", sa.String(length=16), server_default="ACTIVE", nullable=False),
    )
    op.add_column("role_grants", sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("role_grants", sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True))
    op.execute("UPDATE role_grants SET activated_at = granted_at")
    op.create_index(op.f("ix_role_grants_status"), "role_grants", ["status"], unique=False)
    op.drop_index("uq_role_grants_global", table_name="role_grants")
    op.drop_index("uq_role_grants_regional", table_name="role_grants")
    op.create_index(
        "uq_role_grants_global",
        "role_grants",
        ["user_id", "role"],
        unique=True,
        postgresql_where=sa.text("region_id IS NULL AND status <> 'REVOKED'"),
        sqlite_where=sa.text("region_id IS NULL AND status <> 'REVOKED'"),
    )
    op.create_index(
        "uq_role_grants_regional",
        "role_grants",
        ["user_id", "role", "region_id"],
        unique=True,
        postgresql_where=sa.text("region_id IS NOT NULL AND status <> 'REVOKED'"),
        sqlite_where=sa.text("region_id IS NOT NULL AND status <> 'REVOKED'"),
    )

    op.add_column(
        "trusted_devices",
        sa.Column("primary_authenticated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute("UPDATE trusted_devices SET primary_authenticated_at = created_at")
    op.alter_column("trusted_devices", "primary_authenticated_at", nullable=False)

    op.add_column(
        "signup_requests", sa.Column("reviewed_by_user_id", sa.Uuid(), nullable=True)
    )
    op.add_column("signup_requests", sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("signup_requests", sa.Column("approved_person_id", sa.Uuid(), nullable=True))
    op.add_column("signup_requests", sa.Column("invitation_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        op.f("fk_signup_requests_reviewed_by_user_id_users"),
        "signup_requests",
        "users",
        ["reviewed_by_user_id"],
        ["id"],
    )
    op.create_foreign_key(
        op.f("fk_signup_requests_approved_person_id_people"),
        "signup_requests",
        "people",
        ["approved_person_id"],
        ["id"],
    )
    op.create_foreign_key(
        op.f("fk_signup_requests_invitation_id_invitations"),
        "signup_requests",
        "invitations",
        ["invitation_id"],
        ["id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("fk_signup_requests_invitation_id_invitations"),
        "signup_requests",
        type_="foreignkey",
    )
    op.drop_constraint(
        op.f("fk_signup_requests_approved_person_id_people"),
        "signup_requests",
        type_="foreignkey",
    )
    op.drop_constraint(
        op.f("fk_signup_requests_reviewed_by_user_id_users"),
        "signup_requests",
        type_="foreignkey",
    )
    op.drop_column("signup_requests", "invitation_id")
    op.drop_column("signup_requests", "approved_person_id")
    op.drop_column("signup_requests", "reviewed_at")
    op.drop_column("signup_requests", "reviewed_by_user_id")
    op.drop_column("trusted_devices", "primary_authenticated_at")
    op.drop_index("uq_role_grants_regional", table_name="role_grants")
    op.drop_index("uq_role_grants_global", table_name="role_grants")
    op.create_index(
        "uq_role_grants_global",
        "role_grants",
        ["user_id", "role"],
        unique=True,
        postgresql_where=sa.text("region_id IS NULL"),
        sqlite_where=sa.text("region_id IS NULL"),
    )
    op.create_index(
        "uq_role_grants_regional",
        "role_grants",
        ["user_id", "role", "region_id"],
        unique=True,
        postgresql_where=sa.text("region_id IS NOT NULL"),
        sqlite_where=sa.text("region_id IS NOT NULL"),
    )
    op.drop_index(op.f("ix_role_grants_status"), table_name="role_grants")
    op.drop_column("role_grants", "revoked_at")
    op.drop_column("role_grants", "activated_at")
    op.drop_column("role_grants", "status")
