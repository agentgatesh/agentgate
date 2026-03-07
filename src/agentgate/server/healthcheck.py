"""Background health checker for registered agents."""

import asyncio
import logging
from datetime import datetime, timezone

import httpx

from agentgate.db.engine import async_session
from agentgate.db.models import Agent

logger = logging.getLogger("agentgate.healthcheck")

# In-memory health status: agent_id -> {status, last_check, latency_ms, error}
_health_status: dict[str, dict] = {}

CHECK_INTERVAL_SECONDS = 60
TIMEOUT_SECONDS = 10


async def check_agent(agent_id: str, agent_name: str, agent_url: str):
    """Ping a single agent's /health endpoint."""
    url = f"{agent_url.rstrip('/')}/health"
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
            start = asyncio.get_event_loop().time()
            resp = await client.get(url)
            latency = (asyncio.get_event_loop().time() - start) * 1000
            if resp.status_code == 200:
                _health_status[agent_id] = {
                    "status": "healthy",
                    "last_check": datetime.now(timezone.utc).isoformat(),
                    "latency_ms": round(latency, 1),
                    "error": None,
                }
            else:
                _health_status[agent_id] = {
                    "status": "unhealthy",
                    "last_check": datetime.now(timezone.utc).isoformat(),
                    "latency_ms": round(latency, 1),
                    "error": f"HTTP {resp.status_code}",
                }
    except httpx.ConnectError:
        _health_status[agent_id] = {
            "status": "unhealthy",
            "last_check": datetime.now(timezone.utc).isoformat(),
            "latency_ms": None,
            "error": "connect_error",
        }
    except httpx.TimeoutException:
        _health_status[agent_id] = {
            "status": "unhealthy",
            "last_check": datetime.now(timezone.utc).isoformat(),
            "latency_ms": None,
            "error": "timeout",
        }
    except Exception as e:
        _health_status[agent_id] = {
            "status": "unhealthy",
            "last_check": datetime.now(timezone.utc).isoformat(),
            "latency_ms": None,
            "error": str(e),
        }


async def run_health_checks():
    """Run health checks for all registered agents."""
    from sqlalchemy import select

    async with async_session() as session:
        result = await session.execute(select(Agent))
        agents = result.scalars().all()

    tasks = [check_agent(str(a.id), a.name, a.url) for a in agents]
    if tasks:
        await asyncio.gather(*tasks)
    logger.info("Health check completed for %d agent(s)", len(tasks))


async def health_check_loop():
    """Background loop that runs health checks periodically."""
    while True:
        try:
            await run_health_checks()
        except Exception:
            logger.exception("Health check loop error")
        await asyncio.sleep(CHECK_INTERVAL_SECONDS)


def get_agent_health(agent_id: str) -> dict | None:
    """Get health status for a specific agent."""
    return _health_status.get(agent_id)


def get_all_health() -> dict:
    """Get health status for all agents."""
    return dict(_health_status)
