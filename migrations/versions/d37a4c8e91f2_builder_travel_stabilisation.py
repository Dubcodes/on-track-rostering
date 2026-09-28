"""Builder travel stabilisation and personal timing.

Revision ID: d37a4c8e91f2
Revises: c91e7f4a2b60
"""

import sqlalchemy as sa
from alembic import op

revision = "d37a4c8e91f2"
down_revision = "c91e7f4a2b60"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("workdays", sa.Column("generated_from_workday_id", sa.Uuid(), nullable=True))
    op.add_column(
        "workdays",
        sa.Column("status", sa.String(length=24), server_default="SCHEDULED", nullable=False),
    )
    op.create_index("ix_workdays_status", "workdays", ["status"])
    op.execute(
        """
        UPDATE workdays AS travel
        SET generated_from_workday_id = parent.id
        FROM workdays AS parent
        WHERE travel.operation_id = parent.operation_id
          AND travel.id <> parent.id
          AND travel.category = 'TRAVEL_DAY'
          AND parent.category IN ('RACE_DAY', 'TRIALS')
          AND travel.operation_id IS NOT NULL
        """
    )
    op.create_unique_constraint(
        "uq_workdays_generated_from_workday_id", "workdays", ["generated_from_workday_id"]
    )
    op.create_foreign_key(
        "fk_workdays_generated_from_workday_id",
        "workdays",
        "workdays",
        ["generated_from_workday_id"],
        ["id"],
    )
    op.add_column("workday_revisions", sa.Column("last_trial_time", sa.Time(), nullable=True))
    op.add_column(
        "workday_revisions",
        sa.Column("end_time_is_override", sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    op.add_column(
        "assignments", sa.Column("finish_destination_override", sa.String(length=160), nullable=True)
    )
    op.add_column(
        "assignments", sa.Column("return_travel_minutes_override", sa.Integer(), nullable=True)
    )
    op.create_table(
        "personal_workday_entries",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workday_id", sa.Uuid(), nullable=False),
        sa.Column("person_id", sa.Uuid(), nullable=False),
        sa.Column("note", sa.Text(), server_default="", nullable=False),
        sa.Column("start_time", sa.Time(), nullable=True),
        sa.Column("end_time", sa.Time(), nullable=True),
        sa.Column("last_race_time_changed", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("finished_back_at_office", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("standard_travel_opt_out", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["person_id"], ["people.id"]),
        sa.ForeignKeyConstraint(["workday_id"], ["workdays.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workday_id", "person_id"),
    )
    op.create_index(
        "ix_personal_workday_entries_workday_id", "personal_workday_entries", ["workday_id"]
    )
    op.create_index(
        "ix_personal_workday_entries_person_id", "personal_workday_entries", ["person_id"]
    )


def downgrade() -> None:
    op.drop_table("personal_workday_entries")
    op.drop_column("assignments", "return_travel_minutes_override")
    op.drop_column("assignments", "finish_destination_override")
    op.drop_column("workday_revisions", "last_trial_time")
    op.drop_column("workday_revisions", "end_time_is_override")
    op.drop_constraint("fk_workdays_generated_from_workday_id", "workdays", type_="foreignkey")
    op.drop_constraint("uq_workdays_generated_from_workday_id", "workdays", type_="unique")
    op.drop_index("ix_workdays_status", table_name="workdays")
    op.drop_column("workdays", "status")
    op.drop_column("workdays", "generated_from_workday_id")
