"""Admin panel API routes — JWT-based username/password auth."""

import hashlib
import hmac
import json
import time

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import cast, func, select
from sqlalchemy.types import Date

from agentgate.core.config import settings
from agentgate.db.engine import async_session
from agentgate.db.models import Agent, Organization, TaskLog, Transaction

router = APIRouter(prefix="/admin/api", tags=["admin"])

TOKEN_EXPIRY = 86400  # 24 hours


# ---------------------------------------------------------------------------
# Token helpers (HMAC-SHA256, no external deps)
# ---------------------------------------------------------------------------


def _make_token(username: str) -> str:
    payload = json.dumps({"sub": username, "exp": int(time.time()) + TOKEN_EXPIRY})
    sig = hmac.new(settings.secret_key.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}|{sig}"


def _verify_token(token: str) -> str:
    try:
        payload_str, sig = token.rsplit("|", 1)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid token")
    expected = hmac.new(
        settings.secret_key.encode(), payload_str.encode(), hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(sig, expected):
        raise HTTPException(status_code=401, detail="Invalid token")
    payload = json.loads(payload_str)
    if payload.get("exp", 0) < time.time():
        raise HTTPException(status_code=401, detail="Token expired")
    return payload["sub"]


def _get_admin_user(request: Request) -> str:
    auth = request.headers.get("authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing authorization header")
    return _verify_token(auth[7:])


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------


@router.post("/login")
async def admin_login(request: Request):
    from agentgate.server.ratelimit import auth_limiter

    client_ip = request.client.host if request.client else "unknown"
    if not auth_limiter.allow(f"admin_login:{client_ip}"):
        raise HTTPException(status_code=429, detail="Too many login attempts. Try again later.")

    body = await request.json()
    username = body.get("username", "")
    password = body.get("password", "")
    if username != settings.admin_username or password != settings.admin_password:
        raise HTTPException(status_code=401, detail="Invalid credentials")
    return {"token": _make_token(username)}


# ---------------------------------------------------------------------------
# Dashboard KPIs
# ---------------------------------------------------------------------------


@router.get("/dashboard")
async def admin_dashboard(_user: str = Depends(_get_admin_user)):
    async with async_session() as session:
        # Totals
        org_count = (await session.execute(select(func.count(Organization.id)))).scalar() or 0
        agent_count = (await session.execute(select(func.count(Agent.id)))).scalar() or 0
        deployed_count = (await session.execute(
            select(func.count(Agent.id)).where(Agent.deployed.is_(True))
        )).scalar() or 0
        tx_count = (await session.execute(select(func.count(Transaction.id)))).scalar() or 0
        revenue_total = (await session.execute(
            select(func.coalesce(func.sum(Transaction.fee), 0))
        )).scalar() or 0
        task_count = (await session.execute(select(func.count(TaskLog.id)))).scalar() or 0

        # Today
        today = func.current_date()
        signups_today = (await session.execute(
            select(func.count(Organization.id)).where(
                cast(Organization.created_at, Date) == today
            )
        )).scalar() or 0
        tasks_today = (await session.execute(
            select(func.count(TaskLog.id)).where(
                cast(TaskLog.created_at, Date) == today
            )
        )).scalar() or 0
        revenue_today = (await session.execute(
            select(func.coalesce(func.sum(Transaction.fee), 0)).where(
                cast(Transaction.created_at, Date) == today
            )
        )).scalar() or 0

        # Signup trend (last 30 days)
        signup_trend = (await session.execute(
            select(
                cast(Organization.created_at, Date).label("day"),
                func.count(Organization.id).label("count"),
            )
            .group_by("day")
            .order_by("day")
            .limit(30)
        )).all()

        # Tasks trend (last 30 days)
        tasks_trend = (await session.execute(
            select(
                cast(TaskLog.created_at, Date).label("day"),
                func.count(TaskLog.id).label("count"),
            )
            .group_by("day")
            .order_by("day")
            .limit(30)
        )).all()

        # Tier breakdown
        tier_breakdown = (await session.execute(
            select(
                Organization.tier,
                func.count(Organization.id).label("count"),
            )
            .group_by(Organization.tier)
        )).all()

    return {
        "total_users": org_count,
        "total_agents": agent_count,
        "deployed_agents": deployed_count,
        "total_transactions": tx_count,
        "total_revenue": round(float(revenue_total), 4),
        "total_tasks": task_count,
        "signups_today": signups_today,
        "tasks_today": tasks_today,
        "revenue_today": round(float(revenue_today), 4),
        "signup_trend": [{"day": str(r.day), "count": r.count} for r in signup_trend],
        "tasks_trend": [{"day": str(r.day), "count": r.count} for r in tasks_trend],
        "tier_breakdown": {r.tier: r.count for r in tier_breakdown},
    }


# ---------------------------------------------------------------------------
# Users (Organizations)
# ---------------------------------------------------------------------------


@router.get("/users")
async def admin_list_users(
    q: str = "",
    tier: str = "",
    _user: str = Depends(_get_admin_user),
):
    async with async_session() as session:
        query = select(Organization).order_by(Organization.created_at.desc())
        if q:
            query = query.where(Organization.name.ilike(f"%{q}%"))
        if tier:
            query = query.where(Organization.tier == tier)
        result = await session.execute(query)
        orgs = result.scalars().all()

        # Count agents per org
        agent_counts = {}
        if orgs:
            org_ids = [o.id for o in orgs]
            rows = (await session.execute(
                select(Agent.org_id, func.count(Agent.id).label("cnt"))
                .where(Agent.org_id.in_(org_ids))
                .group_by(Agent.org_id)
            )).all()
            agent_counts = {r.org_id: r.cnt for r in rows}

    return [
        {
            "id": str(o.id),
            "name": o.name,
            "email": o.email,
            "tier": o.tier,
            "balance": round(o.balance, 4),
            "rate_limit": o.rate_limit,
            "rate_burst": o.rate_burst,
            "agent_count": agent_counts.get(o.id, 0),
            "created_at": o.created_at.isoformat() if o.created_at else None,
        }
        for o in orgs
    ]


@router.get("/users/{org_id}")
async def admin_get_user(org_id: str, _user: str = Depends(_get_admin_user)):
    async with async_session() as session:
        org = (await session.execute(
            select(Organization).where(Organization.id == org_id)
        )).scalar_one_or_none()
        if not org:
            raise HTTPException(status_code=404, detail="Organization not found")

        # Agents
        agents = (await session.execute(
            select(Agent).where(Agent.org_id == org.id).order_by(Agent.created_at.desc())
        )).scalars().all()

        # Recent transactions
        txs = (await session.execute(
            select(Transaction)
            .where(
                (Transaction.payer_org_id == org.id)
                | (Transaction.receiver_org_id == org.id)
            )
            .order_by(Transaction.created_at.desc())
            .limit(50)
        )).scalars().all()

        # Billing summary
        spent = (await session.execute(
            select(func.coalesce(func.sum(Transaction.amount), 0))
            .where(Transaction.payer_org_id == org.id)
        )).scalar() or 0
        earned = (await session.execute(
            select(func.coalesce(func.sum(Transaction.net), 0))
            .where(Transaction.receiver_org_id == org.id)
        )).scalar() or 0

    return {
        "id": str(org.id),
        "name": org.name,
        "email": org.email,
        "tier": org.tier,
        "balance": round(org.balance, 4),
        "rate_limit": org.rate_limit,
        "rate_burst": org.rate_burst,
        "cost_per_invocation": org.cost_per_invocation,
        "created_at": org.created_at.isoformat() if org.created_at else None,
        "total_spent": round(float(spent), 4),
        "total_earned": round(float(earned), 4),
        "agents": [
            {
                "id": str(a.id),
                "name": a.name,
                "version": a.version,
                "deployed": a.deployed,
                "price_per_task": a.price_per_task,
                "created_at": a.created_at.isoformat() if a.created_at else None,
            }
            for a in agents
        ],
        "transactions": [
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
        ],
    }


@router.put("/users/{org_id}")
async def admin_update_user(org_id: str, request: Request, _user: str = Depends(_get_admin_user)):
    body = await request.json()
    async with async_session() as session:
        org = (await session.execute(
            select(Organization).where(Organization.id == org_id)
        )).scalar_one_or_none()
        if not org:
            raise HTTPException(status_code=404, detail="Organization not found")

        allowed = {"tier", "balance", "rate_limit", "rate_burst"}
        for key in allowed:
            if key in body:
                setattr(org, key, body[key])
        await session.commit()
        await session.refresh(org)

    return {"message": "Updated", "id": str(org.id)}


@router.post("/users/{org_id}/reset-key")
async def admin_reset_user_key(
    org_id: str, _user: str = Depends(_get_admin_user),
):
    """Generate a new API key for an organization. Returns the key once."""
    import secrets

    from agentgate.server.auth import hash_api_key

    async with async_session() as session:
        org = (await session.execute(
            select(Organization).where(Organization.id == org_id)
        )).scalar_one_or_none()
        if not org:
            raise HTTPException(status_code=404, detail="Organization not found")

        new_key = secrets.token_urlsafe(32)
        org.api_key_hash = hash_api_key(new_key)
        org.secondary_api_key_hash = None
        await session.commit()

    return {
        "message": "API key reset successfully",
        "api_key": new_key,
        "note": "Save this key — it won't be shown again.",
    }


@router.delete("/users/{org_id}")
async def admin_delete_user(org_id: str, _user: str = Depends(_get_admin_user)):
    async with async_session() as session:
        org = (await session.execute(
            select(Organization).where(Organization.id == org_id)
        )).scalar_one_or_none()
        if not org:
            raise HTTPException(status_code=404, detail="Organization not found")
        await session.delete(org)
        await session.commit()
    return {"message": "Deleted", "id": org_id}


# ---------------------------------------------------------------------------
# Agents
# ---------------------------------------------------------------------------


@router.get("/agents")
async def admin_list_agents(_user: str = Depends(_get_admin_user)):
    async with async_session() as session:
        agents = (await session.execute(
            select(Agent).order_by(Agent.created_at.desc())
        )).scalars().all()

        # Get org names
        org_ids = {a.org_id for a in agents if a.org_id}
        org_names = {}
        if org_ids:
            rows = (await session.execute(
                select(Organization.id, Organization.name).where(Organization.id.in_(org_ids))
            )).all()
            org_names = {r.id: r.name for r in rows}

    return [
        {
            "id": str(a.id),
            "name": a.name,
            "description": a.description,
            "version": a.version,
            "url": a.url,
            "deployed": a.deployed,
            "price_per_task": a.price_per_task,
            "org_id": str(a.org_id) if a.org_id else None,
            "org_name": org_names.get(a.org_id, "—"),
            "tags": a.tags or [],
            "created_at": a.created_at.isoformat() if a.created_at else None,
        }
        for a in agents
    ]


@router.delete("/agents/{agent_id}")
async def admin_delete_agent(agent_id: str, _user: str = Depends(_get_admin_user)):
    async with async_session() as session:
        agent = (await session.execute(
            select(Agent).where(Agent.id == agent_id)
        )).scalar_one_or_none()
        if not agent:
            raise HTTPException(status_code=404, detail="Agent not found")
        await session.delete(agent)
        await session.commit()
    return {"message": "Deleted", "id": agent_id}


# ---------------------------------------------------------------------------
# Transactions
# ---------------------------------------------------------------------------


@router.get("/transactions")
async def admin_list_transactions(
    limit: int = 100,
    offset: int = 0,
    _user: str = Depends(_get_admin_user),
):
    async with async_session() as session:
        total = (await session.execute(select(func.count(Transaction.id)))).scalar() or 0
        txs = (await session.execute(
            select(Transaction)
            .order_by(Transaction.created_at.desc())
            .offset(offset)
            .limit(limit)
        )).scalars().all()

        # Resolve org names
        org_ids = set()
        for t in txs:
            org_ids.add(t.payer_org_id)
            if t.receiver_org_id:
                org_ids.add(t.receiver_org_id)
        org_names = {}
        if org_ids:
            rows = (await session.execute(
                select(Organization.id, Organization.name).where(Organization.id.in_(org_ids))
            )).all()
            org_names = {r.id: r.name for r in rows}

    return {
        "total": total,
        "transactions": [
            {
                "id": str(t.id),
                "payer": org_names.get(t.payer_org_id, str(t.payer_org_id)),
                "receiver": org_names.get(t.receiver_org_id, "—") if t.receiver_org_id else "—",
                "agent_name": t.agent_name,
                "amount": round(t.amount, 4),
                "fee": round(t.fee, 4),
                "net": round(t.net, 4),
                "tx_type": t.tx_type,
                "created_at": t.created_at.isoformat() if t.created_at else None,
            }
            for t in txs
        ],
    }
