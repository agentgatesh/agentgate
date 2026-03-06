import uuid

from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select

from agentgate.core.config import settings
from agentgate.db.engine import async_session
from agentgate.db.models import Agent
from agentgate.server.schemas import AgentCard, AgentCreate, AgentResponse

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
