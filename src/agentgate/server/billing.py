"""Billing engine — atomic balance updates, double-entry ledger.

Every paid task produces three balance moves, always in a single DB
transaction, so the ledger is balanced (sum of Transaction.fee == platform
balance growth; sum of debits == sum of credits):

    payer.balance        -= price
    receiver.balance     += net        (price - fee, if receiver != payer)
    platform.balance     += fee
    Transaction(...)     INSERT

Atomicity: the payer debit is a conditional UPDATE
`WHERE balance >= price`. If rowcount is 0, no charge was applied and we
return an insufficient-balance error — concurrent tasks cannot overdraft.
"""

from __future__ import annotations

import logging
import uuid

from sqlalchemy import select, update

from agentgate.db.engine import async_session
from agentgate.db.models import Agent, Organization, Transaction

logger = logging.getLogger("agentgate.billing")

PLATFORM_ORG_NAME = "__platform__"

TIER_FEE_PCT: dict[str, float] = {
    "free": 0.03,
    "pro": 0.025,
    "enterprise": 0.02,
}


def _compute_fee(price: float, tier: str) -> tuple[float, float]:
    fee_pct = TIER_FEE_PCT.get(tier, 0.03)
    fee = round(price * fee_pct, 6)
    net = round(price - fee, 6)
    return fee, net


async def process_charge(
    agent: Agent,
    payer_org: Organization | None,
    task_id: str | None,
) -> tuple[bool, str | None]:
    """Charge a paid agent invocation. Returns (charged, error_msg).

    - Free agent (price <= 0): returns (True, None), no ledger write.
    - Admin/no payer org: returns (True, None), no ledger write.
    - Insufficient balance: returns (False, reason), no state change.
    - Success: debits payer, credits receiver (if any), credits platform,
      writes one Transaction row. All in one DB transaction.
    """
    price = agent.price_per_task
    if price <= 0 or payer_org is None:
        return True, None

    fee, net = _compute_fee(price, payer_org.tier)

    async with async_session() as session:
        # Atomic debit — fails if balance would go negative.
        debit = await session.execute(
            update(Organization)
            .where(Organization.id == payer_org.id)
            .where(Organization.balance >= price)
            .values(balance=Organization.balance - price)
        )
        if debit.rowcount == 0:
            # Re-read for a helpful error message (best-effort).
            fresh = await session.get(Organization, payer_org.id)
            bal = fresh.balance if fresh else 0.0
            return False, (
                f"Insufficient balance: {bal:.4f} < {price:.4f} "
                f"(agent: {agent.name})"
            )

        receiver_id: uuid.UUID | None = None
        if agent.org_id and agent.org_id != payer_org.id:
            await session.execute(
                update(Organization)
                .where(Organization.id == agent.org_id)
                .values(balance=Organization.balance + net)
            )
            receiver_id = agent.org_id

        # Credit platform fee — double-entry: every debit has a credit.
        await session.execute(
            update(Organization)
            .where(Organization.name == PLATFORM_ORG_NAME)
            .values(balance=Organization.balance + fee)
        )

        session.add(Transaction(
            payer_org_id=payer_org.id,
            receiver_org_id=receiver_id,
            agent_id=agent.id,
            agent_name=agent.name,
            amount=price,
            fee=fee,
            net=net,
            tx_type="task",
            task_id=task_id,
        ))
        await session.commit()

    logger.info(
        "Billing: org=%s charged=%.4f agent=%s fee=%.4f net=%.4f",
        payer_org.name, price, agent.name, fee, net,
    )
    return True, None


async def process_withdrawal(
    org_id: uuid.UUID,
    amount: float,
    fee: float,
    net: float,
) -> tuple[bool, str | None]:
    """Atomic wallet withdrawal debit. Returns (ok, error_msg).

    Caller is responsible for creating the Stripe Transfer before/after this
    runs and for writing the withdrawal Transaction row.
    """
    async with async_session() as session:
        debit = await session.execute(
            update(Organization)
            .where(Organization.id == org_id)
            .where(Organization.balance >= amount)
            .values(balance=Organization.balance - amount)
        )
        if debit.rowcount == 0:
            fresh = await session.get(Organization, org_id)
            bal = fresh.balance if fresh else 0.0
            return False, f"Insufficient balance: {bal:.4f} < {amount:.4f}"
        await session.commit()
    logger.info(
        "Withdrawal debit: org=%s amount=%.4f fee=%.4f net=%.4f",
        org_id, amount, fee, net,
    )
    return True, None


async def credit_wallet(org_id: uuid.UUID, amount: float) -> None:
    """Credit a wallet (e.g. Stripe top-up). Atomic increment."""
    async with async_session() as session:
        await session.execute(
            update(Organization)
            .where(Organization.id == org_id)
            .values(balance=Organization.balance + amount)
        )
        await session.commit()


async def is_event_processed(source: str, event_id: str) -> bool:
    """Check if a webhook event was already processed (idempotency)."""
    from agentgate.db.models import ProcessedEvent

    async with async_session() as session:
        result = await session.execute(
            select(ProcessedEvent.id).where(
                ProcessedEvent.source == source,
                ProcessedEvent.event_id == event_id,
            )
        )
        return result.scalar_one_or_none() is not None


async def mark_event_processed(source: str, event_id: str, event_type: str) -> bool:
    """Record a webhook event as processed. Returns False if already recorded
    (unique constraint), True on first successful insert."""
    from sqlalchemy.exc import IntegrityError

    from agentgate.db.models import ProcessedEvent

    async with async_session() as session:
        session.add(ProcessedEvent(
            source=source, event_id=event_id, event_type=event_type,
        ))
        try:
            await session.commit()
            return True
        except IntegrityError:
            await session.rollback()
            return False
