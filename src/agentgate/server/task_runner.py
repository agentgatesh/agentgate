"""Task execution helpers — HTTP call to the agent, log writing, webhook fire.

Kept thin and stateless so the route handlers (routes.py) can stay focused on
HTTP concerns and the test suite's existing mocks of
`agentgate.server.routes.async_session` / `httpx` continue to match.
"""

from __future__ import annotations

import asyncio
import logging
import uuid

import httpx

from agentgate.db.engine import async_session
from agentgate.db.models import TaskLog

logger = logging.getLogger("agentgate.task_runner")


async def fire_webhook(
    webhook_url: str, payload: dict, max_retries: int = 3,
) -> None:
    """POST to a webhook URL with exponential backoff retry (1s, 2s, 4s).

    Treats any 5xx as retryable and anything <500 as terminal success.
    """
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
            await asyncio.sleep(2 ** attempt)
    logger.error("Webhook exhausted retries for %s", webhook_url)


async def save_task_log(
    agent_id: uuid.UUID,
    agent_name: str,
    caller_ip: str,
    task_id: str | None,
    status: str,
    latency_ms: float,
    error_detail: str | None = None,
) -> None:
    """Persist a single TaskLog row. Swallows failures (logging is best-effort)."""
    try:
        async with async_session() as session:
            session.add(TaskLog(
                agent_id=agent_id,
                agent_name=agent_name,
                caller_ip=caller_ip,
                task_id=task_id,
                status=status,
                latency_ms=latency_ms,
                error_detail=error_detail,
            ))
            await session.commit()
    except Exception:
        logger.warning("Failed to save task log for %s", agent_name)
