"""Racing programme, map cache, scheduler state, and transition references.

Revision ID: b6c2f0a9d741
Revises: 4a7d9c2e6b10
"""

import sqlalchemy as sa
from alembic import op

revision = "b6c2f0a9d741"
down_revision = "4a7d9c2e6b10"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("external_calendar_events", sa.Column("meeting_name", sa.String(160)))
    op.add_column(
        "external_calendar_events",
        sa.Column("programme_status", sa.String(24), server_default="DISCOVERED", nullable=False),
    )
    op.add_column("external_calendar_events", sa.Column("detail_checked_at", sa.DateTime(timezone=True)))
    op.add_column("external_calendar_events", sa.Column("next_detail_due_at", sa.DateTime(timezone=True)))
    op.add_column(
        "external_calendar_events",
        sa.Column("detail_failure_count", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column("external_calendar_events", sa.Column("latest_detail_error", sa.String(500)))
    op.add_column(
        "external_provider_states",
        sa.Column("explicitly_configured", sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    op.add_column(
        "external_provider_states",
        sa.Column("failure_count", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column("external_provider_states", sa.Column("next_refresh_at", sa.DateTime(timezone=True)))
    op.add_column("external_provider_states", sa.Column("latest_error", sa.String(500)))
    op.execute(
        "UPDATE external_provider_states SET enabled = true, status = 'READY' "
        "WHERE provider IN ('LOVE_RACING', 'HRNZ') AND enabled = false AND status = 'NOT_CONFIGURED'"
    )
    op.alter_column("external_provider_states", "enabled", server_default=sa.true())
    op.create_table(
        "track_maps",
        sa.Column("track_id", sa.Uuid(), nullable=False),
        sa.Column("automatic_file_name", sa.String(200)),
        sa.Column("automatic_content_type", sa.String(40)),
        sa.Column("automatic_hash", sa.String(64)),
        sa.Column("automatic_width", sa.Integer()),
        sa.Column("automatic_height", sa.Integer()),
        sa.Column("automatic_bytes", sa.BigInteger()),
        sa.Column("automatic_source_url", sa.String(1000)),
        sa.Column("automatic_status", sa.String(24), server_default="UNCHECKED", nullable=False),
        sa.Column("automatic_checked_at", sa.DateTime(timezone=True)),
        sa.Column("automatic_error", sa.String(500)),
        sa.Column("manual_file_name", sa.String(200)),
        sa.Column("manual_content_type", sa.String(40)),
        sa.Column("manual_hash", sa.String(64)),
        sa.Column("manual_width", sa.Integer()),
        sa.Column("manual_height", sa.Integer()),
        sa.Column("manual_bytes", sa.BigInteger()),
        sa.Column("manual_updated_at", sa.DateTime(timezone=True)),
        sa.Column("has_manual_override", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.ForeignKeyConstraint(["track_id"], ["tracks.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("track_id"),
    )
    op.create_table(
        "transition_source_references",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("source", sa.String(40), nullable=False),
        sa.Column("entity_kind", sa.String(40), nullable=False),
        sa.Column("external_key", sa.String(200), nullable=False),
        sa.Column("target_type", sa.String(40), nullable=False),
        sa.Column("target_id", sa.Uuid(), nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("imported_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source", "entity_kind", "external_key"),
    )
    op.create_index("ix_transition_source_references_source", "transition_source_references", ["source"])
    op.create_index("ix_transition_source_references_target_id", "transition_source_references", ["target_id"])


def downgrade() -> None:
    op.alter_column("external_provider_states", "enabled", server_default=sa.false())
    op.drop_index("ix_transition_source_references_target_id", table_name="transition_source_references")
    op.drop_index("ix_transition_source_references_source", table_name="transition_source_references")
    op.drop_table("transition_source_references")
    op.drop_table("track_maps")
    op.drop_column("external_provider_states", "latest_error")
    op.drop_column("external_provider_states", "next_refresh_at")
    op.drop_column("external_provider_states", "failure_count")
    op.drop_column("external_provider_states", "explicitly_configured")
    op.drop_column("external_calendar_events", "latest_detail_error")
    op.drop_column("external_calendar_events", "detail_failure_count")
    op.drop_column("external_calendar_events", "next_detail_due_at")
    op.drop_column("external_calendar_events", "detail_checked_at")
    op.drop_column("external_calendar_events", "programme_status")
    op.drop_column("external_calendar_events", "meeting_name")
