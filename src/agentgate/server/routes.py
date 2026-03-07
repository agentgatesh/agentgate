import asyncio
import json
import logging
import uuid

import httpx
from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    HTTPException,
    Query,
    Request,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy import case, cast, desc, func, select
from sqlalchemy.types import Date
from sse_starlette.sse import EventSourceResponse

from agentgate.core.config import settings
from agentgate.db.engine import async_session
from agentgate.db.models import Agent, Organization, Review, TaskLog, Transaction
from agentgate.server.auth import bearer_scheme, bearer_scheme_optional, hash_api_key
from agentgate.server.healthcheck import get_agent_health
from agentgate.server.metrics import Timer, record_request
from agentgate.server.plugins import plugin_manager
from agentgate.server.ratelimit import RateLimiter, task_limiter
from agentgate.server.schemas import (
    AgentCard,
    AgentCreate,
    AgentResponse,
    AgentUpdate,
    ReviewCreate,
    ReviewResponse,
)

logger = logging.getLogger("agentgate.routing")

router = APIRouter(prefix="/agents", tags=["agents"])


async def verify_api_key_or_org(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
) -> Organization | None:
    """Check admin key or org key. Returns org if org-scoped, None if admin."""
    if settings.api_key and credentials.credentials == settings.api_key:
        return None  # Admin
    # Try org key
    key_hash = hash_api_key(credentials.credentials)
    async with async_session() as session:
        result = await session.execute(
            select(Organization).where(Organization.api_key_hash == key_hash)
        )
        org = result.scalar_one_or_none()
        if org:
            return org
    raise HTTPException(status_code=401, detail="Invalid API key")


@router.post("/", response_model=AgentResponse, status_code=201)
async def register_agent(
    data: AgentCreate,
    caller_org: Organization | None = Depends(verify_api_key_or_org),
):
    # If org key is used, force org_id to the caller's org
    org_id = caller_org.id if caller_org else data.org_id
    async with async_session() as session:
        agent = Agent(
            name=data.name,
            description=data.description,
            url=data.url,
            version=data.version,
            skills=data.skills,
            tags=data.tags,
            webhook_url=data.webhook_url,
            price_per_task=data.price_per_task,
            org_id=org_id,
            api_key_hash=hash_api_key(data.agent_api_key) if data.agent_api_key else None,
        )
        session.add(agent)
        await session.commit()
        await session.refresh(agent)
        return agent


@router.get("/", response_model=list[AgentResponse])
async def list_agents(
    skill: str | None = None,
    tag: str | None = Query(default=None),
):
    async with async_session() as session:
        query = select(Agent).order_by(Agent.created_at.desc())
        result = await session.execute(query)
        agents = result.scalars().all()
        if skill:
            skill_lower = skill.lower()
            agents = [
                a for a in agents
                if any(
                    skill_lower in s.get("id", "").lower()
                    or skill_lower in s.get("name", "").lower()
                    for s in (a.skills or [])
                )
            ]
        if tag:
            tag_lower = tag.lower()
            agents = [
                a for a in agents
                if any(tag_lower == t.lower() for t in (a.tags or []))
            ]
        return agents


@router.get("/tags")
async def list_tags():
    """List all unique tags across all agents."""
    async with async_session() as session:
        result = await session.execute(select(Agent).order_by(Agent.created_at.desc()))
        agents = result.scalars().all()
    tags: dict[str, int] = {}
    for agent in agents:
        for t in agent.tags or []:
            tags[t] = tags.get(t, 0) + 1
    return {"tags": [{"name": k, "count": v} for k, v in sorted(tags.items())]}


