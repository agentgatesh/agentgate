import logging
import uuid

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select

from agentgate.core.config import settings
from agentgate.db.engine import async_session
from agentgate.db.models import Agent
from agentgate.server.metrics import Timer, record_request
from agentgate.server.ratelimit import task_limiter
from agentgate.server.schemas import AgentCard, AgentCreate, AgentResponse, AgentUpdate

logger = logging.getLogger("agentgate.routing")

router = APIRouter(prefix="/agents", tags=["agents"])
bearer_scheme = HTTPBearer()


def verify_api_key(credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme)):
    if not settings.api_key:
        raise HTTPException(status_code=500, detail="API key not configured on server")
    if credentials.credentials != settings.api_key:
        raise HTTPException(status_code=401, detail="Invalid API key")


@router.post(
    "/", response_model=AgentResponse, status_code=201,
    dependencies=[Depends(verify_api_key)],
)
async def register_agent(data: AgentCreate):
    async with async_session() as session:
        agent = Agent(
            name=data.name,
            description=data.description,
            url=data.url,
            version=data.version,
            skills=data.skills,
        )
        session.add(agent)
        await session.commit()
        await session.refresh(agent)
        return agent


@router.get("/", response_model=list[AgentResponse])
async def list_agents():
    async with async_session() as session:
        result = await session.execute(select(Agent).order_by(Agent.created_at.desc()))
        return result.scalars().all()


@router.get("/{agent_id}", response_model=AgentResponse)
async def get_agent(agent_id: uuid.UUID):
    async with async_session() as session:
        agent = await session.get(Agent, agent_id)
        if not agent:
            raise HTTPException(status_code=404, detail="Agent not found")
        return agent


@router.put(
    "/{agent_id}", response_model=AgentResponse,
    dependencies=[Depends(verify_api_key)],
)
async def update_agent(agent_id: uuid.UUID, data: AgentUpdate):
    async with async_session() as session:
        agent = await session.get(Agent, agent_id)
        if not agent:
            raise HTTPException(status_code=404, detail="Agent not found")
        for field, value in data.model_dump(exclude_none=True).items():
            setattr(agent, field, value)
        await session.commit()
        await session.refresh(agent)
        return agent


@router.delete("/{agent_id}", status_code=204, dependencies=[Depends(verify_api_key)])
async def delete_agent(agent_id: uuid.UUID):
    async with async_session() as session:
        agent = await session.get(Agent, agent_id)
        if not agent:
            raise HTTPException(status_code=404, detail="Agent not found")
        await session.delete(agent)
        await session.commit()


@router.get("/{agent_id}/card", response_model=AgentCard)
async def get_agent_card(agent_id: uuid.UUID):
    async with async_session() as session:
        agent = await session.get(Agent, agent_id)
        if not agent:
            raise HTTPException(status_code=404, detail="Agent not found")
        return AgentCard(
            name=agent.name,
            description=agent.description,
            url=agent.url,
            version=agent.version,
            skills=agent.skills,
        )


@router.post("/{agent_id}/task")
async def route_task(agent_id: uuid.UUID, task: dict, request: Request):
    """Route an A2A task to the target agent (proxy).

    Looks up the agent's URL in the registry and forwards the task payload
    to {agent_url}/a2a. Returns the agent's response directly.
    """
    client_ip = request.client.host if request.client else "unknown"
    if not task_limiter.allow(client_ip):
        raise HTTPException(status_code=429, detail="Too many requests")

    async with async_session() as session:
        agent = await session.get(Agent, agent_id)
        if not agent:
            raise HTTPException(status_code=404, detail="Agent not found")

    agent_name = agent.name
    target_url = f"{agent.url.rstrip('/')}/a2a"
    logger.info("Routing task to %s (%s)", agent_name, target_url)

    with Timer() as t:
        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                resp = await client.post(target_url, json=task)
            except httpx.ConnectError:
                record_request(agent_name, t.elapsed_ms, error_type="connect_error")
                logger.error("Cannot reach %s at %s", agent_name, agent.url)
                raise HTTPException(
                    status_code=502,
                    detail=f"Cannot reach agent at {agent.url}",
                )
            except httpx.TimeoutException:
                record_request(agent_name, t.elapsed_ms, error_type="timeout")
                logger.error("Timeout reaching %s at %s", agent_name, agent.url)
                raise HTTPException(
                    status_code=504,
                    detail=f"Agent at {agent.url} timed out",
                )

    if resp.status_code >= 400:
        record_request(agent_name, t.elapsed_ms, error_type=f"http_{resp.status_code}")
        logger.warning(
            "Agent %s returned %d in %.0fms", agent_name, resp.status_code, t.elapsed_ms,
        )
        raise HTTPException(
            status_code=resp.status_code,
            detail=f"Agent returned error: {resp.text}",
        )

    record_request(agent_name, t.elapsed_ms)
    logger.info("Task routed to %s — %d in %.0fms", agent_name, resp.status_code, t.elapsed_ms)
    return resp.json()
