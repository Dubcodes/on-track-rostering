"""Add one-time fresh-authentication action intents.

Revision ID: a3d8c1f4e720
Revises: f7a2c9d4e610
"""

import sqlalchemy as sa
from alembic import op

revision = "a3d8c1f4e720"
down_revision = "f7a2c9d4e610"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "fresh_action_intents",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("device_id", sa.Uuid(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("action_path", sa.String(length=300), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["device_id"], ["trusted_devices.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash"),
    )
    op.create_index("ix_fresh_action_intents_device_id", "fresh_action_intents", ["device_id"])
    op.create_index("ix_fresh_action_intents_expires_at", "fresh_action_intents", ["expires_at"])
    op.create_index("ix_fresh_action_intents_user_id", "fresh_action_intents", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_fresh_action_intents_user_id", table_name="fresh_action_intents")
    op.drop_index("ix_fresh_action_intents_expires_at", table_name="fresh_action_intents")
    op.drop_index("ix_fresh_action_intents_device_id", table_name="fresh_action_intents")
    op.drop_table("fresh_action_intents")
