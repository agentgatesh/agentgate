"""HTTP client that talks to the deployer sidecar.

Runs inside the API container, which has no docker socket. All container
lifecycle calls go through here → the deployer sidecar (the only process
with /var/run/docker.sock).
"""

from __future__ import annotations

import logging

import httpx

from agentgate.core.config import settings

logger = logging.getLogger("agentgate.deploy_client")


def _base_url() -> str:
    return settings.deployer_url.rstrip("/")


def _headers() -> dict:
    if not settings.deployer_secret:
        raise RuntimeError("DEPLOYER_SECRET not configured")
    return {"Authorization": f"Bearer {settings.deployer_secret}"}


async def build_and_run(agent_id: str, port: int, tar_bytes: bytes) -> dict:
    """Upload tar.gz to deployer, which will build and run the container."""
    async with httpx.AsyncClient(timeout=300.0) as client:
        resp = await client.post(
            f"{_base_url()}/build-and-run",
            params={"agent_id": agent_id, "port": port},
            files={"file": (f"{agent_id}.tar.gz", tar_bytes, "application/gzip")},
            headers=_headers(),
        )
    if resp.status_code >= 400:
        logger.error("Deployer build-and-run failed: %s %s", resp.status_code, resp.text)
        raise RuntimeError(f"Deployer error: {resp.text}")
    return resp.json()


async def undeploy(agent_id: str) -> dict:
    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.delete(
            f"{_base_url()}/{agent_id}", headers=_headers(),
        )
    resp.raise_for_status()
    return resp.json()


async def status(agent_id: str) -> dict:
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(
            f"{_base_url()}/{agent_id}/status", headers=_headers(),
        )
    resp.raise_for_status()
    return resp.json()


async def logs(agent_id: str, tail: int = 100) -> str:
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(
            f"{_base_url()}/{agent_id}/logs",
            params={"tail": tail},
            headers=_headers(),
        )
    resp.raise_for_status()
    return resp.json().get("logs", "")
