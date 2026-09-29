"""Add catalog-owned base position display order.

Revision ID: e48c7ad291f0
Revises: d37a4c8e91f2
"""

import sqlalchemy as sa
from alembic import op

revision = "e48c7ad291f0"
down_revision = "d37a4c8e91f2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("base_positions", sa.Column("display_order", sa.Integer(), nullable=True))
    op.execute(
        """
        WITH ordered AS (
            SELECT id, ROW_NUMBER() OVER (
                ORDER BY
                    CASE
                        WHEN lower(regexp_replace(name, '[^a-zA-Z0-9]', '', 'g')) LIKE 'side%' THEN 0
                        WHEN lower(regexp_replace(name, '[^a-zA-Z0-9]', '', 'g')) LIKE 'headon%' THEN 1
                        WHEN lower(regexp_replace(name, '[^a-zA-Z0-9]', '', 'g')) LIKE 'back%' THEN 2
                        WHEN lower(regexp_replace(name, '[^a-zA-Z0-9]', '', 'g')) LIKE 'turn%' THEN 3
                        WHEN lower(regexp_replace(name, '[^a-zA-Z0-9]', '', 'g')) LIKE 'rts%' THEN 4
                        WHEN lower(regexp_replace(name, '[^a-zA-Z0-9]', '', 'g')) LIKE 'gimbl%'
                          OR lower(regexp_replace(name, '[^a-zA-Z0-9]', '', 'g')) LIKE 'gimbal%' THEN 5
                        WHEN lower(regexp_replace(name, '[^a-zA-Z0-9]', '', 'g')) LIKE 'steady%'
                          OR lower(regexp_replace(name, '[^a-zA-Z0-9]', '', 'g')) LIKE 'steadicam%' THEN 6
                        WHEN lower(regexp_replace(name, '[^a-zA-Z0-9]', '', 'g')) LIKE 'director%' THEN 7
                        WHEN lower(regexp_replace(name, '[^a-zA-Z0-9]', '', 'g')) LIKE 'vt%' THEN 8
                        WHEN lower(regexp_replace(name, '[^a-zA-Z0-9]', '', 'g')) LIKE 'soundvt%' THEN 10
                        WHEN lower(regexp_replace(name, '[^a-zA-Z0-9]', '', 'g')) LIKE 'sound%' THEN 9
                        WHEN lower(regexp_replace(name, '[^a-zA-Z0-9]', '', 'g')) LIKE 'ccu%' THEN 11
                        WHEN lower(regexp_replace(name, '[^a-zA-Z0-9]', '', 'g')) LIKE 'eng%'
                          OR lower(regexp_replace(name, '[^a-zA-Z0-9]', '', 'g')) LIKE 'engineer%' THEN 12
                        ELSE 100
                    END,
                    lower(name), id
            ) * 10 AS new_order
            FROM base_positions
        )
        UPDATE base_positions
        SET display_order = ordered.new_order
        FROM ordered
        WHERE base_positions.id = ordered.id
        """
    )
    op.create_index("ix_base_positions_display_order", "base_positions", ["display_order"])


def downgrade() -> None:
    op.drop_index("ix_base_positions_display_order", table_name="base_positions")
    op.drop_column("base_positions", "display_order")
