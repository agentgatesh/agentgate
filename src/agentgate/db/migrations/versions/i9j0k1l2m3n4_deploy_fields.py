"""Add deploy fields: deployed, container_id, container_port

Revision ID: i9j0k1l2m3n4
Revises: h8i9j0k1l2m3
Create Date: 2026-03-07
"""

import sqlalchemy as sa
from alembic import op

revision = "i9j0k1l2m3n4"
down_revision = "h8i9j0k1l2m3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "agents",
        sa.Column("deployed", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.add_column(
        "agents",
        sa.Column("container_id", sa.String(255), nullable=True),
    )
    op.add_column(
        "agents",
        sa.Column("container_port", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("agents", "container_port")
    op.drop_column("agents", "container_id")
    op.drop_column("agents", "deployed")
