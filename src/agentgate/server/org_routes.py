"""Organization routes with org-scoped auth, billing, and rate limits."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy import case, cast, func, select
from sqlalchemy.types import Date

from agentgate.core.config import settings
from agentgate.db.engine import async_session
from agentgate.db.models import Agent, Organization, TaskLog, Transaction
from agentgate.server.auth import bearer_scheme, hash_api_key
from agentgate.server.schemas import AgentResponse, OrgCreate, OrgResponse, OrgUpdate, SignupRequest

router = APIRouter(prefix="/orgs", tags=["organizations"])


# ---------------------------------------------------------------------------
# Public signup — no auth required
# ---------------------------------------------------------------------------


@router.post("/signup", status_code=201)
async def signup(data: SignupRequest):
    """Create a new organization with a generated API key. No auth required.

    Returns the API key (shown only once — save it!).
    """
    import secrets

    async with async_session() as session:
        existing = await session.execute(
            select(Organization).where(Organization.name == data.name)
        )
        if existing.scalar_one_or_none():
            raise HTTPException(status_code=409, detail="Organization name already taken")

        api_key = secrets.token_urlsafe(32)

        password_hash = None
        if data.password:
            from agentgate.server.auth_routes import hash_password
            password_hash = hash_password(data.password)

        org = Organization(
            name=data.name,
            email=data.email,
            password_hash=password_hash,
            api_key_hash=hash_api_key(api_key),
            tier="free",
        )
        session.add(org)
        await session.commit()
        await session.refresh(org)

    return {
        "message": "Organization created successfully",
        "org_id": str(org.id),
        "org_name": org.name,
        "api_key": api_key,
        "tier": org.tier,
        "note": "Save your API key — it won't be shown again.",
    }


async def _get_org_by_key(session, credentials: HTTPAuthorizationCredentials) -> Organization:
    """Look up an organization by its API key hash (checks both primary and secondary)."""
    key_hash = hash_api_key(credentials.credentials)
    result = await session.execute(
        select(Organization).where(
            (Organization.api_key_hash == key_hash)
            | (Organization.secondary_api_key_hash == key_hash)
        )
    )
    return result.scalar_one_or_none()


def _is_admin_key(credentials: HTTPAuthorizationCredentials) -> bool:
    """Check if the provided credentials match the global admin API key."""
    return bool(settings.api_key and credentials.credentials == settings.api_key)


async def verify_admin_key(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
):
    """Verify admin API key. Used for org management endpoints."""
    if not settings.api_key:
        raise HTTPException(status_code=500, detail="API key not configured on server")
    if credentials.credentials != settings.api_key:
        raise HTTPException(status_code=401, detail="Invalid API key")


async def resolve_org_or_admin(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
) -> Organization | None:
    """Resolve the caller: returns Organization if org key, None if admin key.

    Raises 401 if the key matches neither.
    """
    if _is_admin_key(credentials):
        return None  # Admin — full access

    async with async_session() as session:
        org = await _get_org_by_key(session, credentials)
        if org:
            return org

    raise HTTPException(status_code=401, detail="Invalid API key")


# ---------------------------------------------------------------------------
# CRUD — admin only for create/list/delete, org-scoped for get/update
# ---------------------------------------------------------------------------


@router.post(
    "/", response_model=OrgResponse, status_code=201,
    dependencies=[Depends(verify_admin_key)],
)
async def create_org(data: OrgCreate):
    """Create a new organization. Requires admin API key."""
    async with async_session() as session:
        existing = await session.execute(
            select(Organization).where(Organization.name == data.name)
        )
        if existing.scalar_one_or_none():
            raise HTTPException(status_code=409, detail="Organization name already exists")
        org = Organization(
            name=data.name,
            api_key_hash=hash_api_key(data.api_key),
            cost_per_invocation=data.cost_per_invocation,
            billing_alert_threshold=data.billing_alert_threshold,
            rate_limit=data.rate_limit,
            rate_burst=data.rate_burst,
            tier=data.tier,
        )
        session.add(org)
        await session.commit()
        await session.refresh(org)
        return org


@router.get(
    "/", response_model=list[OrgResponse],
    dependencies=[Depends(verify_admin_key)],
)
async def list_orgs():
    """List all organizations. Requires admin API key."""
    async with async_session() as session:
        result = await session.execute(
            select(Organization).order_by(Organization.created_at.desc())
        )
        return result.scalars().all()


@router.get("/{org_id}", response_model=OrgResponse)
async def get_org(
    org_id: uuid.UUID,
    caller_org: Organization | None = Depends(resolve_org_or_admin),
):
    """Get an organization. Admin sees any, org sees only itself."""
    if caller_org and caller_org.id != org_id:
        raise HTTPException(status_code=403, detail="Access denied to this organization")
    async with async_session() as session:
        org = await session.get(Organization, org_id)
        if not org:
            raise HTTPException(status_code=404, detail="Organization not found")
        return org


@router.put("/{org_id}", response_model=OrgResponse)
async def update_org(
    org_id: uuid.UUID,
    data: OrgUpdate,
    caller_org: Organization | None = Depends(resolve_org_or_admin),
):
    """Update an organization. Admin or org owner."""
    if caller_org and caller_org.id != org_id:
        raise HTTPException(status_code=403, detail="Access denied to this organization")
    async with async_session() as session:
        org = await session.get(Organization, org_id)
        if not org:
            raise HTTPException(status_code=404, detail="Organization not found")
        for field, value in data.model_dump(exclude_none=True).items():
            setattr(org, field, value)
        await session.commit()
        await session.refresh(org)
        return org


@router.delete(
    "/{org_id}", status_code=204,
    dependencies=[Depends(verify_admin_key)],
)
async def delete_org(org_id: uuid.UUID):
    """Delete an organization. Requires admin API key."""
    async with async_session() as session:
        org = await session.get(Organization, org_id)
        if not org:
            raise HTTPException(status_code=404, detail="Organization not found")
        await session.delete(org)
        await session.commit()


# ---------------------------------------------------------------------------
# API key rotation
# ---------------------------------------------------------------------------


@router.post("/{org_id}/rotate-key")
async def rotate_org_key(
    org_id: uuid.UUID,
    caller_org: Organization | None = Depends(resolve_org_or_admin),
):
    """Start key rotation: generate a new secondary key.

    The old key remains valid. Call /confirm-rotation to promote the
    new key and revoke the old one.

    Returns the new API key (only shown once).
    """
    import secrets

    if caller_org and caller_org.id != org_id:
        raise HTTPException(status_code=403, detail="Access denied to this organization")

    new_key = secrets.token_urlsafe(32)
    async with async_session() as session:
        org = await session.get(Organization, org_id)
        if not org:
            raise HTTPException(status_code=404, detail="Organization not found")
        org.secondary_api_key_hash = hash_api_key(new_key)
        await session.commit()

    return {"new_api_key": new_key, "status": "pending_confirmation"}


@router.post("/{org_id}/confirm-rotation")
async def confirm_key_rotation(
    org_id: uuid.UUID,
    caller_org: Organization | None = Depends(resolve_org_or_admin),
):
    """Confirm key rotation: promote secondary key to primary.

    After this call, only the new key (from /rotate-key) will work.
    """
    if caller_org and caller_org.id != org_id:
        raise HTTPException(status_code=403, detail="Access denied to this organization")

    async with async_session() as session:
        org = await session.get(Organization, org_id)
        if not org:
            raise HTTPException(status_code=404, detail="Organization not found")
        if not org.secondary_api_key_hash:
            raise HTTPException(
                status_code=400,
                detail="No pending rotation. Call /rotate-key first.",
            )
        org.api_key_hash = org.secondary_api_key_hash
        org.secondary_api_key_hash = None
        await session.commit()

    return {"status": "rotation_confirmed"}


# ---------------------------------------------------------------------------
# Org-scoped agent listing
# ---------------------------------------------------------------------------


@router.get("/{org_id}/agents", response_model=list[AgentResponse])
async def list_org_agents(
    org_id: uuid.UUID,
    caller_org: Organization | None = Depends(resolve_org_or_admin),
):
    """List agents belonging to an organization. Admin or org owner."""
    if caller_org and caller_org.id != org_id:
        raise HTTPException(status_code=403, detail="Access denied to this organization")
    async with async_session() as session:
        org = await session.get(Organization, org_id)
        if not org:
            raise HTTPException(status_code=404, detail="Organization not found")
        result = await session.execute(
            select(Agent).where(Agent.org_id == org_id).order_by(Agent.created_at.desc())
        )
        return result.scalars().all()


# ---------------------------------------------------------------------------
# Org billing
# ---------------------------------------------------------------------------


@router.get("/{org_id}/billing")
async def get_org_billing(
    org_id: uuid.UUID,
    caller_org: Organization | None = Depends(resolve_org_or_admin),
):
    """Get billing summary for an organization. Admin or org owner."""
    if caller_org and caller_org.id != org_id:
        raise HTTPException(status_code=403, detail="Access denied to this organization")

    async with async_session() as session:
        org = await session.get(Organization, org_id)
        if not org:
            raise HTTPException(status_code=404, detail="Organization not found")

        agent_result = await session.execute(
            select(Agent.id).where(Agent.org_id == org_id)
        )
        agent_ids = [row[0] for row in agent_result.all()]

        if not agent_ids:
            return {
                "org_id": str(org_id),
                "org_name": org.name,
                "cost_per_invocation": org.cost_per_invocation,
                "total_invocations": 0,
                "total_errors": 0,
                "total_cost": 0.0,
                "alert_threshold": org.billing_alert_threshold,
                "alert_triggered": False,
            }

        result = await session.execute(
            select(
                func.count(TaskLog.id).label("total_invocations"),
                func.count(
                    case((TaskLog.status == "error", TaskLog.id))
                ).label("total_errors"),
            ).where(TaskLog.agent_id.in_(agent_ids))
        )
        row = result.one()

    total_cost = round(row.total_invocations * org.cost_per_invocation, 4)
    alert_triggered = (
        org.billing_alert_threshold is not None
        and total_cost >= org.billing_alert_threshold
    )

    return {
        "org_id": str(org_id),
        "org_name": org.name,
        "cost_per_invocation": org.cost_per_invocation,
        "total_invocations": row.total_invocations,
        "total_errors": row.total_errors,
        "total_cost": total_cost,
        "alert_threshold": org.billing_alert_threshold,
        "alert_triggered": alert_triggered,
    }


@router.get("/{org_id}/billing/breakdown")
async def get_org_billing_breakdown(
    org_id: uuid.UUID,
    caller_org: Organization | None = Depends(resolve_org_or_admin),
):
    """Get billing breakdown by day for an organization."""
    from datetime import datetime, timedelta, timezone

    if caller_org and caller_org.id != org_id:
        raise HTTPException(status_code=403, detail="Access denied to this organization")

    async with async_session() as session:
        org = await session.get(Organization, org_id)
        if not org:
            raise HTTPException(status_code=404, detail="Organization not found")

        agent_result = await session.execute(
            select(Agent.id).where(Agent.org_id == org_id)
        )
        agent_ids = [row[0] for row in agent_result.all()]

        if not agent_ids:
            return {
                "org_id": str(org_id),
                "org_name": org.name,
                "cost_per_invocation": org.cost_per_invocation,
                "breakdown": [],
            }

        since = datetime.now(timezone.utc) - timedelta(days=30)
        date_col = cast(TaskLog.created_at, Date).label("period")

        query = (
            select(
                date_col,
                func.count(TaskLog.id).label("invocations"),
                func.count(
                    case((TaskLog.status == "error", TaskLog.id))
                ).label("errors"),
            )
            .where(TaskLog.agent_id.in_(agent_ids), TaskLog.created_at >= since)
            .group_by("period")
            .order_by("period")
        )
        result = await session.execute(query)
        rows = result.all()

    return {
        "org_id": str(org_id),
        "org_name": org.name,
        "cost_per_invocation": org.cost_per_invocation,
        "breakdown": [
            {
                "period": (
                    row.period.isoformat()
                    if hasattr(row.period, "isoformat") else str(row.period)
                ),
                "invocations": row.invocations,
                "errors": row.errors,
                "cost": round(row.invocations * org.cost_per_invocation, 4),
            }
            for row in rows
        ],
    }


# ---------------------------------------------------------------------------
# Wallet / Balance
# ---------------------------------------------------------------------------

TIER_LIMITS = {
    "free": {"max_agents": 5, "rate_limit": 10.0, "rate_burst": 20, "fee_pct": 0.03},
    "pro": {"max_agents": 50, "rate_limit": 100.0, "rate_burst": 200, "fee_pct": 0.025},
    "enterprise": {"max_agents": 500, "rate_limit": 1000.0, "rate_burst": 2000, "fee_pct": 0.02},
}


@router.get("/{org_id}/wallet")
async def get_org_wallet(
    org_id: uuid.UUID,
    caller_org: Organization | None = Depends(resolve_org_or_admin),
):
    """Get wallet balance and tier info for an organization."""
    if caller_org and caller_org.id != org_id:
        raise HTTPException(status_code=403, detail="Access denied to this organization")

    async with async_session() as session:
        org = await session.get(Organization, org_id)
        if not org:
            raise HTTPException(status_code=404, detail="Organization not found")

        # Count agents owned
        agent_count_result = await session.execute(
            select(func.count(Agent.id)).where(Agent.org_id == org_id)
        )
        agent_count = agent_count_result.scalar() or 0

    tier_info = TIER_LIMITS.get(org.tier, TIER_LIMITS["free"])

    return {
        "org_id": str(org_id),
        "org_name": org.name,
        "balance": round(org.balance, 4),
        "tier": org.tier,
        "tier_limits": tier_info,
        "agent_count": agent_count,
        "max_agents": tier_info["max_agents"],
    }


@router.post("/{org_id}/topup")
async def topup_org_wallet(
    org_id: uuid.UUID,
    body: dict,
    caller_org: Organization | None = Depends(resolve_org_or_admin),
):
    """Add funds to an organization's wallet. Admin or org owner.

    Body: {"amount": 10.0}

    NOTE: Currently disabled — Stripe integration coming soon.
    """
    raise HTTPException(
        status_code=503,
        detail="Coming soon — payment integration in progress. "
        "Wallet top-up will be available once Stripe is connected.",
    )


# ---------------------------------------------------------------------------
# Tier upgrade/downgrade
# ---------------------------------------------------------------------------


@router.post("/{org_id}/tier")
async def change_org_tier(
    org_id: uuid.UUID,
    body: dict,
    caller_org: Organization | None = Depends(resolve_org_or_admin),
):
    """Change an organization's tier. Admin or org owner.

    Body: {"tier": "pro"}

    NOTE: Upgrades to paid tiers currently disabled — Stripe integration coming soon.
    """
    if caller_org and caller_org.id != org_id:
        raise HTTPException(status_code=403, detail="Access denied to this organization")

    new_tier = body.get("tier", "")
    if new_tier not in TIER_LIMITS:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid tier: {new_tier}. Must be one of: {', '.join(TIER_LIMITS.keys())}",
        )

    # Block upgrades to paid tiers until Stripe is integrated
    if new_tier in ("pro", "enterprise"):
        raise HTTPException(
            status_code=503,
            detail="Coming soon — paid tiers will be available once Stripe is connected.",
        )

    async with async_session() as session:
        org = await session.get(Organization, org_id)
        if not org:
            raise HTTPException(status_code=404, detail="Organization not found")

        old_tier = org.tier
        if old_tier == new_tier:
            raise HTTPException(
                status_code=400,
                detail=f"Already on tier: {new_tier}",
            )

        org.tier = new_tier
        await session.commit()
        await session.refresh(org)

    new_limits = TIER_LIMITS[new_tier]
    return {
        "org_id": str(org_id),
        "org_name": org.name,
        "old_tier": old_tier,
        "new_tier": new_tier,
        "tier_limits": new_limits,
    }


# ---------------------------------------------------------------------------
# Transactions
# ---------------------------------------------------------------------------


@router.get("/{org_id}/transactions")
async def list_org_transactions(
    org_id: uuid.UUID,
    caller_org: Organization | None = Depends(resolve_org_or_admin),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    role: str = Query(default="all", pattern="^(all|payer|receiver)$"),
):
    """List transactions for an organization. Filter by role (payer/receiver/all)."""
    if caller_org and caller_org.id != org_id:
        raise HTTPException(status_code=403, detail="Access denied to this organization")

    async with async_session() as session:
        org = await session.get(Organization, org_id)
        if not org:
            raise HTTPException(status_code=404, detail="Organization not found")

        if role == "payer":
            query = select(Transaction).where(Transaction.payer_org_id == org_id)
        elif role == "receiver":
            query = select(Transaction).where(Transaction.receiver_org_id == org_id)
        else:
            query = select(Transaction).where(
                (Transaction.payer_org_id == org_id)
                | (Transaction.receiver_org_id == org_id)
            )

        query = query.order_by(Transaction.created_at.desc()).offset(offset).limit(limit)
        result = await session.execute(query)
        txns = result.scalars().all()

    return [
        {
            "id": str(tx.id),
            "payer_org_id": str(tx.payer_org_id),
            "receiver_org_id": str(tx.receiver_org_id) if tx.receiver_org_id else None,
            "agent_id": str(tx.agent_id),
            "agent_name": tx.agent_name,
            "amount": tx.amount,
            "fee": tx.fee,
            "net": tx.net,
            "tx_type": tx.tx_type,
            "task_id": tx.task_id,
            "created_at": tx.created_at.isoformat(),
        }
        for tx in txns
    ]


@router.get("/{org_id}/transactions/summary")
async def get_org_transaction_summary(
    org_id: uuid.UUID,
    caller_org: Organization | None = Depends(resolve_org_or_admin),
):
    """Get transaction summary: total spent, total earned, total fees."""
    if caller_org and caller_org.id != org_id:
        raise HTTPException(status_code=403, detail="Access denied to this organization")

    async with async_session() as session:
        org = await session.get(Organization, org_id)
        if not org:
            raise HTTPException(status_code=404, detail="Organization not found")

        # Total spent (as payer)
        spent_result = await session.execute(
            select(
                func.coalesce(func.sum(Transaction.amount), 0).label("total_spent"),
                func.count(Transaction.id).label("tx_count"),
            ).where(Transaction.payer_org_id == org_id)
        )
        spent_row = spent_result.one()

        # Total earned (as receiver)
        earned_result = await session.execute(
            select(
                func.coalesce(func.sum(Transaction.net), 0).label("total_earned"),
                func.coalesce(func.sum(Transaction.fee), 0).label("total_fees"),
                func.count(Transaction.id).label("tx_count"),
            ).where(Transaction.receiver_org_id == org_id)
        )
        earned_row = earned_result.one()

    return {
        "org_id": str(org_id),
        "org_name": org.name,
        "balance": round(org.balance, 4),
        "total_spent": round(float(spent_row.total_spent), 4),
        "total_earned": round(float(earned_row.total_earned), 4),
        "total_fees_paid": round(float(earned_row.total_fees), 4),
        "transactions_as_payer": spent_row.tx_count,
        "transactions_as_receiver": earned_row.tx_count,
    }
