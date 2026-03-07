"""UCP (Universal Commerce Protocol) routes.

Implements:
- /.well-known/ucp profile discovery
- Checkout sessions for paid agents (create / get / complete)
"""

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select

from agentgate import __version__
from agentgate.db.engine import async_session
from agentgate.db.models import Agent, Organization, Transaction

router = APIRouter(prefix="/ucp", tags=["ucp"])
bearer_scheme = HTTPBearer(auto_error=False)

# UCP spec version we implement
UCP_VERSION = "2026-03-01"

# In-memory checkout sessions (in production, this would be in DB/Redis)
_checkout_sessions: dict[str, dict] = {}


def get_ucp_profile() -> dict:
    """Return the UCP discovery profile for AgentGate."""
    return {
        "ucp": {
            "version": UCP_VERSION,
            "services": {
                "dev.ucp.shopping": {
                    "version": UCP_VERSION,
                    "spec": "https://ucp.dev/specs/shopping/2026-03-01",
                    "rest": {
                        "schema": "https://agentgate.sh/docs",
                        "endpoint": "https://agentgate.sh/ucp",
                    },
                    "a2a": {
                        "endpoint": "https://agentgate.sh/.well-known/agent.json",
                    },
                },
            },
            "capabilities": [
                {
                    "name": "dev.ucp.shopping.checkout",
                    "version": UCP_VERSION,
                    "spec": "https://ucp.dev/specs/shopping/checkout/2026-03-01",
                    "description": "Create checkout sessions for paid AI agent tasks",
                },
                {
                    "name": "dev.ucp.shopping.catalog",
                    "version": UCP_VERSION,
                    "spec": "https://ucp.dev/specs/shopping/catalog/2026-03-01",
                    "description": "Browse paid agents as products",
                },
            ],
            "payment": {
                "methods": ["wallet"],
                "currency": "USD",
            },
        },
        "platform": {
            "name": "AgentGate",
            "version": __version__,
            "url": "https://agentgate.sh",
        },
    }


@router.get("/catalog")
async def ucp_catalog():
    """UCP catalog: list paid agents as products."""
    async with async_session() as session:
        result = await session.execute(
            select(Agent).where(Agent.price_per_task > 0).order_by(Agent.name)
        )
        agents = result.scalars().all()

    return {
        "ucp": {"version": UCP_VERSION, "capability": "dev.ucp.shopping.catalog"},
        "products": [
            {
                "id": str(a.id),
                "name": a.name,
                "description": a.description,
                "price": {"amount": a.price_per_task, "currency": "USD", "unit": "per_task"},
                "category": "ai_agent",
                "tags": a.tags or [],
                "url": f"https://agentgate.sh/agents/{a.id}/card",
            }
            for a in agents
        ],
        "total": len(agents),
    }


@router.post("/checkout")
async def create_checkout_session(
    body: dict,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
):
    """Create a UCP checkout session for a paid agent task.

    Body: {"agent_id": "...", "task": {...}}
    Returns a checkout session with payment info.
    """
    agent_id = body.get("agent_id")
    task = body.get("task")
    if not agent_id:
        raise HTTPException(status_code=400, detail="agent_id is required")
    if not task:
        raise HTTPException(status_code=400, detail="task is required")

    try:
        agent_uuid = uuid.UUID(agent_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid agent_id format")

    async with async_session() as session:
        agent = await session.get(Agent, agent_uuid)
        if not agent:
            raise HTTPException(status_code=404, detail="Agent not found")

    if agent.price_per_task <= 0:
        raise HTTPException(
            status_code=400,
            detail="Agent is free — no checkout needed",
        )

    # Resolve caller org
    caller_org = None
    if credentials:
        import hashlib
        key_hash = hashlib.sha256(credentials.credentials.encode()).hexdigest()
        async with async_session() as session:
            result = await session.execute(
                select(Organization).where(Organization.api_key_hash == key_hash)
            )
            caller_org = result.scalar_one_or_none()

    session_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)

    checkout = {
        "session_id": session_id,
        "status": "pending",
        "agent_id": str(agent.id),
        "agent_name": agent.name,
        "amount": agent.price_per_task,
        "currency": "USD",
        "payer_org_id": str(caller_org.id) if caller_org else None,
        "payer_org_name": caller_org.name if caller_org else None,
        "payer_balance": round(caller_org.balance, 4) if caller_org else None,
        "task": task,
        "created_at": now.isoformat(),
        "updated_at": now.isoformat(),
        "ucp": {
            "version": UCP_VERSION,
            "capability": "dev.ucp.shopping.checkout",
        },
    }

    # Pre-check balance
    if caller_org and caller_org.balance < agent.price_per_task:
        checkout["status"] = "insufficient_funds"
        checkout["error"] = (
            f"Balance {caller_org.balance:.4f} < {agent.price_per_task:.4f}"
        )
        _checkout_sessions[session_id] = checkout
        raise HTTPException(status_code=402, detail=checkout)

    _checkout_sessions[session_id] = checkout
    return checkout


