"""notification delivery and retry state

Revision ID: ab24e50d17c4
Revises: 7a91d36e5b20
Create Date: 2026-09-08
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "ab24e50d17c4"
down_revision: str | Sequence[str] | None = "7a91d36e5b20"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "notification_events",
        sa.Column("status", sa.String(length=20), server_default="PENDING", nullable=False),
    )
    op.add_column(
        "notification_events",
        sa.Column("available_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index(op.f("ix_notification_events_status"), "notification_events", ["status"])
    op.create_index(op.f("ix_notification_events_available_at"), "notification_events", ["available_at"])
    op.create_table(
        "notification_deliveries",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("event_key", sa.String(length=240), nullable=False),
        sa.Column("subscription_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_http_status", sa.Integer(), nullable=True),
        sa.Column("last_error", sa.String(length=160), nullable=False),
        sa.ForeignKeyConstraint(
            ["event_key"],
            ["notification_events.event_key"],
            name=op.f("fk_notification_deliveries_event_key_notification_events"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["subscription_id"],
            ["push_subscriptions.id"],
            name=op.f("fk_notification_deliveries_subscription_id_push_subscriptions"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_notification_deliveries")),
        sa.UniqueConstraint("event_key", "subscription_id", name=op.f("uq_notification_deliveries_event_key")),
    )
    op.create_index(op.f("ix_notification_deliveries_event_key"), "notification_deliveries", ["event_key"])
    op.create_index(op.f("ix_notification_deliveries_status"), "notification_deliveries", ["status"])
    op.create_index(
        op.f("ix_notification_deliveries_subscription_id"),
        "notification_deliveries",
        ["subscription_id"],
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_notification_deliveries_subscription_id"), table_name="notification_deliveries")
    op.drop_index(op.f("ix_notification_deliveries_status"), table_name="notification_deliveries")
    op.drop_index(op.f("ix_notification_deliveries_event_key"), table_name="notification_deliveries")
    op.drop_table("notification_deliveries")
    op.drop_index(op.f("ix_notification_events_available_at"), table_name="notification_events")
    op.drop_index(op.f("ix_notification_events_status"), table_name="notification_events")
    op.drop_column("notification_events", "available_at")
    op.drop_column("notification_events", "status")
