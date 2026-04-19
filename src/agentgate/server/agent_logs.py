"""Agent logs / usage / health endpoints."""

import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import case, cast, desc, func, select
from sqlalchemy.types import Date

from agentgate.db.models import Agent, Organization, TaskLog
from agentgate.server.deps import verify_api_key_or_org
from agentgate.server.healthcheck import get_agent_health

router = APIRouter()


def _async_session():
    from agentgate.server import routes
    return routes.async_session


@router.get("/{agent_id}/health")
async def agent_health(agent_id: uuid.UUID):
    async with _async_session()() as session:
        agent = await session.get(Agent, agent_id)
        if not agent:
            raise HTTPException(status_code=404, detail="Agent not found")
    health = get_agent_health(str(agent_id))
    if not health:
        return {"agent": agent.name, "status": "unknown", "message": "No health check yet"}
    return {"agent": agent.name, **health}


@router.get("/{agent_id}/logs")
async def get_agent_logs(
    agent_id: uuid.UUID,
    caller_org: Organization | None = Depends(verify_api_key_or_org),
    limit: int = Query(default=50, le=200),
    offset: int = Query(default=0, ge=0),
):
    """Get invocation logs for an agent. Requires API key (admin or org)."""
    async with _async_session()() as session:
        agent = await session.get(Agent, agent_id)
        if not agent:
            raise HTTPException(status_code=404, detail="Agent not found")
        if caller_org and agent.org_id != caller_org.id:
            raise HTTPException(status_code=403, detail="Access denied to this agent")
        result = await session.execute(
            select(TaskLog)
            .where(TaskLog.agent_id == agent_id)
            .order_by(desc(TaskLog.created_at))
            .offset(offset)
            .limit(limit)
        )
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
    async with _async_session()() as session:
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
    async with _async_session()() as session:
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
