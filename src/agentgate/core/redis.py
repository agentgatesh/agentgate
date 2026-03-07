"""Redis client with graceful fallback to None if unavailable."""

import logging

from agentgate.core.config import settings

logger = logging.getLogger("agentgate.redis")

_redis_client = None


def get_redis():
    """Return the Redis client (sync), or None if not configured/available."""
    global _redis_client
    if _redis_client is not None:
        return _redis_client
    if not settings.redis_url:
        return None
    try:
        import redis

        _redis_client = redis.from_url(settings.redis_url, decode_responses=True)
        _redis_client.ping()
        logger.info("Connected to Redis at %s", settings.redis_url)
        return _redis_client
    except Exception:
        logger.warning("Redis unavailable, falling back to in-memory")
        return None
