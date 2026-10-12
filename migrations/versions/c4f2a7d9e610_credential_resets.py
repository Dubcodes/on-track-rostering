"""credential resets

Revision ID: c4f2a7d9e610
Revises: b8e3f1a6c420
Create Date: 2026-10-12
"""

import sqlalchemy as sa
from alembic import op

revision = "c4f2a7d9e610"
down_revision = "b8e3f1a6c420"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "credential_resets",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"], ["users.id"],
            name=op.f("fk_credential_resets_created_by_user_id_users"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"],
            name=op.f("fk_credential_resets_user_id_users"), ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_credential_resets")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_credential_resets_token_hash")),
    )
    op.create_index(
        op.f("ix_credential_resets_user_id"), "credential_resets", ["user_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_credential_resets_user_id"), table_name="credential_resets")
    op.drop_table("credential_resets")
