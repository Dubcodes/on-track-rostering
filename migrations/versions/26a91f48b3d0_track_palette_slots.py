"""Replace prototype track hex colours with stable per-region palette slots.

Revision ID: 26a91f48b3d0
Revises: 91ce2f784ab0
"""

import sqlalchemy as sa
from alembic import op

revision = "26a91f48b3d0"
down_revision = "91ce2f784ab0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""DO $$ BEGIN
        IF EXISTS (SELECT region_id FROM tracks WHERE lifecycle = 'ACTIVE'
                   GROUP BY region_id HAVING count(*) > 20) THEN
            RAISE EXCEPTION 'Track palette migration requires at most 20 active tracks per region';
        END IF;
    END $$""")
    op.add_column("tracks", sa.Column("palette_slot", sa.Integer(), nullable=True))
    op.execute("""WITH ranked AS (
        SELECT id, row_number() OVER (PARTITION BY region_id, lifecycle
          ORDER BY lower(name), name, id) AS slot FROM tracks
    ) UPDATE tracks SET palette_slot = ((ranked.slot - 1) % 20) + 1
      FROM ranked WHERE tracks.id = ranked.id""")
    op.alter_column("tracks", "palette_slot", nullable=False)
    op.create_check_constraint("track_palette_range", "tracks", "palette_slot BETWEEN 1 AND 20")
    op.create_index(
        "uq_track_active_palette",
        "tracks",
        ["region_id", "palette_slot"],
        unique=True,
        postgresql_where=sa.text("lifecycle = 'ACTIVE'"),
    )
    op.drop_constraint("valid_hex_colour", "tracks", type_="check")
    op.drop_column("tracks", "display_colour")
    # Only obsolete presentation is removed; historical business snapshots stay.
    op.drop_column("workday_revisions", "track_colour_snapshot")


def downgrade() -> None:
    # Prototype colour values cannot be recovered by a downgrade.
    op.add_column(
        "workday_revisions",
        sa.Column("track_colour_snapshot", sa.String(7), nullable=False, server_default="#667085"),
    )
    op.add_column(
        "tracks", sa.Column("display_colour", sa.String(7), nullable=False, server_default="#2E7D6A")
    )
    op.create_check_constraint(
        "valid_hex_colour", "tracks", "length(display_colour) = 7 AND substr(display_colour, 1, 1) = '#'"
    )
    op.drop_index("uq_track_active_palette", table_name="tracks")
    op.drop_constraint("track_palette_range", "tracks", type_="check")
    op.drop_column("tracks", "palette_slot")
