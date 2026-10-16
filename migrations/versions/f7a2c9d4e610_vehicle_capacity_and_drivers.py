"""Add vehicle capacity and explicit truck-driver qualification.

Revision ID: f7a2c9d4e610
Revises: e8b5d1f2a740
"""

import sqlalchemy as sa
from alembic import op

revision = "f7a2c9d4e610"
down_revision = "e8b5d1f2a740"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("vehicles", sa.Column("seat_capacity", sa.Integer(), nullable=True))
    op.create_check_constraint(
        "vehicle_seat_capacity_positive",
        "vehicles",
        "seat_capacity IS NULL OR seat_capacity > 0",
    )
    op.add_column(
        "people",
        sa.Column("truck_driver_qualified", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "assignments",
        sa.Column("is_vehicle_driver", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("assignments", "is_vehicle_driver")
    op.drop_column("people", "truck_driver_qualified")
    op.drop_constraint("vehicle_seat_capacity_positive", "vehicles", type_="check")
    op.drop_column("vehicles", "seat_capacity")
