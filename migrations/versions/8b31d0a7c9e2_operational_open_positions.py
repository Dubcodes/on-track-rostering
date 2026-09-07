"""operational open positions and notification outbox

Revision ID: 8b31d0a7c9e2
Revises: d3f5ba5f0d58
Create Date: 2026-09-08
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "8b31d0a7c9e2"
down_revision: str | Sequence[str] | None = "d3f5ba5f0d58"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "open_position_applications",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("revision_id", sa.Uuid(), nullable=False),
        sa.Column("slot_key", sa.Uuid(), nullable=False),
        sa.Column("person_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("selected_draft_revision_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["person_id"],
            ["people.id"],
            name=op.f("fk_open_position_applications_person_id_people"),
        ),
        sa.ForeignKeyConstraint(
            ["revision_id"],
            ["workday_revisions.id"],
            name=op.f("fk_open_position_applications_revision_id_workday_revisions"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["selected_draft_revision_id"],
            ["workday_revisions.id"],
            name=op.f(
                "fk_open_position_applications_selected_draft_revision_id_workday_revisions"
            ),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_open_position_applications")),
        sa.UniqueConstraint(
            "revision_id",
            "slot_key",
            "person_id",
            name=op.f("uq_open_position_applications_revision_id"),
        ),
    )
    op.create_index(
        op.f("ix_open_position_applications_person_id"),
        "open_position_applications",
        ["person_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_open_position_applications_revision_id"),
        "open_position_applications",
        ["revision_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_open_position_applications_slot_key"),
        "open_position_applications",
        ["slot_key"],
        unique=False,
    )
    op.create_table(
        "notification_events",
        sa.Column("event_key", sa.String(length=240), nullable=False),
        sa.Column("event_type", sa.String(length=80), nullable=False),
        sa.Column("region_id", sa.Uuid(), nullable=True),
        sa.Column("workday_id", sa.Uuid(), nullable=True),
        sa.Column("assignment_slot_key", sa.Uuid(), nullable=True),
        sa.Column("audience_user_id", sa.Uuid(), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["audience_user_id"],
            ["users.id"],
            name=op.f("fk_notification_events_audience_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["region_id"], ["regions.id"], name=op.f("fk_notification_events_region_id_regions")
        ),
        sa.ForeignKeyConstraint(
            ["workday_id"],
            ["workdays.id"],
            name=op.f("fk_notification_events_workday_id_workdays"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("event_key", name=op.f("pk_notification_events")),
    )
    op.create_index(
        op.f("ix_notification_events_audience_user_id"),
        "notification_events",
        ["audience_user_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_notification_events_event_type"),
        "notification_events",
        ["event_type"],
        unique=False,
    )
    op.create_index(
        op.f("ix_notification_events_region_id"),
        "notification_events",
        ["region_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_notification_events_workday_id"),
        "notification_events",
        ["workday_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_notification_events_workday_id"), table_name="notification_events")
    op.drop_index(op.f("ix_notification_events_region_id"), table_name="notification_events")
    op.drop_index(op.f("ix_notification_events_event_type"), table_name="notification_events")
    op.drop_index(op.f("ix_notification_events_audience_user_id"), table_name="notification_events")
    op.drop_table("notification_events")
    op.drop_index(
        op.f("ix_open_position_applications_slot_key"),
        table_name="open_position_applications",
    )
    op.drop_index(
        op.f("ix_open_position_applications_revision_id"),
        table_name="open_position_applications",
    )
    op.drop_index(
        op.f("ix_open_position_applications_person_id"),
        table_name="open_position_applications",
    )
    op.drop_table("open_position_applications")
