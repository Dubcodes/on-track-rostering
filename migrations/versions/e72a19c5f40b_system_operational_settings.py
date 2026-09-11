"""persist global operational settings

Revision ID: e72a19c5f40b
Revises: f14b3928a6cd
Create Date: 2026-09-12
"""

import sqlalchemy as sa
from alembic import op

revision = "e72a19c5f40b"
down_revision = "f14b3928a6cd"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "system_settings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column(
            "public_signup_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
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
        sa.text(
            "INSERT INTO system_settings (id, public_signup_enabled) VALUES (1, false)"
        )
    )


def downgrade() -> None:
    op.drop_table("system_settings")
