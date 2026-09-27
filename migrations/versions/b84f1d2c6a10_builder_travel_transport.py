"""builder travel and assignment transport

Revision ID: b84f1d2c6a10
Revises: 7e6a1c2d4f90
"""

import sqlalchemy as sa
from alembic import op

revision = "b84f1d2c6a10"
down_revision = "7e6a1c2d4f90"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "workday_revisions",
        sa.Column("start_origin", sa.String(length=160), nullable=False, server_default=""),
    )
    op.add_column(
        "workday_revisions",
        sa.Column("finish_destination", sa.String(length=160), nullable=False, server_default=""),
    )
    op.add_column(
        "assignments",
        sa.Column("transport_mode", sa.String(length=24), nullable=False, server_default="UNASSIGNED"),
    )
    op.add_column(
        "assignments",
        sa.Column("custom_transport_text", sa.String(length=160), nullable=False, server_default=""),
    )


def downgrade() -> None:
    op.drop_column("assignments", "custom_transport_text")
    op.drop_column("assignments", "transport_mode")
    op.drop_column("workday_revisions", "finish_destination")
    op.drop_column("workday_revisions", "start_origin")
