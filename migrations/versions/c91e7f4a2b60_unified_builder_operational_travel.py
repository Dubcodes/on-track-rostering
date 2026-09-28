"""unified builder operational travel

Revision ID: c91e7f4a2b60
Revises: b84f1d2c6a10
"""

import sqlalchemy as sa
from alembic import op

revision = "c91e7f4a2b60"
down_revision = "b84f1d2c6a10"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("workdays", sa.Column("racing_discipline", sa.String(length=24), nullable=True))
    op.execute(
        """
        UPDATE workdays AS workday
        SET racing_discipline = event.discipline
        FROM external_calendar_events AS event
        WHERE workday.external_event_id = event.id
          AND workday.category IN ('RACE_DAY', 'TRIALS')
          AND event.discipline IN ('THOROUGHBRED', 'HARNESS')
        """
    )
    op.execute(
        """
        UPDATE workdays
        SET racing_discipline = 'THOROUGHBRED'
        WHERE category IN ('RACE_DAY', 'TRIALS')
          AND racing_discipline IS NULL
        """
    )
    op.add_column("workday_revisions", sa.Column("standard_travel_enabled", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("workday_revisions", sa.Column("travel_departure_time", sa.Time(), nullable=True))
    op.add_column("workday_revisions", sa.Column("travel_to_hotel_minutes", sa.Integer(), nullable=True))
    op.add_column("workday_revisions", sa.Column("default_hotel", sa.String(length=160), nullable=False, server_default=""))
    op.add_column("workday_revisions", sa.Column("hotel_to_track_minutes", sa.Integer(), nullable=True))
    op.add_column("workday_revisions", sa.Column("return_travel_minutes", sa.Integer(), nullable=True))
    op.add_column("workday_revisions", sa.Column("pack_up_minutes", sa.Integer(), nullable=False, server_default="60"))
    op.add_column("assignments", sa.Column("uses_standard_travel", sa.Boolean(), nullable=False, server_default=sa.true()))
    op.add_column("assignments", sa.Column("hotel_to_track_minutes_override", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("assignments", "hotel_to_track_minutes_override")
    op.drop_column("assignments", "uses_standard_travel")
    op.drop_column("workday_revisions", "pack_up_minutes")
    op.drop_column("workday_revisions", "return_travel_minutes")
    op.drop_column("workday_revisions", "hotel_to_track_minutes")
    op.drop_column("workday_revisions", "default_hotel")
    op.drop_column("workday_revisions", "travel_to_hotel_minutes")
    op.drop_column("workday_revisions", "travel_departure_time")
    op.drop_column("workday_revisions", "standard_travel_enabled")
    op.drop_column("workdays", "racing_discipline")
