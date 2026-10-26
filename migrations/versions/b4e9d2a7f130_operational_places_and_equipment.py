"""Add reusable operational places and revision equipment snapshots.

Revision ID: b4e9d2a7f130
Revises: a3d8c1f4e720
"""

import sqlalchemy as sa
from alembic import op

revision = "b4e9d2a7f130"
down_revision = "a3d8c1f4e720"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "operational_places",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("region_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("address", sa.String(length=500), nullable=True),
        sa.Column("kind", sa.String(length=24), nullable=False),
        sa.Column("lifecycle", sa.String(length=16), nullable=False),
        sa.Column("lock_version", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "kind IN ('ACCOMMODATION', 'DESTINATION')",
            name=op.f("ck_operational_places_operational_place_kind"),
        ),
        sa.ForeignKeyConstraint(["region_id"], ["regions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_operational_places_region_id", "operational_places", ["region_id"])
    op.create_index(
        "uq_operational_places_region_name_ci",
        "operational_places",
        ["region_id", sa.text("lower(name)")],
        unique=True,
    )
    op.create_table(
        "equipment_setups",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("region_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("notes", sa.String(length=500), nullable=False),
        sa.Column("lifecycle", sa.String(length=16), nullable=False),
        sa.Column("lock_version", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["region_id"], ["regions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_equipment_setups_region_id", "equipment_setups", ["region_id"])
    op.create_index(
        "uq_equipment_setups_region_name_ci",
        "equipment_setups",
        ["region_id", sa.text("lower(name)")],
        unique=True,
    )
    op.create_table(
        "equipment_setup_items",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("setup_id", sa.Uuid(), nullable=False),
        sa.Column("item_key", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("notes", sa.String(length=500), nullable=False),
        sa.Column("display_order", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "quantity BETWEEN 1 AND 999",
            name=op.f("ck_equipment_setup_items_equipment_setup_item_quantity"),
        ),
        sa.ForeignKeyConstraint(["setup_id"], ["equipment_setups.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("setup_id", "item_key"),
    )
    op.create_index("ix_equipment_setup_items_setup_id", "equipment_setup_items", ["setup_id"])
    op.add_column(
        "workday_revisions",
        sa.Column("default_hotel_address_snapshot", sa.String(length=500), nullable=False, server_default=""),
    )
    op.create_table(
        "workday_equipment_items",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("revision_id", sa.Uuid(), nullable=False),
        sa.Column("item_key", sa.Uuid(), nullable=False),
        sa.Column("setup_id", sa.Uuid(), nullable=True),
        sa.Column("setup_name_snapshot", sa.String(length=160), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("notes", sa.String(length=500), nullable=False),
        sa.Column("display_order", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "quantity BETWEEN 1 AND 999",
            name=op.f("ck_workday_equipment_items_workday_equipment_item_quantity"),
        ),
        sa.ForeignKeyConstraint(["revision_id"], ["workday_revisions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["setup_id"], ["equipment_setups.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("revision_id", "item_key"),
    )
    op.create_index("ix_workday_equipment_items_revision_id", "workday_equipment_items", ["revision_id"])


def downgrade() -> None:
    op.drop_index("ix_workday_equipment_items_revision_id", table_name="workday_equipment_items")
    op.drop_table("workday_equipment_items")
    op.drop_column("workday_revisions", "default_hotel_address_snapshot")
    op.drop_index("ix_equipment_setup_items_setup_id", table_name="equipment_setup_items")
    op.drop_table("equipment_setup_items")
    op.drop_index("uq_equipment_setups_region_name_ci", table_name="equipment_setups")
    op.drop_index("ix_equipment_setups_region_id", table_name="equipment_setups")
    op.drop_table("equipment_setups")
    op.drop_index("uq_operational_places_region_name_ci", table_name="operational_places")
    op.drop_index("ix_operational_places_region_id", table_name="operational_places")
    op.drop_table("operational_places")
