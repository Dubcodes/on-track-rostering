"""persist global system branding

Revision ID: f14b3928a6cd
Revises: c8e451d10a77
Create Date: 2026-09-10
"""

import sqlalchemy as sa
from alembic import op

revision = "f14b3928a6cd"
down_revision = "c8e451d10a77"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "system_branding",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column(
            "product_name",
            sa.String(length=80),
            nullable=False,
            server_default="On Track",
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("updated_by_user_id", sa.Uuid(), nullable=True),
        sa.CheckConstraint("id = 1", name="singleton"),
        sa.ForeignKeyConstraint(["updated_by_user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.execute(
        sa.text("INSERT INTO system_branding (id, product_name) VALUES (1, 'On Track')")
    )


def downgrade() -> None:
    op.drop_table("system_branding")
