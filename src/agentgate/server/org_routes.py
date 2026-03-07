"""Organization routes for multi-tenancy support."""

import hashlib
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select

from agentgate.db.engine import async_session
from agentgate.db.models import Agent, Organization
from agentgate.server.routes import verify_api_key
from agentgate.server.schemas import AgentResponse, OrgCreate, OrgResponse

router = APIRouter(prefix="/orgs", tags=["organizations"])


def _hash_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


@router.post("/", response_model=OrgResponse, status_code=201,
             dependencies=[Depends(verify_api_key)])
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
            api_key_hash=_hash_key(data.api_key),
        )
        session.add(org)
        await session.commit()
        await session.refresh(org)
        return org


@router.get("/", response_model=list[OrgResponse],
            dependencies=[Depends(verify_api_key)])
async def list_orgs():
    """List all organizations. Requires admin API key."""
    async with async_session() as session:
        result = await session.execute(
            select(Organization).order_by(Organization.created_at.desc())
        )
        return result.scalars().all()


@router.get("/{org_id}", response_model=OrgResponse,
            dependencies=[Depends(verify_api_key)])
async def get_org(org_id: uuid.UUID):
    """Get an organization by ID. Requires admin API key."""
    async with async_session() as session:
        org = await session.get(Organization, org_id)
        if not org:
            raise HTTPException(status_code=404, detail="Organization not found")
        return org


@router.delete("/{org_id}", status_code=204,
               dependencies=[Depends(verify_api_key)])
async def delete_org(org_id: uuid.UUID):
    """Delete an organization. Requires admin API key."""
    async with async_session() as session:
        org = await session.get(Organization, org_id)
        if not org:
            raise HTTPException(status_code=404, detail="Organization not found")
        await session.delete(org)
        await session.commit()


@router.get("/{org_id}/agents", response_model=list[AgentResponse],
            dependencies=[Depends(verify_api_key)])
async def list_org_agents(org_id: uuid.UUID):
    """List agents belonging to an organization. Requires admin API key."""
    async with async_session() as session:
        org = await session.get(Organization, org_id)
        if not org:
            raise HTTPException(status_code=404, detail="Organization not found")
        result = await session.execute(
            select(Agent).where(Agent.org_id == org_id).order_by(Agent.created_at.desc())
        )
        return result.scalars().all()
