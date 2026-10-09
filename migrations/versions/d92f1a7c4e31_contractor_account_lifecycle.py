"""contractor account lifecycle

Revision ID: d92f1a7c4e31
Revises: b6c2f0a9d741
Create Date: 2026-10-09
"""

import sqlalchemy as sa
from alembic import op

revision = "d92f1a7c4e31"
down_revision = "b6c2f0a9d741"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "system_settings",
        sa.Column(
            "contractor_inactivity_days",
            sa.Integer(),
            nullable=False,
            server_default="30",
        ),
    )
    op.add_column(
        "users",
        sa.Column("contractor_manual_extension_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column("contractor_access_expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        op.f("ix_users_contractor_access_expires_at"),
        "users",
        ["contractor_access_expires_at"],
        unique=False,
    )
    op.execute(
        sa.text(
            """
            UPDATE users
            SET contractor_manual_extension_at = CURRENT_TIMESTAMP,
                contractor_access_expires_at = CURRENT_TIMESTAMP + INTERVAL '30 days'
            WHERE EXISTS (
                SELECT 1
                FROM role_grants
                WHERE role_grants.user_id = users.id
                  AND role_grants.role = 'CONTRACTOR'
                  AND role_grants.status = 'ACTIVE'
            )
            """
        )
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_users_contractor_access_expires_at"), table_name="users")
    op.drop_column("users", "contractor_access_expires_at")
    op.drop_column("users", "contractor_manual_extension_at")
    op.drop_column("system_settings", "contractor_inactivity_days")
