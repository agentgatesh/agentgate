"""Account panel API routes — session cookie auth for logged-in users."""

import secrets

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import func, select

from agentgate.db.engine import async_session
from agentgate.db.models import Agent, Organization, TaskLog, Transaction
from agentgate.server.auth import hash_api_key
from agentgate.server.auth_routes import (
    get_current_user,
    hash_password,
    verify_password,
)

router = APIRouter(prefix="/account/api", tags=["account"])


async def _require_user(request: Request) -> Organization:
    """Get the logged-in user or raise 401."""
    org = await get_current_user(request)
    if not org:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return org


# ---------------------------------------------------------------------------
# Dashboard overview
# ---------------------------------------------------------------------------


@router.get("/dashboard")
async def account_dashboard(request: Request):
    org = await _require_user(request)

    async with async_session() as session:
        # Agent count
        agent_count = (await session.execute(
            select(func.count(Agent.id)).where(Agent.org_id == org.id)
        )).scalar() or 0

        deployed_count = (await session.execute(
            select(func.count(Agent.id)).where(
                Agent.org_id == org.id, Agent.deployed.is_(True)
            )
        )).scalar() or 0

        # Agent IDs for task queries
        agent_ids_result = await session.execute(
            select(Agent.id).where(Agent.org_id == org.id)
        )
        agent_ids = [r[0] for r in agent_ids_result.all()]

        total_tasks = 0
        total_errors = 0
        if agent_ids:
            from sqlalchemy import case

            row = (await session.execute(
                select(
                    func.count(TaskLog.id).label("total"),
                    func.count(case((TaskLog.status == "error", TaskLog.id))).label("errors"),
                ).where(TaskLog.agent_id.in_(agent_ids))
            )).one()
            total_tasks = row.total
            total_errors = row.errors

        # Transaction summary
        spent = (await session.execute(
            select(func.coalesce(func.sum(Transaction.amount), 0))
            .where(Transaction.payer_org_id == org.id)
        )).scalar() or 0

        earned = (await session.execute(
            select(func.coalesce(func.sum(Transaction.net), 0))
            .where(Transaction.receiver_org_id == org.id)
        )).scalar() or 0

    return {
        "org_id": str(org.id),
        "org_name": org.name,
        "tier": org.tier,
        "balance": round(org.balance, 4),
        "agent_count": agent_count,
        "deployed_count": deployed_count,
        "total_tasks": total_tasks,
        "total_errors": total_errors,
        "total_spent": round(float(spent), 4),
        "total_earned": round(float(earned), 4),
    }


# ---------------------------------------------------------------------------
# Agents
# ---------------------------------------------------------------------------


@router.get("/agents")
async def account_agents(request: Request):
    org = await _require_user(request)

    async with async_session() as session:
        agents = (await session.execute(
            select(Agent).where(Agent.org_id == org.id).order_by(Agent.created_at.desc())
        )).scalars().all()

    return [
        {
            "id": str(a.id),
            "name": a.name,
            "description": a.description,
            "version": a.version,
            "url": a.url,
            "deployed": a.deployed,
            "price_per_task": a.price_per_task,
            "tags": a.tags or [],
            "created_at": a.created_at.isoformat() if a.created_at else None,
        }
        for a in agents
    ]


# ---------------------------------------------------------------------------
# Billing
# ---------------------------------------------------------------------------


@router.get("/billing")
async def account_billing(request: Request):
    org = await _require_user(request)

    async with async_session() as session:
        # Refresh org for latest balance
        fresh_org = await session.get(Organization, org.id)

        agent_count = (await session.execute(
            select(func.count(Agent.id)).where(Agent.org_id == org.id)
        )).scalar() or 0

        spent = (await session.execute(
            select(
                func.coalesce(func.sum(Transaction.amount), 0).label("total_spent"),
                func.count(Transaction.id).label("tx_count"),
            ).where(Transaction.payer_org_id == org.id)
        )).one()

        earned = (await session.execute(
            select(
                func.coalesce(func.sum(Transaction.net), 0).label("total_earned"),
                func.coalesce(func.sum(Transaction.fee), 0).label("total_fees"),
                func.count(Transaction.id).label("tx_count"),
            ).where(Transaction.receiver_org_id == org.id)
        )).one()

    from agentgate.server.org_routes import TIER_LIMITS

    tier_info = TIER_LIMITS.get(fresh_org.tier, TIER_LIMITS["free"])

    return {
        "org_id": str(org.id),
        "org_name": fresh_org.name,
        "balance": round(fresh_org.balance, 4),
        "tier": fresh_org.tier,
        "tier_limits": tier_info,
        "agent_count": agent_count,
        "total_spent": round(float(spent.total_spent), 4),
        "total_earned": round(float(earned.total_earned), 4),
        "total_fees_paid": round(float(earned.total_fees), 4),
        "transactions_as_payer": spent.tx_count,
        "transactions_as_receiver": earned.tx_count,
    }


