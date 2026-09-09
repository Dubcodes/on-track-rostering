"""foundation correction state

Revision ID: c8e451d10a77
Revises: ab24e50d17c4
Create Date: 2026-09-08
"""

import sqlalchemy as sa
from alembic import op

revision = "c8e451d10a77"
down_revision = "ab24e50d17c4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "credential_admin_eligible",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.add_column(
        "notification_events", sa.Column("claim_token", sa.String(length=36), nullable=True)
    )
    op.add_column(
        "notification_events", sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_index(
        "ix_notification_events_claim_token", "notification_events", ["claim_token"], unique=False
    )
    op.alter_column(
        "notification_preferences", "one_hour_before", server_default=sa.true()
    )


def downgrade() -> None:
    op.alter_column(
        "notification_preferences", "one_hour_before", server_default=sa.false()
    )
    op.drop_index("ix_notification_events_claim_token", table_name="notification_events")
    op.drop_column("notification_events", "claimed_at")
    op.drop_column("notification_events", "claim_token")
    op.drop_column("users", "credential_admin_eligible")
