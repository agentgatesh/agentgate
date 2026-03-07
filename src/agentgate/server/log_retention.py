"""Periodic log retention: TTL-based and per-agent cap."""

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, func, select

from agentgate.core.config import settings
from agentgate.db.engine import async_session
from agentgate.db.models import TaskLog

logger = logging.getLogger("agentgate.log_retention")

CLEANUP_INTERVAL = 3600  # Run every hour


async def cleanup_old_logs():
    """Delete logs older than retention period and enforce per-agent cap."""
    try:
        async with async_session() as session:
            # 1) TTL-based: delete logs older than retention_days
            cutoff = datetime.now(timezone.utc) - timedelta(days=settings.log_retention_days)
            result = await session.execute(
                delete(TaskLog).where(TaskLog.created_at < cutoff)
            )
            ttl_deleted = result.rowcount

            # 2) Per-agent cap: keep only the latest N logs per agent
            agent_ids_result = await session.execute(
                select(TaskLog.agent_id).group_by(TaskLog.agent_id).having(
                    func.count(TaskLog.id) > settings.log_max_per_agent
                )
            )
            cap_deleted = 0
            for (agent_id,) in agent_ids_result.all():
                # Find the created_at of the Nth newest log
                nth_result = await session.execute(
                    select(TaskLog.created_at)
                    .where(TaskLog.agent_id == agent_id)
                    .order_by(TaskLog.created_at.desc())
                    .offset(settings.log_max_per_agent)
                    .limit(1)
                )
                nth_row = nth_result.scalar_one_or_none()
                if nth_row:
                    result = await session.execute(
                        delete(TaskLog).where(
                            TaskLog.agent_id == agent_id,
                            TaskLog.created_at <= nth_row,
                        )
                    )
                    cap_deleted += result.rowcount

            await session.commit()

            if ttl_deleted or cap_deleted:
                logger.info(
                    "Log cleanup: %d TTL-expired, %d over-cap deleted",
                    ttl_deleted, cap_deleted,
                )
    except Exception:
        logger.warning("Log cleanup failed", exc_info=True)


async def log_retention_loop():
    """Background loop that runs cleanup periodically."""
    while True:
        await asyncio.sleep(CLEANUP_INTERVAL)
        await cleanup_old_logs()
