"""workday rescheduling

Revision ID: e4b7a1d2c903
Revises: d92f1a7c4e31
Create Date: 2026-10-10
"""

import sqlalchemy as sa
from alembic import op

revision = "e4b7a1d2c903"
down_revision = "d92f1a7c4e31"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "workdays",
        sa.Column("rescheduled_from_workday_id", sa.Uuid(), nullable=True),
    )
    op.create_foreign_key(
        "fk_workdays_rescheduled_from_workday_id_workdays",
        "workdays",
        "workdays",
        ["rescheduled_from_workday_id"],
        ["id"],
    )
    op.create_unique_constraint(
        "uq_workdays_rescheduled_from_workday_id",
        "workdays",
        ["rescheduled_from_workday_id"],
    )
    op.add_column(
        "personal_workday_entries",
        sa.Column("replacement_response", sa.String(length=16), nullable=True),
    )
    op.add_column(
        "personal_workday_entries",
        sa.Column("replacement_responded_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_check_constraint(
        "ck_personal_workday_entries_replacement_response",
        "personal_workday_entries",
        "replacement_response IS NULL OR replacement_response IN ('ACCEPTED', 'DECLINED')",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_personal_workday_entries_replacement_response",
        "personal_workday_entries",
        type_="check",
    )
    op.drop_column("personal_workday_entries", "replacement_responded_at")
    op.drop_column("personal_workday_entries", "replacement_response")
    op.drop_constraint(
        "uq_workdays_rescheduled_from_workday_id", "workdays", type_="unique"
    )
    op.drop_constraint(
        "fk_workdays_rescheduled_from_workday_id_workdays",
        "workdays",
        type_="foreignkey",
    )
    op.drop_column("workdays", "rescheduled_from_workday_id")
