"""Deploy routes — upload, build, run, and manage agent containers."""

import asyncio
import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy import select

from agentgate.db.engine import async_session
from agentgate.db.models import Agent
from agentgate.server.auth import verify_api_key
from agentgate.server.deploy_engine import (
    allocate_port,
    build_image,
    cleanup_deploy_files,
    ensure_dockerfile,
    get_container_logs,
    get_container_status,
    remove_image,
    run_container,
    save_agent_files,
    stop_container,
)

logger = logging.getLogger("agentgate.deploy_routes")

router = APIRouter(prefix="/deploy", tags=["deploy"])

# Deploy is sequential (build is expensive anyway). A process-wide lock
# serialises port allocation + container run so two concurrent deploys
# never pick the same port.
_deploy_lock = asyncio.Lock()


@router.post("/", status_code=201)
async def deploy_agent(
    file: UploadFile,
    name: str,
    description: str = "",
    version: str = "1.0.0",
    _creds: HTTPAuthorizationCredentials = Depends(verify_api_key),
):
    """Deploy an agent from an uploaded tar.gz archive.

    The archive should contain at minimum an `agent.py` file with a FastAPI app.
    A Dockerfile is auto-generated if not included.
    """
    if not file.filename or not file.filename.endswith(".tar.gz"):
        raise HTTPException(status_code=400, detail="Upload must be a .tar.gz archive")

    tar_bytes = await file.read()
    if len(tar_bytes) > 50 * 1024 * 1024:  # 50 MB limit
        raise HTTPException(status_code=400, detail="Archive too large (max 50 MB)")

    agent_id = str(uuid.uuid4())

    async with _deploy_lock:
        async with async_session() as session:
            result = await session.execute(
                select(Agent.container_port).where(Agent.container_port.isnot(None))
            )
            existing_ports = [row[0] for row in result.all()]

        port = allocate_port(existing_ports)

        try:
            agent_dir = save_agent_files(agent_id, tar_bytes)
            ensure_dockerfile(agent_dir, port)
            build_image(agent_id, agent_dir)
            container_id = run_container(agent_id, port)
        except Exception as exc:
            stop_container(agent_id)
            remove_image(agent_id)
            cleanup_deploy_files(agent_id)
            logger.error("Deploy failed for %s: %s", name, exc)
            raise HTTPException(status_code=500, detail=f"Deploy failed: {exc}")

        container_name = f"agentgate-agent-{agent_id[:12]}"
        internal_url = f"http://{container_name}:{port}"

        async with async_session() as session:
            agent = Agent(
                id=uuid.UUID(agent_id),
                name=name,
                description=description,
                url=internal_url,
                version=version,
                skills=[],
                tags=[],
                deployed=True,
                container_id=container_id[:12],
                container_port=port,
            )
            session.add(agent)
            await session.commit()
            await session.refresh(agent)

    return {
        "id": str(agent.id),
        "name": agent.name,
        "description": agent.description,
        "version": agent.version,
        "url": internal_url,
        "port": port,
        "container_id": container_id[:12],
        "deployed": True,
        "card_url": f"https://agentgate.sh/agents/{agent.id}/card",
        "task_url": f"https://agentgate.sh/agents/{agent.id}/task",
    }


@router.get("/{agent_id}/status")
async def deploy_status(
    agent_id: str,
    _creds: HTTPAuthorizationCredentials = Depends(verify_api_key),
):
    """Get the status of a deployed agent container."""
    async with async_session() as session:
        result = await session.execute(
            select(Agent).where(Agent.id == uuid.UUID(agent_id))
        )
        agent = result.scalar_one_or_none()

    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    if not agent.deployed:
        raise HTTPException(status_code=400, detail="Agent is not a deployed agent")

    status = get_container_status(agent_id)
    return {
        "agent_id": str(agent.id),
        "agent_name": agent.name,
        **status,
    }


@router.get("/{agent_id}/logs")
async def deploy_logs(
    agent_id: str,
    tail: int = 100,
    _creds: HTTPAuthorizationCredentials = Depends(verify_api_key),
):
    """Get recent container logs for a deployed agent."""
    async with async_session() as session:
        result = await session.execute(
            select(Agent).where(Agent.id == uuid.UUID(agent_id))
        )
        agent = result.scalar_one_or_none()

    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    if not agent.deployed:
        raise HTTPException(status_code=400, detail="Agent is not a deployed agent")

    logs = get_container_logs(agent_id, tail=tail)
    return {"agent_id": str(agent.id), "logs": logs}


@router.delete("/{agent_id}", status_code=200)
async def undeploy_agent(
    agent_id: str,
    _creds: HTTPAuthorizationCredentials = Depends(verify_api_key),
):
    """Stop and remove a deployed agent (container + image + DB record)."""
    async with async_session() as session:
        result = await session.execute(
            select(Agent).where(Agent.id == uuid.UUID(agent_id))
        )
        agent = result.scalar_one_or_none()

    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    if not agent.deployed:
        raise HTTPException(status_code=400, detail="Agent is not a deployed agent")

    stop_container(agent_id)
    remove_image(agent_id)
    cleanup_deploy_files(agent_id)

    async with async_session() as session:
        result = await session.execute(
            select(Agent).where(Agent.id == uuid.UUID(agent_id))
        )
        agent = result.scalar_one_or_none()
        if agent:
            await session.delete(agent)
            await session.commit()

    return {"status": "undeployed", "agent_id": agent_id}
