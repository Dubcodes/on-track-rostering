"""provider-neutral external calendar foundation

Revision ID: 7e6a1c2d4f90
Revises: 26a91f48b3d0
"""

import sqlalchemy as sa
from alembic import op

revision = "7e6a1c2d4f90"
down_revision = "26a91f48b3d0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "external_calendar_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("event_date", sa.Date(), nullable=False),
        sa.Column("track_id", sa.Uuid(), nullable=True),
        sa.Column("external_track_name", sa.String(160), nullable=True),
        sa.Column("discipline", sa.String(24), nullable=False),
        sa.Column("event_kind", sa.String(16), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("first_trial_time", sa.Time(), nullable=True),
        sa.Column("first_race_time", sa.Time(), nullable=True),
        sa.Column("last_race_time", sa.Time(), nullable=True),
        sa.Column("race_count", sa.Integer(), nullable=True),
        sa.Column("field_provenance", sa.JSON(), nullable=False),
        sa.Column("presentation_provider", sa.String(40), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["track_id"], ["tracks.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("event_date", "track_id", "discipline", "event_kind"),
    )
    op.create_index("ix_external_calendar_events_event_date", "external_calendar_events", ["event_date"])
    op.create_index("ix_external_calendar_events_track_id", "external_calendar_events", ["track_id"])
    op.create_table(
        "external_provider_states",
        sa.Column("provider", sa.String(40), primary_key=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(40), nullable=False),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("observations_found", sa.Integer(), nullable=False),
        sa.Column("events_created", sa.Integer(), nullable=False),
        sa.Column("events_enriched", sa.Integer(), nullable=False),
        sa.Column("warning_count", sa.Integer(), nullable=False),
    )
    op.create_table(
        "calendar_display_preferences",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("show_thoroughbred", sa.Boolean(), nullable=False),
        sa.Column("show_harness", sa.Boolean(), nullable=False),
        sa.Column("show_trials", sa.Boolean(), nullable=False),
        sa.Column("minimal_external_detail", sa.Boolean(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id"),
    )
    op.create_table(
        "external_track_mappings",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("provider", sa.String(40), nullable=False),
        sa.Column("external_track_key", sa.String(160), nullable=False),
        sa.Column("external_track_name", sa.String(160), nullable=False),
        sa.Column("track_id", sa.Uuid(), nullable=False),
        sa.Column("confirmed_by_user_id", sa.Uuid(), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["track_id"], ["tracks.id"]),
        sa.ForeignKeyConstraint(["confirmed_by_user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider", "external_track_key"),
    )
    op.create_index("ix_external_track_mappings_track_id", "external_track_mappings", ["track_id"])
    op.create_table(
        "external_event_observations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("event_id", sa.Uuid(), nullable=True),
        sa.Column("provider", sa.String(40), nullable=False),
        sa.Column("provider_event_id", sa.String(160), nullable=True),
        sa.Column("retrieved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("source_track_name", sa.String(160), nullable=False),
        sa.Column("parsed_facts", sa.JSON(), nullable=False),
        sa.Column("raw_payload", sa.JSON(), nullable=False),
        sa.Column("mapping_state", sa.String(24), nullable=False),
        sa.Column("reconciliation_state", sa.String(24), nullable=False),
        sa.ForeignKeyConstraint(["event_id"], ["external_calendar_events.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider", "provider_event_id", "payload_hash"),
    )
    op.create_index("ix_external_event_observations_event_id", "external_event_observations", ["event_id"])
    op.create_index("ix_external_event_observations_provider", "external_event_observations", ["provider"])
    op.add_column("workdays", sa.Column("external_event_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "fk_workdays_external_event_id_external_calendar_events",
        "workdays",
        "external_calendar_events",
        ["external_event_id"],
        ["id"],
    )
    op.create_unique_constraint("uq_workdays_external_event_id", "workdays", ["external_event_id"])


def downgrade() -> None:
    op.drop_constraint("uq_workdays_external_event_id", "workdays", type_="unique")
    op.drop_constraint(
        "fk_workdays_external_event_id_external_calendar_events", "workdays", type_="foreignkey"
    )
    op.drop_column("workdays", "external_event_id")
    op.drop_table("external_event_observations")
    op.drop_table("external_track_mappings")
    op.drop_table("calendar_display_preferences")
    op.drop_table("external_provider_states")
    op.drop_table("external_calendar_events")
