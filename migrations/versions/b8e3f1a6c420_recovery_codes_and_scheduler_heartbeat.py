"""recovery codes and scheduler heartbeat

Revision ID: b8e3f1a6c420
Revises: a7c4e9d2f610
Create Date: 2026-10-10
"""

import sqlalchemy as sa
from alembic import op

revision = "b8e3f1a6c420"
down_revision = "a7c4e9d2f610"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "recovery_codes",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("code_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"],
            name=op.f("fk_recovery_codes_user_id_users"), ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_recovery_codes")),
        sa.UniqueConstraint("code_hash", name=op.f("uq_recovery_codes_code_hash")),
    )
    op.create_index(
        op.f("ix_recovery_codes_user_id"), "recovery_codes", ["user_id"], unique=False
    )
    op.create_table(
        "scheduler_heartbeat",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("last_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_failed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_status", sa.String(length=16), nullable=False),
        sa.Column("last_error", sa.String(length=400), nullable=True),
        sa.Column("notifications_status", sa.String(length=16), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("id = 1", name=op.f("ck_scheduler_heartbeat_singleton")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_scheduler_heartbeat")),
    )


def downgrade() -> None:
    op.drop_table("scheduler_heartbeat")
    op.drop_index(op.f("ix_recovery_codes_user_id"), table_name="recovery_codes")
    op.drop_table("recovery_codes")
