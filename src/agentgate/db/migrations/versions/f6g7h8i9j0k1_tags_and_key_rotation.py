"""Add agent tags and org key rotation

Revision ID: f6g7h8i9j0k1
Revises: e5f6g7h8i9j0
Create Date: 2026-03-07
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "f6g7h8i9j0k1"
down_revision = "e5f6g7h8i9j0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "agents",
        sa.Column("tags", JSONB, nullable=False, server_default="[]"),
    )
    op.add_column(
        "organizations",
        sa.Column("secondary_api_key_hash", sa.String(255), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("organizations", "secondary_api_key_hash")
    op.drop_column("agents", "tags")
