"""Task execution helpers — HTTP call to the agent, log writing, webhook fire.

Kept thin and stateless so the route handlers (routes.py) can stay focused on
HTTP concerns and the test suite's existing mocks of
`agentgate.server.routes.async_session` / `httpx` continue to match.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import time
import uuid

import httpx

from agentgate.core.config import settings
from agentgate.db.engine import async_session
from agentgate.db.models import TaskLog

logger = logging.getLogger("agentgate.task_runner")


def _sign_webhook(payload_bytes: bytes, timestamp: str) -> str:
    """HMAC-SHA256 signature over `timestamp.payload_bytes`, using SECRET_KEY.

    Receivers verify with the same algorithm. Including the timestamp in
    the signed material defeats replay attacks once the receiver also
    checks that the timestamp is recent (±5 min is typical).
    """
    signed = timestamp.encode() + b"." + payload_bytes
    return hmac.new(settings.secret_key.encode(), signed, hashlib.sha256).hexdigest()


async def fire_webhook(
    webhook_url: str, payload: dict, max_retries: int = 3,
) -> None:
    """POST to a webhook URL with exponential backoff retry (1s, 2s, 4s).

    Attaches headers so receivers can verify provenance:
      X-AgentGate-Timestamp: unix seconds of signing
      X-AgentGate-Signature: hex HMAC-SHA256 of "{ts}.{raw_body}"

    Treats any 5xx as retryable and anything <500 as terminal success.
    """
    body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    timestamp = str(int(time.time()))
    signature = _sign_webhook(body, timestamp)
    headers = {
        "Content-Type": "application/json",
        "X-AgentGate-Timestamp": timestamp,
        "X-AgentGate-Signature": signature,
        "User-Agent": "AgentGate-Webhook/1",
    }

    for attempt in range(max_retries):
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(webhook_url, content=body, headers=headers)
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
