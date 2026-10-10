"""roster presets and fortnight anchor

Revision ID: a7c4e9d2f610
Revises: e4b7a1d2c903
Create Date: 2026-10-10
"""

import sqlalchemy as sa
from alembic import op

revision = "a7c4e9d2f610"
down_revision = "e4b7a1d2c903"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "system_settings",
        sa.Column(
            "fortnight_anchor",
            sa.Date(),
            nullable=False,
            server_default="2026-08-31",
        ),
    )
    op.create_table(
        "position_presets",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("region_id", sa.Uuid(), nullable=False),
        sa.Column("preset_key", sa.String(length=24), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_by_user_id", sa.Uuid(), nullable=True),
        sa.ForeignKeyConstraint(
            ["region_id"], ["regions.id"],
            name=op.f("fk_position_presets_region_id_regions"), ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by_user_id"], ["users.id"],
            name=op.f("fk_position_presets_updated_by_user_id_users"),
        ),
        sa.CheckConstraint(
            "preset_key IN ('THOROUGHBRED', 'HARNESS', 'TRIALS')",
            name=op.f("ck_position_presets_position_preset_key"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_position_presets")),
        sa.UniqueConstraint("region_id", "preset_key", name=op.f("uq_position_presets_region_id")),
    )
    op.create_index(
        op.f("ix_position_presets_region_id"), "position_presets", ["region_id"], unique=False
    )
    op.create_table(
        "position_preset_items",
        sa.Column("preset_id", sa.Uuid(), nullable=False),
        sa.Column("base_position_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["base_position_id"], ["base_positions.id"],
            name=op.f("fk_position_preset_items_base_position_id_base_positions"),
        ),
        sa.ForeignKeyConstraint(
            ["preset_id"], ["position_presets.id"],
            name=op.f("fk_position_preset_items_preset_id_position_presets"), ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("preset_id", "base_position_id", name=op.f("pk_position_preset_items")),
    )


def downgrade() -> None:
    op.drop_table("position_preset_items")
    op.drop_index(op.f("ix_position_presets_region_id"), table_name="position_presets")
    op.drop_table("position_presets")
    op.drop_column("system_settings", "fortnight_anchor")
