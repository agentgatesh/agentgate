"""Add stripe_connect_id to organizations

Revision ID: l2m3n4o5p6q7
Revises: k1l2m3n4o5p6
Create Date: 2026-03-10
"""

import sqlalchemy as sa
from alembic import op

revision = "l2m3n4o5p6q7"
down_revision = "k1l2m3n4o5p6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("organizations", sa.Column("stripe_connect_id", sa.String(255), nullable=True))


def downgrade() -> None:
    op.drop_column("organizations", "stripe_connect_id")
