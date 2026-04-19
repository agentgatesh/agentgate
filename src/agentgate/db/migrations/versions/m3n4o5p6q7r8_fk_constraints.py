"""Add foreign key constraints with ON DELETE actions

Revision ID: m3n4o5p6q7r8
Revises: l2m3n4o5p6q7
Create Date: 2026-04-19

Adds explicit FK constraints so cross-table state stays consistent:
- agents.org_id        -> organizations.id  ON DELETE SET NULL
- chains.org_id        -> organizations.id  ON DELETE SET NULL
- task_logs.agent_id   -> agents.id         ON DELETE CASCADE
- reviews.agent_id     -> agents.id         ON DELETE CASCADE
- transactions.payer_org_id    -> organizations.id  ON DELETE SET NULL
- transactions.receiver_org_id -> organizations.id  ON DELETE SET NULL
- transactions.agent_id        -> agents.id         ON DELETE SET NULL

transactions.payer_org_id and transactions.agent_id become nullable so the
ledger survives org/agent deletion (audit trail preserved).
"""

from alembic import op

revision = "m3n4o5p6q7r8"
down_revision = "l2m3n4o5p6q7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("transactions", "payer_org_id", nullable=True)
    op.alter_column("transactions", "agent_id", nullable=True)

    op.create_foreign_key(
        "fk_agents_org_id", "agents", "organizations",
        ["org_id"], ["id"], ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_chains_org_id", "chains", "organizations",
        ["org_id"], ["id"], ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_task_logs_agent_id", "task_logs", "agents",
        ["agent_id"], ["id"], ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_reviews_agent_id", "reviews", "agents",
        ["agent_id"], ["id"], ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_transactions_payer", "transactions", "organizations",
        ["payer_org_id"], ["id"], ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_transactions_receiver", "transactions", "organizations",
        ["receiver_org_id"], ["id"], ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_transactions_agent", "transactions", "agents",
        ["agent_id"], ["id"], ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_transactions_agent", "transactions", type_="foreignkey")
    op.drop_constraint("fk_transactions_receiver", "transactions", type_="foreignkey")
    op.drop_constraint("fk_transactions_payer", "transactions", type_="foreignkey")
    op.drop_constraint("fk_reviews_agent_id", "reviews", type_="foreignkey")
    op.drop_constraint("fk_task_logs_agent_id", "task_logs", type_="foreignkey")
    op.drop_constraint("fk_chains_org_id", "chains", type_="foreignkey")
    op.drop_constraint("fk_agents_org_id", "agents", type_="foreignkey")

    op.alter_column("transactions", "agent_id", nullable=False)
    op.alter_column("transactions", "payer_org_id", nullable=False)
