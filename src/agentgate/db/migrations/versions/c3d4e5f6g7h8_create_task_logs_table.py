"""Create task_logs table

Revision ID: c3d4e5f6g7h8
Revises: a1b2c3d4e5f6
Create Date: 2026-03-07
"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "c3d4e5f6g7h8"
down_revision: Union[str, Sequence[str], None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "task_logs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("agent_id", UUID(as_uuid=True), nullable=False),
        sa.Column("agent_name", sa.String(255), nullable=False),
        sa.Column("caller_ip", sa.String(45), nullable=False),
        sa.Column("task_id", sa.String(255), nullable=True),
        sa.Column("status", sa.String(50), nullable=False),
        sa.Column("error_detail", sa.Text(), nullable=True),
        sa.Column("latency_ms", sa.Float(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_task_logs_agent_id", "task_logs", ["agent_id"])
    op.create_index("ix_task_logs_created_at", "task_logs", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_task_logs_created_at", table_name="task_logs")
    op.drop_index("ix_task_logs_agent_id", table_name="task_logs")
    op.drop_table("task_logs")
