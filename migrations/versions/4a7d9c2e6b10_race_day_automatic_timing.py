"""Race Day automatic timing and Track travel defaults.

Revision ID: 4a7d9c2e6b10
Revises: f59d8b7c1a20
"""

import sqlalchemy as sa
from alembic import op

revision = "4a7d9c2e6b10"
down_revision = "f59d8b7c1a20"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tracks", sa.Column("default_travel_minutes", sa.Integer(), nullable=True))
    op.create_check_constraint(
        "ck_tracks_default_travel_minutes_range",
        "tracks",
        "default_travel_minutes BETWEEN 0 AND 1440",
    )
    op.add_column(
        "workday_revisions",
        sa.Column("start_time_is_override", sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    op.add_column(
        "workday_revisions",
        sa.Column("on_track_time_is_override", sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    op.add_column(
        "workday_revisions", sa.Column("track_travel_minutes", sa.Integer(), nullable=True)
    )
    op.add_column(
        "workday_revisions",
        sa.Column(
            "track_travel_minutes_is_override",
            sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
        ),
    )
    op.create_check_constraint(
        "ck_workday_revisions_track_travel_minutes_range",
        "workday_revisions",
        "track_travel_minutes BETWEEN 0 AND 1440",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_workday_revisions_track_travel_minutes_range",
        "workday_revisions",
        type_="check",
    )
    op.drop_column("workday_revisions", "track_travel_minutes_is_override")
    op.drop_column("workday_revisions", "track_travel_minutes")
    op.drop_column("workday_revisions", "on_track_time_is_override")
    op.drop_column("workday_revisions", "start_time_is_override")
    op.drop_constraint("ck_tracks_default_travel_minutes_range", "tracks", type_="check")
    op.drop_column("tracks", "default_travel_minutes")
