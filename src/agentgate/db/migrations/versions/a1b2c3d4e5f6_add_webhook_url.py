"""add webhook_url to agents

Revision ID: a1b2c3d4e5f6
Revises: 8c8195387159
Create Date: 2026-03-07 12:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, Sequence[str], None] = '8c8195387159'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("agents", sa.Column("webhook_url", sa.String(2048), nullable=True))


def downgrade() -> None:
    op.drop_column("agents", "webhook_url")
