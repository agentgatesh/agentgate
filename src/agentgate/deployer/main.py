"""Deployer sidecar — the only process with /var/run/docker.sock.

Exposes a tiny HTTP API on the internal docker network. The main API
container calls this over HTTP to build / run / stop agent containers,
so the main API never talks to Docker directly. If an attacker bypasses
auth on the main API, they cannot execute commands on the host because
the main API has no docker socket mount.

Auth: bearer token (DEPLOYER_SECRET shared between api + deployer).
Bound to 0.0.0.0 inside the compose network but NOT exposed to the host.
"""

from __future__ import annotations

import hmac
import logging
import os

import uvicorn
from fastapi import Depends, FastAPI, HTTPException, UploadFile
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from agentgate.server.deploy_engine import (
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

logger = logging.getLogger("agentgate.deployer")
_bearer = HTTPBearer()


def _secret() -> str:
    return os.environ.get("DEPLOYER_SECRET", "")


def verify(credentials: HTTPAuthorizationCredentials = Depends(_bearer)) -> None:
    expected = _secret()
    if not expected:
        raise HTTPException(status_code=503, detail="Deployer not configured")
    if not hmac.compare_digest(credentials.credentials, expected):
        raise HTTPException(status_code=401, detail="Invalid deployer token")


app = FastAPI(
    title="AgentGate Deployer",
    description="Internal sidecar that talks to the Docker socket. Not public.",
)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.post("/build-and-run", dependencies=[Depends(verify)])
async def build_and_run(
    agent_id: str,
    port: int,
    file: UploadFile,
) -> dict:
    """Extract tar.gz, ensure Dockerfile, build image, run container.

    Body: multipart/form-data with `file=<tar.gz>`.
    Query: agent_id, port.
    """
    tar_bytes = await file.read()
    if len(tar_bytes) > 50 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="Archive too large (max 50 MB)")

    try:
        agent_dir = save_agent_files(agent_id, tar_bytes)
        ensure_dockerfile(agent_dir, port)
        build_image(agent_id, agent_dir)
        container_id = run_container(agent_id, port)
    except Exception as exc:
        stop_container(agent_id)
        remove_image(agent_id)
        cleanup_deploy_files(agent_id)
        logger.exception("Deploy failed for %s", agent_id)
        raise HTTPException(status_code=500, detail=f"Deploy failed: {exc}")

    return {"container_id": container_id, "port": port}


@app.delete("/{agent_id}", dependencies=[Depends(verify)])
async def undeploy(agent_id: str) -> dict:
    stop_container(agent_id)
    remove_image(agent_id)
    cleanup_deploy_files(agent_id)
    return {"status": "undeployed", "agent_id": agent_id}


@app.get("/{agent_id}/status", dependencies=[Depends(verify)])
async def status(agent_id: str) -> dict:
    return get_container_status(agent_id)


@app.get("/{agent_id}/logs", dependencies=[Depends(verify)])
async def logs(agent_id: str, tail: int = 100) -> dict:
    return {"agent_id": agent_id, "logs": get_container_logs(agent_id, tail=tail)}


def main() -> None:
    uvicorn.run(app, host="0.0.0.0", port=8100)


if __name__ == "__main__":
    main()