@router.get("/transactions")
async def account_transactions(request: Request):
    org = await _require_user(request)

    async with async_session() as session:
        txs = (await session.execute(
            select(Transaction)
            .where(
                (Transaction.payer_org_id == org.id)
                | (Transaction.receiver_org_id == org.id)
            )
            .order_by(Transaction.created_at.desc())
            .limit(50)
        )).scalars().all()

    return [
        {
            "id": str(t.id),
            "agent_name": t.agent_name,
            "amount": round(t.amount, 4),
            "fee": round(t.fee, 4),
            "net": round(t.net, 4),
            "tx_type": t.tx_type,
            "direction": "paid" if t.payer_org_id == org.id else "received",
            "created_at": t.created_at.isoformat() if t.created_at else None,
        }
        for t in txs
    ]


# ---------------------------------------------------------------------------
# Profile
# ---------------------------------------------------------------------------


@router.get("/profile")
async def account_profile(request: Request):
    org = await _require_user(request)

    return {
        "org_id": str(org.id),
        "org_name": org.name,
        "email": org.email,
        "tier": org.tier,
        "has_password": bool(org.password_hash),
        "oauth_provider": org.oauth_provider,
        "rate_limit": org.rate_limit,
        "rate_burst": org.rate_burst,
        "created_at": org.created_at.isoformat() if org.created_at else None,
    }


@router.post("/change-password")
async def account_change_password(request: Request):
    org = await _require_user(request)
    body = await request.json()

    current_password = body.get("current_password", "")
    new_password = body.get("new_password", "")

    if not new_password or len(new_password) < 8:
        raise HTTPException(status_code=400, detail="New password must be at least 8 characters")

    # If user has a password, verify the current one
    if org.password_hash:
        if not current_password:
            raise HTTPException(status_code=400, detail="Current password is required")
        if not verify_password(current_password, org.password_hash):
            raise HTTPException(status_code=401, detail="Current password is incorrect")

    async with async_session() as session:
        db_org = await session.get(Organization, org.id)
        db_org.password_hash = hash_password(new_password)
        await session.commit()

    return {"message": "Password updated successfully"}


@router.post("/reset-key")
async def account_reset_key(request: Request):
    org = await _require_user(request)

    new_key = secrets.token_urlsafe(32)

    async with async_session() as session:
        db_org = await session.get(Organization, org.id)
        db_org.api_key_hash = hash_api_key(new_key)
        db_org.secondary_api_key_hash = None
        await session.commit()

    return {
        "message": "API key reset successfully",
        "api_key": new_key,
    }


# ---------------------------------------------------------------------------
# Stripe — wallet top-up & Pro subscription
# ---------------------------------------------------------------------------


@router.post("/topup")
async def account_topup(request: Request):
    """Create a Stripe Checkout session for wallet top-up."""
    org = await _require_user(request)
    body = await request.json()
    amount = body.get("amount", 0)

    from agentgate.server.stripe_routes import create_topup_checkout

    return await create_topup_checkout(str(org.id), amount)


@router.post("/subscribe-pro")
async def account_subscribe_pro(request: Request):
    """Create a Stripe Checkout session for Pro subscription."""
    org = await _require_user(request)

    from agentgate.server.stripe_routes import create_pro_checkout

    return await create_pro_checkout(str(org.id))


# ---------------------------------------------------------------------------
# Stripe Connect — developer payout
# ---------------------------------------------------------------------------


@router.post("/connect-onboard")
async def account_connect_onboard(request: Request):
    """Start Stripe Connect onboarding to receive payouts."""
    org = await _require_user(request)

    from agentgate.server.stripe_routes import create_connect_onboarding

    return await create_connect_onboarding(str(org.id))


@router.get("/connect-status")
async def account_connect_status(request: Request):
    """Get the user's Stripe Connect account status."""
    org = await _require_user(request)

    from agentgate.server.stripe_routes import get_connect_status

    return await get_connect_status(str(org.id))


@router.post("/withdraw")
async def account_withdraw(request: Request):
    """Withdraw funds from wallet to connected Stripe account."""
    org = await _require_user(request)
    body = await request.json()
    amount = body.get("amount", 0)

    from agentgate.server.stripe_routes import create_withdrawal

    return await create_withdrawal(str(org.id), amount)
