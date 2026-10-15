"""management identity consolidation

Revision ID: e8b5d1f2a740
Revises: d7a4c9e2f610
Create Date: 2026-10-15
"""

import sqlalchemy as sa
from alembic import op

revision = "e8b5d1f2a740"
down_revision = "d7a4c9e2f610"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "error_reports",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("reporting_user_id", sa.Uuid(), nullable=False),
        sa.Column("reporting_person_id", sa.Uuid(), nullable=True),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("build_id", sa.String(length=120), nullable=False),
        sa.Column("page_path", sa.String(length=500), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reviewed_by_user_id", sa.Uuid(), nullable=True),
        sa.ForeignKeyConstraint(["reporting_user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["reporting_person_id"], ["people.id"]),
        sa.ForeignKeyConstraint(["reviewed_by_user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_error_reports_reporting_user_id", "error_reports", ["reporting_user_id"])
    op.create_index("ix_error_reports_reporting_person_id", "error_reports", ["reporting_person_id"])
    op.create_index("ix_error_reports_status", "error_reports", ["status"])
    op.create_table(
        "operational_notice_regions",
        sa.Column("notice_id", sa.Uuid(), nullable=False),
        sa.Column("region_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(["notice_id"], ["operational_notices.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["region_id"], ["regions.id"]),
        sa.PrimaryKeyConstraint("notice_id", "region_id"),
    )
    op.execute(
        "INSERT INTO operational_notice_regions (notice_id, region_id) "
        "SELECT id, region_id FROM operational_notices WHERE region_id IS NOT NULL"
    )
    op.create_table(
        "notice_publishing_scopes",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("configured_by_user_id", sa.Uuid(), nullable=False),
        sa.Column("configured_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["configured_by_user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("user_id"),
    )
    op.create_table(
        "notice_publishing_scope_regions",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("region_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"], ["notice_publishing_scopes.user_id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["region_id"], ["regions.id"]),
        sa.PrimaryKeyConstraint("user_id", "region_id"),
    )


def downgrade() -> None:
    op.drop_table("notice_publishing_scope_regions")
    op.drop_table("notice_publishing_scopes")
    op.drop_table("operational_notice_regions")
    op.drop_index("ix_error_reports_status", table_name="error_reports")
    op.drop_index("ix_error_reports_reporting_person_id", table_name="error_reports")
    op.drop_index("ix_error_reports_reporting_user_id", table_name="error_reports")
    op.drop_table("error_reports")
