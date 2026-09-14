"""operational notices and richer notification preferences

Revision ID: 91ce2f784ab0
Revises: e72a19c5f40b
Create Date: 2026-09-14
"""

import sqlalchemy as sa
from alembic import op

revision = "91ce2f784ab0"
down_revision = "e72a19c5f40b"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for name, default in (
        ("notifications_enabled", True),
        ("important_changes_24h", True),
        ("weekly_digest", False),
        ("open_positions_digest", False),
        ("admin_alerts", True),
    ):
        op.add_column(
            "notification_preferences",
            sa.Column(
                name, sa.Boolean(), nullable=False, server_default=sa.true() if default else sa.false()
            ),
        )
    op.add_column(
        "notification_preferences",
        sa.Column("reminder_time", sa.Time(), nullable=False, server_default=sa.text("'19:00:00'")),
    )
    op.create_table(
        "operational_notices",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("scope", sa.String(length=12), nullable=False),
        sa.Column("region_id", sa.Uuid(), nullable=True),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("scope in ('GLOBAL', 'REGION')", name="ck_notice_scope"),
        sa.CheckConstraint(
            "(scope = 'GLOBAL' and region_id is null) or (scope = 'REGION' and region_id is not null)",
            name="ck_notice_scope_region",
        ),
        sa.CheckConstraint("expires_at > starts_at", name="ck_notice_window"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["region_id"], ["regions.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_operational_notices_region_id", "operational_notices", ["region_id"])
    op.create_index(
        "ix_operational_notices_created_by_user_id", "operational_notices", ["created_by_user_id"]
    )
    op.create_index("ix_notice_active_window", "operational_notices", ["starts_at", "expires_at"])


def downgrade() -> None:
    op.drop_table("operational_notices")
    for name in (
        "reminder_time",
        "admin_alerts",
        "open_positions_digest",
        "weekly_digest",
        "important_changes_24h",
        "notifications_enabled",
    ):
        op.drop_column("notification_preferences", name)
