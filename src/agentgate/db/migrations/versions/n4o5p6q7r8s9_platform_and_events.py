"""Add processed_events (webhook idempotency) and seed platform fee account

Revision ID: n4o5p6q7r8s9
Revises: m3n4o5p6q7r8
Create Date: 2026-04-19

- processed_events table: tracks processed webhook event IDs so retries are
  safe (Stripe retries webhooks indefinitely until 2xx, and network drops
  between the webhook handler and the DB can also cause dupes).
- platform fee account: a singleton Organization named "__platform__" whose
  balance accumulates every fee charged by the billing engine. Makes the
  Transaction ledger double-entry: every debit has a matching credit.
"""

import uuid

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "n4o5p6q7r8s9"
down_revision = "m3n4o5p6q7r8"
branch_labels = None
depends_on = None

PLATFORM_ORG_NAME = "__platform__"


def upgrade() -> None:
    op.create_table(
        "processed_events",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("source", sa.String(50), nullable=False),
        sa.Column("event_id", sa.String(255), nullable=False),
        sa.Column("event_type", sa.String(100), nullable=False),
        sa.Column(
            "processed_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_processed_events_source_id",
        "processed_events",
        ["source", "event_id"],
        unique=True,
    )

    # Seed the platform fee account. api_key_hash is a random non-matching
    # hash — the account is never used for auth.
    op.execute(
        sa.text(
            "INSERT INTO organizations (id, name, api_key_hash, balance, tier) "
            "VALUES (:id, :name, :hash, 0, 'enterprise') "
            "ON CONFLICT (name) DO NOTHING"
        ).bindparams(
            id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
            name=PLATFORM_ORG_NAME,
            hash="__platform_no_auth__",
        )
    )


def downgrade() -> None:
    op.execute(
        sa.text("DELETE FROM organizations WHERE name = :name").bindparams(
            name=PLATFORM_ORG_NAME,
        )
    )
    op.drop_index("ix_processed_events_source_id", table_name="processed_events")
    op.drop_table("processed_events")
