"""operational parity fields

Revision ID: d7a4c9e2f610
Revises: c4f2a7d9e610
Create Date: 2026-10-14
"""

import sqlalchemy as sa
from alembic import op

revision = "d7a4c9e2f610"
down_revision = "c4f2a7d9e610"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("external_calendar_events", sa.Column("last_trial_time", sa.Time(), nullable=True))
    op.add_column("external_calendar_events", sa.Column("trial_count", sa.Integer(), nullable=True))
    op.add_column("tracks", sa.Column("address", sa.String(length=500), nullable=True))
    op.add_column("regions", sa.Column("base_name", sa.String(length=160), nullable=True))
    op.add_column("regions", sa.Column("base_address", sa.String(length=500), nullable=True))
    op.add_column(
        "vehicles", sa.Column("is_truck", sa.Boolean(), nullable=False, server_default=sa.false())
    )
    op.execute(
        sa.text("UPDATE vehicles SET is_truck = true WHERE lower(trim(name)) IN ('tender', 'ob')")
    )
    op.add_column(
        "workday_revisions",
        sa.Column("truck_start_offset_minutes", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "workday_revisions",
        sa.Column("track_address_snapshot", sa.String(length=500), nullable=False, server_default=""),
    )
    op.add_column(
        "workday_revisions",
        sa.Column("start_address_snapshot", sa.String(length=500), nullable=False, server_default=""),
    )
    op.add_column(
        "workday_revisions",
        sa.Column("finish_address_snapshot", sa.String(length=500), nullable=False, server_default=""),
    )
    op.add_column(
        "assignments", sa.Column("accommodation_address", sa.String(length=500), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("assignments", "accommodation_address")
    op.drop_column("workday_revisions", "finish_address_snapshot")
    op.drop_column("workday_revisions", "start_address_snapshot")
    op.drop_column("workday_revisions", "track_address_snapshot")
    op.drop_column("workday_revisions", "truck_start_offset_minutes")
    op.drop_column("vehicles", "is_truck")
    op.drop_column("regions", "base_address")
    op.drop_column("regions", "base_name")
    op.drop_column("tracks", "address")
    op.drop_column("external_calendar_events", "trial_count")
    op.drop_column("external_calendar_events", "last_trial_time")
