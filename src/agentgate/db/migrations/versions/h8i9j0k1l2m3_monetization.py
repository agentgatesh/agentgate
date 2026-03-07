"""Add monetization: price_per_task, balance, tier, transactions

Revision ID: h8i9j0k1l2m3
Revises: g7h8i9j0k1l2
Create Date: 2026-03-07
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "h8i9j0k1l2m3"
down_revision = "g7h8i9j0k1l2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Agent pricing
    op.add_column(
        "agents",
        sa.Column("price_per_task", sa.Float(), nullable=False, server_default="0"),
    )

    # Org wallet + tier
    op.add_column(
        "organizations",
        sa.Column("balance", sa.Float(), nullable=False, server_default="0"),
    )
    op.add_column(
        "organizations",
        sa.Column("tier", sa.String(20), nullable=False, server_default="free"),
    )

    # Transaction ledger
    op.create_table(
        "transactions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("payer_org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("receiver_org_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("agent_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("agent_name", sa.String(255), nullable=False),
        sa.Column("amount", sa.Float(), nullable=False),
        sa.Column("fee", sa.Float(), nullable=False),
        sa.Column("net", sa.Float(), nullable=False),
        sa.Column("tx_type", sa.String(20), nullable=False, server_default="task"),
        sa.Column("task_id", sa.String(255), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_transactions_payer_org_id", "transactions", ["payer_org_id"])
    op.create_index(
        "ix_transactions_receiver_org_id", "transactions", ["receiver_org_id"],
    )
    op.create_index("ix_transactions_created_at", "transactions", ["created_at"])


def downgrade() -> None:
    op.drop_table("transactions")
    op.drop_column("organizations", "tier")
    op.drop_column("organizations", "balance")
    op.drop_column("agents", "price_per_task")