@router.get("/search")
async def search_agents(
    q: str | None = Query(default=None, description="Full-text search query"),
    tags: str | None = Query(default=None, description="Comma-separated tags (AND logic)"),
    skill: str | None = Query(default=None, description="Filter by skill id or name"),
    sort: str | None = Query(default="newest", pattern="^(newest|name|version|rating)$"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    """Advanced agent search with full-text, multi-tag, and sorting.

    - q: search name, description, skills, and tags
    - tags: comma-separated list (all must match)
    - skill: filter by skill id or name
    - sort: newest (default), name, version, rating
    """
    async with async_session() as session:
        query = select(Agent).order_by(Agent.created_at.desc())
        result = await session.execute(query)
        agents = list(result.scalars().all())

        # Fetch review stats for all agents
        review_result = await session.execute(
            select(
                Review.agent_id,
                func.count(Review.id).label("review_count"),
                func.avg(Review.rating).label("avg_rating"),
            ).group_by(Review.agent_id)
        )
        review_stats = {
            row.agent_id: {
                "review_count": row.review_count,
                "avg_rating": round(float(row.avg_rating), 2),
            }
            for row in review_result.all()
        }

    # Full-text search across name, description, skills, tags
    if q:
        q_lower = q.lower()
        agents = [
            a for a in agents
            if q_lower in a.name.lower()
            or q_lower in (a.description or "").lower()
            or any(
                q_lower in s.get("id", "").lower()
                or q_lower in s.get("name", "").lower()
                or q_lower in s.get("description", "").lower()
                for s in (a.skills or [])
            )
            or any(q_lower in t.lower() for t in (a.tags or []))
        ]

    # Multi-tag filter (AND logic)
    if tags:
        required_tags = [t.strip().lower() for t in tags.split(",") if t.strip()]
        agents = [
            a for a in agents
            if all(
                any(rt == t.lower() for t in (a.tags or []))
                for rt in required_tags
            )
        ]

    # Skill filter
    if skill:
        skill_lower = skill.lower()
        agents = [
            a for a in agents
            if any(
                skill_lower in s.get("id", "").lower()
                or skill_lower in s.get("name", "").lower()
                for s in (a.skills or [])
            )
        ]

    # Sorting
    if sort == "name":
        agents.sort(key=lambda a: a.name.lower())
    elif sort == "version":
        agents.sort(key=lambda a: a.version, reverse=True)
    elif sort == "rating":
        agents.sort(
            key=lambda a: review_stats.get(a.id, {}).get("avg_rating", 0),
            reverse=True,
        )

    total = len(agents)
    agents = agents[offset:offset + limit]

    return {
        "total": total,
        "offset": offset,
        "limit": limit,
        "agents": [
            {
                "id": str(a.id),
                "name": a.name,
                "description": a.description,
                "url": a.url,
                "version": a.version,
                "skills": a.skills,
                "tags": a.tags or [],
                "org_id": str(a.org_id) if a.org_id else None,
                "avg_rating": review_stats.get(a.id, {}).get("avg_rating"),
                "review_count": review_stats.get(a.id, {}).get("review_count", 0),
                "created_at": a.created_at.isoformat(),
                "updated_at": a.updated_at.isoformat(),
            }
            for a in agents
        ],
    }


@router.get("/by-name/{name}", response_model=list[AgentResponse])
async def get_agent_versions(
    name: str,
    version: str | None = Query(default=None),
):
    """Get all versions of an agent by name. Optionally filter by version."""
    async with async_session() as session:
        query = select(Agent).where(Agent.name == name).order_by(Agent.created_at.desc())
        if version:
            query = select(Agent).where(
                Agent.name == name, Agent.version == version,
            ).order_by(Agent.created_at.desc())
        result = await session.execute(query)
        agents = result.scalars().all()
        if not agents:
            raise HTTPException(status_code=404, detail="No agents found with this name")
        return agents


@router.get("/by-name/{name}/latest", response_model=AgentResponse)
async def get_agent_latest(name: str):
    """Get the latest version of an agent by name (most recently created)."""
    async with async_session() as session:
        result = await session.execute(
            select(Agent).where(Agent.name == name).order_by(Agent.created_at.desc()).limit(1)
        )
        agent = result.scalar_one_or_none()
        if not agent:
            raise HTTPException(status_code=404, detail="No agent found with this name")
        return agent


@router.get("/{agent_id}", response_model=AgentResponse)
async def get_agent(agent_id: uuid.UUID):
    async with async_session() as session:
        agent = await session.get(Agent, agent_id)
        if not agent:
            raise HTTPException(status_code=404, detail="Agent not found")
        return agent


@router.put("/{agent_id}", response_model=AgentResponse)
async def update_agent(
    agent_id: uuid.UUID,
    data: AgentUpdate,
    caller_org: Organization | None = Depends(verify_api_key_or_org),
):
    async with async_session() as session:
        agent = await session.get(Agent, agent_id)
        if not agent:
            raise HTTPException(status_code=404, detail="Agent not found")
        # Org can only update its own agents
        if caller_org and agent.org_id != caller_org.id:
            raise HTTPException(status_code=403, detail="Access denied to this agent")
        update_data = data.model_dump(exclude_none=True)
        if "agent_api_key" in update_data:
            key = update_data.pop("agent_api_key")
            if key:
                agent.api_key_hash = hash_api_key(key)
            else:
                agent.api_key_hash = None
        for field, value in update_data.items():
            setattr(agent, field, value)
        await session.commit()
        await session.refresh(agent)
        return agent


@router.delete("/{agent_id}", status_code=204)
async def delete_agent(
    agent_id: uuid.UUID,
    caller_org: Organization | None = Depends(verify_api_key_or_org),
):
    async with async_session() as session:
        agent = await session.get(Agent, agent_id)
        if not agent:
            raise HTTPException(status_code=404, detail="Agent not found")
        if caller_org and agent.org_id != caller_org.id:
            raise HTTPException(status_code=403, detail="Access denied to this agent")
        await session.delete(agent)
        await session.commit()


@router.get("/{agent_id}/health")
async def agent_health(agent_id: uuid.UUID):
    async with async_session() as session:
        agent = await session.get(Agent, agent_id)
        if not agent:
            raise HTTPException(status_code=404, detail="Agent not found")
    health = get_agent_health(str(agent_id))
    if not health:
        return {"agent": agent.name, "status": "unknown", "message": "No health check yet"}
    return {"agent": agent.name, **health}


@router.get("/{agent_id}/card")
async def get_agent_card(agent_id: uuid.UUID):
    async with async_session() as session:
        agent = await session.get(Agent, agent_id)
        if not agent:
            raise HTTPException(status_code=404, detail="Agent not found")
        card = AgentCard(
            name=agent.name,
            description=agent.description,
            url=agent.url,
            version=agent.version,
            skills=agent.skills,
        ).model_dump()

        # Add UCP checkout capability for paid agents
        if agent.price_per_task > 0:
            from agentgate.server.ucp_routes import UCP_VERSION

            card["ucp"] = {
                "version": UCP_VERSION,
                "capabilities": ["dev.ucp.shopping.checkout"],
                "price_per_task": agent.price_per_task,
                "currency": "USD",
                "checkout_url": "https://agentgate.sh/ucp/checkout",
            }

        return card


@router.get("/{agent_id}/logs")
async def get_agent_logs(
    agent_id: uuid.UUID,
    caller_org: Organization | None = Depends(verify_api_key_or_org),
    limit: int = Query(default=50, le=200),
    offset: int = Query(default=0, ge=0),
):
    """Get invocation logs for an agent. Requires API key (admin or org)."""
    async with async_session() as session:
        agent = await session.get(Agent, agent_id)
        if not agent:
            raise HTTPException(status_code=404, detail="Agent not found")
        if caller_org and agent.org_id != caller_org.id:
            raise HTTPException(status_code=403, detail="Access denied to this agent")
        query = (
            select(TaskLog)
            .where(TaskLog.agent_id == agent_id)
            .order_by(desc(TaskLog.created_at))
            .offset(offset)
            .limit(limit)
        )
        result = await session.execute(query)
        logs = result.scalars().all()
    return [
        {
            "id": str(log.id),
            "agent_id": str(log.agent_id),
            "agent_name": log.agent_name,
            "caller_ip": log.caller_ip,
            "task_id": log.task_id,
            "status": log.status,
            "error_detail": log.error_detail,
            "latency_ms": round(log.latency_ms, 1),
            "created_at": log.created_at.isoformat(),
        }
        for log in logs
    ]


@router.get("/{agent_id}/usage")
async def get_agent_usage(
    agent_id: uuid.UUID,
    caller_org: Organization | None = Depends(verify_api_key_or_org),
):
    """Get usage stats for an agent. Requires API key (admin or org)."""
    async with async_session() as session:
        agent = await session.get(Agent, agent_id)
        if not agent:
            raise HTTPException(status_code=404, detail="Agent not found")
        if caller_org and agent.org_id != caller_org.id:
            raise HTTPException(status_code=403, detail="Access denied to this agent")
        result = await session.execute(
            select(
                func.count(TaskLog.id).label("total_invocations"),
                func.count(TaskLog.id).filter(TaskLog.status == "error").label("total_errors"),
                func.avg(TaskLog.latency_ms).label("avg_latency_ms"),
                func.max(TaskLog.created_at).label("last_invocation"),
            ).where(TaskLog.agent_id == agent_id)
        )
        row = result.one()
    return {
        "agent_id": str(agent_id),
        "agent_name": agent.name,
        "total_invocations": row.total_invocations,
        "total_errors": row.total_errors,
        "avg_latency_ms": round(row.avg_latency_ms, 1) if row.avg_latency_ms else 0,
        "last_invocation": row.last_invocation.isoformat() if row.last_invocation else None,
    }


@router.get("/{agent_id}/usage/breakdown")
async def get_agent_usage_breakdown(
    agent_id: uuid.UUID,
    caller_org: Organization | None = Depends(verify_api_key_or_org),
    period: str = Query(default="day", pattern="^(day|month)$"),
    days: int = Query(default=30, ge=1, le=365),
):
    """Get usage breakdown by day or month. Requires API key (admin or org)."""
    from datetime import datetime, timedelta, timezone

    async with async_session() as session:
        agent = await session.get(Agent, agent_id)
        if not agent:
            raise HTTPException(status_code=404, detail="Agent not found")
        if caller_org and agent.org_id != caller_org.id:
            raise HTTPException(status_code=403, detail="Access denied to this agent")

        since = datetime.now(timezone.utc) - timedelta(days=days)

        if period == "day":
            date_col = cast(TaskLog.created_at, Date).label("period")
        else:
            date_col = func.date_trunc("month", TaskLog.created_at).label("period")

        query = (
            select(
                date_col,
                func.count(TaskLog.id).label("invocations"),
                func.count(
                    case((TaskLog.status == "error", TaskLog.id))
                ).label("errors"),
                func.avg(TaskLog.latency_ms).label("avg_latency_ms"),
            )
            .where(TaskLog.agent_id == agent_id, TaskLog.created_at >= since)
            .group_by("period")
            .order_by("period")
        )
        result = await session.execute(query)
        rows = result.all()

    return {
        "agent_id": str(agent_id),
        "agent_name": agent.name,
        "period": period,
        "days": days,
        "breakdown": [
            {
                "period": (
                    row.period.isoformat()
                    if hasattr(row.period, "isoformat") else str(row.period)
                ),
                "invocations": row.invocations,
                "errors": row.errors,
                "avg_latency_ms": round(row.avg_latency_ms, 1) if row.avg_latency_ms else 0,
            }
            for row in rows
        ],
    }


@router.post("/{agent_id}/reviews", response_model=ReviewResponse, status_code=201)
async def create_review(agent_id: uuid.UUID, data: ReviewCreate):
    """Submit a review for an agent (1-5 stars). No auth required."""
    async with async_session() as session:
        agent = await session.get(Agent, agent_id)
        if not agent:
            raise HTTPException(status_code=404, detail="Agent not found")
        review = Review(
            agent_id=agent_id,
            rating=data.rating,
            comment=data.comment,
            reviewer=data.reviewer,
        )
        session.add(review)
        await session.commit()
        await session.refresh(review)
        return review


@router.get("/{agent_id}/reviews", response_model=list[ReviewResponse])
async def list_reviews(
    agent_id: uuid.UUID,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    """Get reviews for an agent, newest first."""
    async with async_session() as session:
        agent = await session.get(Agent, agent_id)
        if not agent:
            raise HTTPException(status_code=404, detail="Agent not found")
        result = await session.execute(
            select(Review)
            .where(Review.agent_id == agent_id)
            .order_by(desc(Review.created_at))
            .offset(offset)
            .limit(limit)
        )
        return result.scalars().all()


@router.get("/{agent_id}/reviews/stats")
async def review_stats(agent_id: uuid.UUID):
    """Get aggregate review stats for an agent."""
    async with async_session() as session:
        agent = await session.get(Agent, agent_id)
        if not agent:
            raise HTTPException(status_code=404, detail="Agent not found")
        result = await session.execute(
            select(
                func.count(Review.id).label("review_count"),
                func.avg(Review.rating).label("avg_rating"),
                func.count(Review.id).filter(Review.rating == 5).label("five_star"),
                func.count(Review.id).filter(Review.rating == 4).label("four_star"),
                func.count(Review.id).filter(Review.rating == 3).label("three_star"),
                func.count(Review.id).filter(Review.rating == 2).label("two_star"),
                func.count(Review.id).filter(Review.rating == 1).label("one_star"),
            ).where(Review.agent_id == agent_id)
        )
        row = result.one()
    return {
        "agent_id": str(agent_id),
        "agent_name": agent.name,
        "review_count": row.review_count,
        "avg_rating": round(row.avg_rating, 2) if row.avg_rating else None,
        "distribution": {
            "5": row.five_star,
            "4": row.four_star,
            "3": row.three_star,
            "2": row.two_star,
            "1": row.one_star,
        },
    }


TIER_FEE_PCT = {"free": 0.03, "pro": 0.025, "enterprise": 0.02}


async def _process_billing(
    agent: Agent,
    payer_org: Organization | None,
    task_id_str: str | None,
):
    """Process billing for a paid agent task.

    - Deducts agent.price_per_task from payer org balance
    - Credits agent owner org (minus fee)
    - Records a Transaction in the ledger
    - Returns (charged, error_msg) — error_msg is set if insufficient funds
    """
    if agent.price_per_task <= 0:
        return True, None  # Free agent, nothing to charge

    if not payer_org:
        return True, None  # Admin key, no billing

    if payer_org.balance < agent.price_per_task:
        return False, (
            f"Insufficient balance: {payer_org.balance:.4f} < "
            f"{agent.price_per_task:.4f} (agent: {agent.name})"
        )

    fee_pct = TIER_FEE_PCT.get(payer_org.tier, 0.03)
    fee = round(agent.price_per_task * fee_pct, 6)
    net = round(agent.price_per_task - fee, 6)

    async with async_session() as session:
        # Deduct from payer
        payer = await session.get(Organization, payer_org.id)
        payer.balance = round(payer.balance - agent.price_per_task, 4)

        # Credit agent owner (if different from payer)
        receiver_org_id = None
        if agent.org_id and agent.org_id != payer_org.id:
            receiver = await session.get(Organization, agent.org_id)
            if receiver:
                receiver.balance = round(receiver.balance + net, 4)
                receiver_org_id = receiver.id

        # Record transaction
        tx = Transaction(
            payer_org_id=payer_org.id,
            receiver_org_id=receiver_org_id,
            agent_id=agent.id,
            agent_name=agent.name,
            amount=agent.price_per_task,
            fee=fee,
            net=net,
            tx_type="task",
            task_id=task_id_str,
        )
        session.add(tx)
        await session.commit()

    logger.info(
        "Billing: org %s charged %.4f for %s (fee: %.4f, net: %.4f)",
        payer_org.name, agent.price_per_task, agent.name, fee, net,
    )
    return True, None


async def _fire_webhook(
    webhook_url: str, payload: dict, max_retries: int = 3,
):
    """Fire webhook with exponential backoff retry."""
    for attempt in range(max_retries):
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(webhook_url, json=payload)
            if resp.status_code < 500:
                logger.info("Webhook sent to %s (attempt %d)", webhook_url, attempt + 1)
                return
            logger.warning(
                "Webhook %s returned %d (attempt %d/%d)",
                webhook_url, resp.status_code, attempt + 1, max_retries,
            )
        except Exception:
            logger.warning(
                "Webhook failed for %s (attempt %d/%d)",
                webhook_url, attempt + 1, max_retries,
            )
        if attempt < max_retries - 1:
            delay = 2 ** attempt  # 1s, 2s, 4s
            await asyncio.sleep(delay)
    logger.error("Webhook exhausted retries for %s", webhook_url)


async def _save_task_log(
    agent_id: uuid.UUID,
    agent_name: str,
    caller_ip: str,
    task_id: str | None,
    status: str,
    latency_ms: float,
    error_detail: str | None = None,
):
    """Save a task invocation log to the database."""
    try:
        async with async_session() as session:
            log = TaskLog(
                agent_id=agent_id,
                agent_name=agent_name,
                caller_ip=caller_ip,
                task_id=task_id,
                status=status,
                latency_ms=latency_ms,
                error_detail=error_detail,
            )
            session.add(log)
            await session.commit()
    except Exception:
        logger.warning("Failed to save task log for %s", agent_name)


@router.post("/{agent_id}/task")
async def route_task(
    agent_id: uuid.UUID, task: dict, request: Request,
    background_tasks: BackgroundTasks,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme_optional),
):
    """Route an A2A task to the target agent (proxy).

    Looks up the agent's URL in the registry and forwards the task payload
    to {agent_url}/a2a. Returns the agent's response directly.
    If the agent has an api_key_hash, a Bearer token is required.
    If the agent has a webhook_url configured, a notification is sent in the background.
    """
    client_ip = request.client.host if request.client else "unknown"

    async with async_session() as session:
        agent = await session.get(Agent, agent_id)
        if not agent:
            raise HTTPException(status_code=404, detail="Agent not found")

        # Per-org rate limiting (if agent belongs to an org)
        if agent.org_id:
            org = await session.get(Organization, agent.org_id)
            if org:
                org_limiter = RateLimiter(rate=org.rate_limit, burst=org.rate_burst)
                org_key = f"org:{org.id}:{client_ip}"
                if not org_limiter.allow(org_key):
                    raise HTTPException(status_code=429, detail="Too many requests")
            else:
                if not task_limiter.allow(client_ip):
                    raise HTTPException(status_code=429, detail="Too many requests")
        else:
            if not task_limiter.allow(client_ip):
                raise HTTPException(status_code=429, detail="Too many requests")

    # Per-agent auth check
    if agent.api_key_hash:
        if not credentials or hash_api_key(credentials.credentials) != agent.api_key_hash:
            raise HTTPException(status_code=401, detail="Invalid or missing agent API key")

    # Resolve calling org for billing (if credentials provided and match an org)
    caller_org_for_billing = None
    if credentials:
        key_hash = hash_api_key(credentials.credentials)
        async with async_session() as session:
            result = await session.execute(
                select(Organization).where(Organization.api_key_hash == key_hash)
            )
            caller_org_for_billing = result.scalar_one_or_none()

    # Pre-check balance (fail fast with 402 before calling the agent)
    if agent.price_per_task > 0 and caller_org_for_billing:
        if caller_org_for_billing.balance < agent.price_per_task:
            raise HTTPException(
                status_code=402,
                detail=(
                    f"Insufficient balance: "
                    f"{caller_org_for_billing.balance:.4f} < "
                    f"{agent.price_per_task:.4f} "
                    f"(agent: {agent.name})"
                ),
            )

    agent_name = agent.name
    webhook_url = agent.webhook_url
    task_id_str = task.get("id")
    target_url = f"{agent.url.rstrip('/')}/a2a"
    logger.info("Routing task to %s (%s)", agent_name, target_url)

    # Run pre-task plugins
    pre_context = await plugin_manager.run_pre_hooks({
        "agent_id": str(agent_id),
        "agent_name": agent_name,
        "task": task,
        "client_ip": client_ip,
    })
    task = pre_context.get("task", task)

    with Timer() as t:
        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                resp = await client.post(target_url, json=task)
            except httpx.ConnectError:
                record_request(agent_name, t.elapsed_ms, error_type="connect_error")
                logger.error("Cannot reach %s at %s", agent_name, agent.url)
                background_tasks.add_task(
                    _save_task_log, agent_id, agent_name, client_ip,
                    task_id_str, "error", t.elapsed_ms, "connect_error",
                )
                raise HTTPException(
                    status_code=502,
                    detail=f"Cannot reach agent at {agent.url}",
                )
            except httpx.TimeoutException:
                record_request(agent_name, t.elapsed_ms, error_type="timeout")
                logger.error("Timeout reaching %s at %s", agent_name, agent.url)
                background_tasks.add_task(
                    _save_task_log, agent_id, agent_name, client_ip,
                    task_id_str, "error", t.elapsed_ms, "timeout",
                )
                raise HTTPException(
                    status_code=504,
                    detail=f"Agent at {agent.url} timed out",
                )

    if resp.status_code >= 400:
        record_request(agent_name, t.elapsed_ms, error_type=f"http_{resp.status_code}")
        logger.warning(
            "Agent %s returned %d in %.0fms", agent_name, resp.status_code, t.elapsed_ms,
        )
        background_tasks.add_task(
            _save_task_log, agent_id, agent_name, client_ip,
            task_id_str, "error", t.elapsed_ms, f"http_{resp.status_code}",
        )
        raise HTTPException(
            status_code=resp.status_code,
            detail=f"Agent returned error: {resp.text}",
        )

    record_request(agent_name, t.elapsed_ms)
    logger.info("Task routed to %s — %d in %.0fms", agent_name, resp.status_code, t.elapsed_ms)

    # Charge billing AFTER successful A2A response (no charge on failure)
    if agent.price_per_task > 0:
        charged, err = await _process_billing(
            agent, caller_org_for_billing, task_id_str,
        )
        if not charged:
            raise HTTPException(status_code=402, detail=err)

    # Save log in background
    background_tasks.add_task(
        _save_task_log, agent_id, agent_name, client_ip,
        task_id_str, "success", t.elapsed_ms,
    )

    if webhook_url:
        background_tasks.add_task(
            _fire_webhook,
            webhook_url,
            {
                "event": "task.completed",
                "agent_id": str(agent_id),
                "agent_name": agent_name,
                "task_id": task_id_str,
                "latency_ms": round(t.elapsed_ms, 1),
            },
        )

    # Run post-task plugins
    background_tasks.add_task(
        plugin_manager.run_post_hooks,
        {
            "agent_id": str(agent_id),
            "agent_name": agent_name,
            "task": task,
            "client_ip": client_ip,
            "status": "success",
            "latency_ms": round(t.elapsed_ms, 1),
            "response": resp.json(),
        },
    )

    result = resp.json()

    # Attach UCP metadata for paid agents
    if agent.price_per_task > 0:
        from agentgate.server.ucp_routes import UCP_VERSION

        result = {
            "result": result,
            "ucp": {
                "version": UCP_VERSION,
                "capability": "dev.ucp.shopping.checkout",
                "agent_id": str(agent_id),
                "price_per_task": agent.price_per_task,
                "currency": "USD",
            },
        }

    return result


@router.post("/{agent_id}/task/stream")
async def route_task_stream(
    agent_id: uuid.UUID, task: dict, request: Request,
    background_tasks: BackgroundTasks,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme_optional),
):
    """Route an A2A task and stream the response via Server-Sent Events.

    Returns an SSE stream with events:
      - status: task routing status updates
      - chunk: streamed response data from the agent
      - result: final complete result
      - error: error details if something fails
    """
    client_ip = request.client.host if request.client else "unknown"

    async with async_session() as session:
        agent = await session.get(Agent, agent_id)
        if not agent:
            raise HTTPException(status_code=404, detail="Agent not found")

        if agent.org_id:
            org = await session.get(Organization, agent.org_id)
            if org:
                org_limiter = RateLimiter(rate=org.rate_limit, burst=org.rate_burst)
                org_key = f"org:{org.id}:{client_ip}"
                if not org_limiter.allow(org_key):
                    raise HTTPException(status_code=429, detail="Too many requests")
            else:
                if not task_limiter.allow(client_ip):
                    raise HTTPException(status_code=429, detail="Too many requests")
        else:
            if not task_limiter.allow(client_ip):
                raise HTTPException(status_code=429, detail="Too many requests")

    if agent.api_key_hash:
        if not credentials or hash_api_key(credentials.credentials) != agent.api_key_hash:
            raise HTTPException(status_code=401, detail="Invalid or missing agent API key")

    # Resolve calling org for billing
    caller_org_for_billing = None
    if credentials:
        key_hash = hash_api_key(credentials.credentials)
        async with async_session() as session:
            result = await session.execute(
                select(Organization).where(Organization.api_key_hash == key_hash)
            )
            caller_org_for_billing = result.scalar_one_or_none()

    # Pre-check balance (fail fast with 402)
    if agent.price_per_task > 0 and caller_org_for_billing:
        if caller_org_for_billing.balance < agent.price_per_task:
            raise HTTPException(
                status_code=402,
                detail=(
                    f"Insufficient balance: "
                    f"{caller_org_for_billing.balance:.4f} < "
                    f"{agent.price_per_task:.4f} "
                    f"(agent: {agent.name})"
                ),
            )

    agent_name = agent.name
    webhook_url = agent.webhook_url
    task_id_str = task.get("id")
    target_url = f"{agent.url.rstrip('/')}/a2a"

    async def event_generator():
        yield {"event": "status", "data": f"Routing task to {agent_name}"}

        with Timer() as t:
            try:
                async with httpx.AsyncClient(timeout=30.0) as http_client:
                    async with http_client.stream(
                        "POST", target_url, json=task,
                    ) as resp:
                        if resp.status_code >= 400:
                            record_request(
                                agent_name, t.elapsed_ms,
                                error_type=f"http_{resp.status_code}",
                            )
                            yield {
                                "event": "error",
                                "data": f"Agent returned {resp.status_code}",
                            }
                            return

                        chunks = []
                        async for chunk in resp.aiter_text():
                            chunks.append(chunk)
                            yield {"event": "chunk", "data": chunk}

            except httpx.ConnectError:
                record_request(agent_name, t.elapsed_ms, error_type="connect_error")
                yield {"event": "error", "data": f"Cannot reach agent at {agent.url}"}
                return
            except httpx.TimeoutException:
                record_request(agent_name, t.elapsed_ms, error_type="timeout")
                yield {"event": "error", "data": f"Agent at {agent.url} timed out"}
                return

        record_request(agent_name, t.elapsed_ms)
        logger.info(
            "Task streamed to %s — %.0fms", agent_name, t.elapsed_ms,
        )

        # Charge billing AFTER successful stream
        if agent.price_per_task > 0:
            charged, err = await _process_billing(
                agent, caller_org_for_billing, task_id_str,
            )
            if not charged:
                yield {"event": "error", "data": err}
                return

        yield {
            "event": "result",
            "data": "".join(chunks),
        }

        # Save log (inline since we're in a generator)
        await _save_task_log(
            agent_id, agent_name, client_ip,
            task_id_str, "success", t.elapsed_ms,
        )

        if webhook_url:
            await _fire_webhook(
                webhook_url,
                {
                    "event": "task.completed",
                    "agent_id": str(agent_id),
                    "agent_name": agent_name,
                    "task_id": task_id_str,
                    "latency_ms": round(t.elapsed_ms, 1),
                },
            )

    return EventSourceResponse(event_generator())


@router.websocket("/{agent_id}/task/ws")
async def route_task_ws(websocket: WebSocket, agent_id: uuid.UUID):
    """WebSocket endpoint for bidirectional task routing.

    Client sends JSON messages with A2A task payloads.
    Server responds with JSON messages containing:
      {"type": "status", "data": "..."}
      {"type": "chunk", "data": "..."}
      {"type": "result", "data": {...}}
      {"type": "error", "data": "..."}

    Auth: send {"type": "auth", "token": "..."} as first message if agent requires auth.
    """
    await websocket.accept()
    client_ip = websocket.client.host if websocket.client else "unknown"

    # Resolve agent
    async with async_session() as session:
        agent = await session.get(Agent, agent_id)
        if not agent:
            await websocket.send_json({"type": "error", "data": "Agent not found"})
            await websocket.close(code=4004)
            return

    agent_name = agent.name
    webhook_url = agent.webhook_url
    target_url = f"{agent.url.rstrip('/')}/a2a"
    auth_token: str | None = None
    caller_org_for_billing: Organization | None = None

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                await websocket.send_json({"type": "error", "data": "Invalid JSON"})
                continue

            msg_type = msg.get("type", "task")

            # Handle auth message
            if msg_type == "auth":
                auth_token = msg.get("token")
                # Resolve calling org for billing
                if auth_token:
                    key_hash = hash_api_key(auth_token)
                    async with async_session() as session:
                        result = await session.execute(
                            select(Organization).where(Organization.api_key_hash == key_hash)
                        )
                        caller_org_for_billing = result.scalar_one_or_none()
                await websocket.send_json({"type": "status", "data": "Authenticated"})
                continue

            # For task messages, validate auth if required
            if agent.api_key_hash:
                if not auth_token or hash_api_key(auth_token) != agent.api_key_hash:
                    await websocket.send_json({
                        "type": "error", "data": "Invalid or missing agent API key",
                    })
                    continue

            # Rate limiting
            if not task_limiter.allow(client_ip):
                await websocket.send_json({"type": "error", "data": "Too many requests"})
                continue

            # Extract task payload
            task = msg.get("task", msg)
            task_id_str = task.get("id")

            # Pre-check balance (fail fast with 402)
            if agent.price_per_task > 0 and caller_org_for_billing:
                if caller_org_for_billing.balance < agent.price_per_task:
                    await websocket.send_json({
                        "type": "error",
                        "data": (
                            f"Insufficient balance: "
                            f"{caller_org_for_billing.balance:.4f} < "
                            f"{agent.price_per_task:.4f} "
                            f"(agent: {agent.name})"
                        ),
                    })
                    continue

            await websocket.send_json({
                "type": "status", "data": f"Routing task to {agent_name}",
            })

            # Run pre-task plugins
            try:
                pre_context = await plugin_manager.run_pre_hooks({
                    "agent_id": str(agent_id),
                    "agent_name": agent_name,
                    "task": task,
                    "client_ip": client_ip,
                })
                task = pre_context.get("task", task)
            except Exception as e:
                await websocket.send_json({"type": "error", "data": str(e)})
                continue

            # Route the task
            with Timer() as t:
                try:
                    async with httpx.AsyncClient(timeout=30.0) as client:
                        resp = await client.post(target_url, json=task)
                except httpx.ConnectError:
                    record_request(agent_name, t.elapsed_ms, error_type="connect_error")
                    await _save_task_log(
                        agent_id, agent_name, client_ip,
                        task_id_str, "error", t.elapsed_ms, "connect_error",
                    )
                    await websocket.send_json({
                        "type": "error", "data": f"Cannot reach agent at {agent.url}",
                    })
                    continue
                except httpx.TimeoutException:
                    record_request(agent_name, t.elapsed_ms, error_type="timeout")
                    await _save_task_log(
                        agent_id, agent_name, client_ip,
                        task_id_str, "error", t.elapsed_ms, "timeout",
                    )
                    await websocket.send_json({
                        "type": "error", "data": f"Agent at {agent.url} timed out",
                    })
                    continue

            if resp.status_code >= 400:
                record_request(agent_name, t.elapsed_ms, error_type=f"http_{resp.status_code}")
                await _save_task_log(
                    agent_id, agent_name, client_ip,
                    task_id_str, "error", t.elapsed_ms, f"http_{resp.status_code}",
                )
                await websocket.send_json({
                    "type": "error", "data": f"Agent returned {resp.status_code}",
                })
                continue

            record_request(agent_name, t.elapsed_ms)
            result_data = resp.json()

            # Charge billing AFTER successful response
            if agent.price_per_task > 0:
                charged, err = await _process_billing(
                    agent, caller_org_for_billing, task_id_str,
                )
                if not charged:
                    await websocket.send_json({"type": "error", "data": err})
                    continue
                # Refresh balance for next task in same WS session
                if caller_org_for_billing:
                    async with async_session() as session:
                        refreshed = await session.get(Organization, caller_org_for_billing.id)
                        if refreshed:
                            caller_org_for_billing = refreshed

            await websocket.send_json({"type": "result", "data": result_data})

            # Background: save log, fire webhook, run post-hooks
            await _save_task_log(
                agent_id, agent_name, client_ip,
                task_id_str, "success", t.elapsed_ms,
            )
            if webhook_url:
                asyncio.create_task(_fire_webhook(
                    webhook_url,
                    {
                        "event": "task.completed",
                        "agent_id": str(agent_id),
                        "agent_name": agent_name,
                        "task_id": task_id_str,
                        "latency_ms": round(t.elapsed_ms, 1),
                    },
                ))
            asyncio.create_task(plugin_manager.run_post_hooks({
                "agent_id": str(agent_id),
                "agent_name": agent_name,
                "task": task,
                "client_ip": client_ip,
                "status": "success",
                "latency_ms": round(t.elapsed_ms, 1),
                "response": result_data,
            }))

    except WebSocketDisconnect:
        logger.info("WebSocket client disconnected from agent %s", agent_name)
