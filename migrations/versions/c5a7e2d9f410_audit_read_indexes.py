"""Add indexes for bounded regional and roster audit reads.

Revision ID: c5a7e2d9f410
Revises: b4e9d2a7f130
Create Date: 2026-11-07
"""

from collections.abc import Sequence

from alembic import op

revision: str = "c5a7e2d9f410"
down_revision: str | None = "b4e9d2a7f130"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_audit_events_region_occurred_at",
        "audit_events",
        ["region_id", "occurred_at"],
        unique=False,
    )
    op.create_index(
        "ix_audit_events_action_occurred_at",
        "audit_events",
        ["action", "occurred_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_audit_events_action_occurred_at", table_name="audit_events")
    op.drop_index("ix_audit_events_region_occurred_at", table_name="audit_events")
