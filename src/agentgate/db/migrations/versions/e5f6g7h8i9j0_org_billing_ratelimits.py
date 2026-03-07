"""Add billing and rate limit fields to organizations

Revision ID: e5f6g7h8i9j0
Revises: d4e5f6g7h8i9
Create Date: 2026-03-07
"""

import sqlalchemy as sa
from alembic import op

revision = "e5f6g7h8i9j0"
down_revision = "d4e5f6g7h8i9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "organizations",
        sa.Column("cost_per_invocation", sa.Float, nullable=False, server_default="0.001"),
    )
    op.add_column(
        "organizations",
        sa.Column("billing_alert_threshold", sa.Float, nullable=True),
    )
    op.add_column(
        "organizations",
        sa.Column("rate_limit", sa.Float, nullable=False, server_default="10.0"),
    )
    op.add_column(
        "organizations",
        sa.Column("rate_burst", sa.Integer, nullable=False, server_default="20"),
    )


def downgrade() -> None:
    op.drop_column("organizations", "rate_burst")
    op.drop_column("organizations", "rate_limit")
    op.drop_column("organizations", "billing_alert_threshold")
    op.drop_column("organizations", "cost_per_invocation")
