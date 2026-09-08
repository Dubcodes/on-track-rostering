"""WebAuthn challenges and optional TOTP factors

Revision ID: 7a91d36e5b20
Revises: 4c72a6f19d31
Create Date: 2026-09-08
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "7a91d36e5b20"
down_revision: str | Sequence[str] | None = "4c72a6f19d31"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "totp_factors",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("encrypted_secret", sa.LargeBinary(), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("disabled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_counter", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_totp_factors_user_id_users"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("user_id", name=op.f("pk_totp_factors")),
    )
    op.create_table(
        "webauthn_challenges",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("purpose", sa.String(length=24), nullable=False),
        sa.Column("challenge_hash", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column("device_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["device_id"],
            ["trusted_devices.id"],
            name=op.f("fk_webauthn_challenges_device_id_trusted_devices"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_webauthn_challenges_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_webauthn_challenges")),
        sa.UniqueConstraint("challenge_hash", name=op.f("uq_webauthn_challenges_challenge_hash")),
    )
    op.create_index(
        op.f("ix_webauthn_challenges_expires_at"),
        "webauthn_challenges",
        ["expires_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_webauthn_challenges_purpose"),
        "webauthn_challenges",
        ["purpose"],
        unique=False,
    )
    op.create_index(
        op.f("ix_webauthn_challenges_user_id"),
        "webauthn_challenges",
        ["user_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_webauthn_challenges_user_id"), table_name="webauthn_challenges")
    op.drop_index(op.f("ix_webauthn_challenges_purpose"), table_name="webauthn_challenges")
    op.drop_index(op.f("ix_webauthn_challenges_expires_at"), table_name="webauthn_challenges")
    op.drop_table("webauthn_challenges")
    op.drop_table("totp_factors")