@router.get("/checkout/{session_id}")
async def get_checkout_session(session_id: str):
    """Get the status of a UCP checkout session."""
    checkout = _checkout_sessions.get(session_id)
    if not checkout:
        raise HTTPException(status_code=404, detail="Checkout session not found")
    return checkout


@router.post("/checkout/{session_id}/complete")
async def complete_checkout_session(
    session_id: str,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
):
    """Complete a UCP checkout session: execute the task and process billing.

    This triggers the actual A2A task routing + billing in one step.
    """
    import httpx

    from agentgate.server.routes import TIER_FEE_PCT

    checkout = _checkout_sessions.get(session_id)
    if not checkout:
        raise HTTPException(status_code=404, detail="Checkout session not found")

    if checkout["status"] == "completed":
        raise HTTPException(status_code=400, detail="Session already completed")
    if checkout["status"] == "insufficient_funds":
        raise HTTPException(status_code=402, detail="Insufficient funds")

    agent_uuid = uuid.UUID(checkout["agent_id"])

    async with async_session() as session:
        agent = await session.get(Agent, agent_uuid)
        if not agent:
            raise HTTPException(status_code=404, detail="Agent not found")

    # Resolve payer org
    payer_org = None
    if checkout["payer_org_id"]:
        async with async_session() as session:
            payer_org = await session.get(
                Organization, uuid.UUID(checkout["payer_org_id"]),
            )

    # Re-check balance
    if payer_org and payer_org.balance < agent.price_per_task:
        checkout["status"] = "insufficient_funds"
        checkout["updated_at"] = datetime.now(timezone.utc).isoformat()
        raise HTTPException(
            status_code=402,
            detail=f"Insufficient balance: {payer_org.balance:.4f} < {agent.price_per_task:.4f}",
        )

    # Execute A2A task
    target_url = f"{agent.url.rstrip('/')}/a2a"
    task = checkout["task"]

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(target_url, json=task)
    except (httpx.ConnectError, httpx.TimeoutException) as exc:
        checkout["status"] = "failed"
        checkout["error"] = str(exc)
        checkout["updated_at"] = datetime.now(timezone.utc).isoformat()
        raise HTTPException(status_code=502, detail=f"Agent unreachable: {exc}")

    if resp.status_code >= 400:
        checkout["status"] = "failed"
        checkout["error"] = f"Agent returned {resp.status_code}"
        checkout["updated_at"] = datetime.now(timezone.utc).isoformat()
        raise HTTPException(
            status_code=resp.status_code,
            detail=f"Agent error: {resp.text}",
        )

    # Process billing (charge after success)
    if payer_org and agent.price_per_task > 0:
        fee_pct = TIER_FEE_PCT.get(payer_org.tier, 0.03)
        fee = round(agent.price_per_task * fee_pct, 6)
        net = round(agent.price_per_task - fee, 6)

        async with async_session() as session:
            payer = await session.get(Organization, payer_org.id)
            payer.balance = round(payer.balance - agent.price_per_task, 4)

            receiver_org_id = None
            if agent.org_id and agent.org_id != payer_org.id:
                receiver = await session.get(Organization, agent.org_id)
                if receiver:
                    receiver.balance = round(receiver.balance + net, 4)
                    receiver_org_id = receiver.id

            tx = Transaction(
                payer_org_id=payer_org.id,
                receiver_org_id=receiver_org_id,
                agent_id=agent.id,
                agent_name=agent.name,
                amount=agent.price_per_task,
                fee=fee,
                net=net,
                tx_type="ucp_checkout",
                task_id=task.get("id"),
            )
            session.add(tx)
            await session.commit()

            checkout["transaction_id"] = str(tx.id)
            checkout["fee"] = fee
            checkout["net"] = net
            checkout["payer_new_balance"] = round(payer.balance, 4)

    # Mark completed
    checkout["status"] = "completed"
    checkout["result"] = resp.json()
    checkout["updated_at"] = datetime.now(timezone.utc).isoformat()

    return checkout
