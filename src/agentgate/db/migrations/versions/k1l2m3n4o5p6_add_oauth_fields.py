"""Add OAuth fields to organizations

Revision ID: k1l2m3n4o5p6
Revises: j0k1l2m3n4o5
Create Date: 2026-03-09
"""

import sqlalchemy as sa
from alembic import op

revision = "k1l2m3n4o5p6"
down_revision = "j0k1l2m3n4o5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("organizations", sa.Column("password_hash", sa.String(255), nullable=True))
    op.add_column("organizations", sa.Column("oauth_provider", sa.String(50), nullable=True))
    op.add_column("organizations", sa.Column("oauth_id", sa.String(255), nullable=True))


def downgrade() -> None:
    op.drop_column("organizations", "oauth_id")
    op.drop_column("organizations", "oauth_provider")
    op.drop_column("organizations", "password_hash")
