"""Add operational person unavailability ranges.

Revision ID: f59d8b7c1a20
Revises: e48c7ad291f0
"""

import sqlalchemy as sa
from alembic import op

revision = "f59d8b7c1a20"
down_revision = "e48c7ad291f0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "person_unavailability",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("person_id", sa.Uuid(), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column("note", sa.Text(), nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_by_user_id", sa.Uuid(), nullable=True),
        sa.CheckConstraint(
            "end_date >= start_date", name="person_unavailability_valid_dates"
        ),
        sa.ForeignKeyConstraint(["cancelled_by_user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["person_id"], ["people.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_person_unavailability_person_dates",
        "person_unavailability",
        ["person_id", "start_date", "end_date"],
    )
    op.create_index(
        op.f("ix_person_unavailability_person_id"),
        "person_unavailability",
        ["person_id"],
    )
    op.create_index(
        op.f("ix_person_unavailability_start_date"),
        "person_unavailability",
        ["start_date"],
    )
    op.create_index(
        op.f("ix_person_unavailability_end_date"),
        "person_unavailability",
        ["end_date"],
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_person_unavailability_end_date"), table_name="person_unavailability")
    op.drop_index(op.f("ix_person_unavailability_start_date"), table_name="person_unavailability")
    op.drop_index(op.f("ix_person_unavailability_person_id"), table_name="person_unavailability")
    op.drop_index(
        "ix_person_unavailability_person_dates", table_name="person_unavailability"
    )
    op.drop_table("person_unavailability")
