"""Rate limiter with Redis support and in-memory fallback."""

import logging
import time
from threading import Lock

logger = logging.getLogger("agentgate.ratelimit")

# Lua script for atomic token bucket in Redis
_LUA_SCRIPT = """
local key = KEYS[1]
local rate = tonumber(ARGV[1])
local burst = tonumber(ARGV[2])
local now = tonumber(ARGV[3])

local data = redis.call('HMGET', key, 'tokens', 'last')
local tokens = tonumber(data[1])
local last = tonumber(data[2])

if tokens == nil then
    tokens = burst
    last = now
end

local elapsed = now - last
tokens = math.min(burst, tokens + elapsed * rate)

if tokens >= 1 then
    redis.call('HMSET', key, 'tokens', tokens - 1, 'last', now)
    redis.call('EXPIRE', key, 60)
    return 1
else
    redis.call('HMSET', key, 'tokens', tokens, 'last', now)
    redis.call('EXPIRE', key, 60)
    return 0
end
"""


class RateLimiter:
    """Token bucket rate limiter with Redis support.

    Uses Redis if available, otherwise falls back to in-memory.
    """

    def __init__(self, rate: float = 10.0, burst: int = 20):
        self.rate = rate
        self.burst = burst
        self._buckets: dict[str, tuple[float, float]] = {}
        self._lock = Lock()
        self._redis = None
        self._lua_sha = None

    def _get_redis(self):
        """Lazy-load Redis client."""
        if self._redis is not None:
            return self._redis
        try:
            from agentgate.core.redis import get_redis

            self._redis = get_redis()
            if self._redis:
                self._lua_sha = self._redis.script_load(_LUA_SCRIPT)
                logger.info("Rate limiter using Redis")
            else:
                self._redis = False  # Sentinel: tried, not available
        except Exception:
            self._redis = False
        return self._redis if self._redis is not False else None

    def allow(self, key: str) -> bool:
        """Check if a request from `key` is allowed."""
        redis_client = self._get_redis()
        if redis_client:
            return self._allow_redis(redis_client, key)
        return self._allow_memory(key)

    def reset(self, key: str) -> None:
        """Remove a key's bucket (useful for testing)."""
        with self._lock:
            self._buckets.pop(key, None)

    def _allow_redis(self, redis_client, key: str) -> bool:
        try:
            result = redis_client.evalsha(
                self._lua_sha, 1,
                f"rl:{key}", str(self.rate), str(self.burst), str(time.time()),
            )
            return result == 1
        except Exception:
            logger.warning("Redis rate limit error, falling back to in-memory")
            return self._allow_memory(key)

    def _allow_memory(self, key: str) -> bool:
        now = time.monotonic()
        with self._lock:
            tokens, last_time = self._buckets.get(key, (self.burst, now))
            elapsed = now - last_time
            tokens = min(self.burst, tokens + elapsed * self.rate)
            if tokens >= 1:
                self._buckets[key] = (tokens - 1, now)
                return True
            self._buckets[key] = (tokens, now)
            return False


task_limiter = RateLimiter(rate=10.0, burst=20)

# Stricter limiter for auth endpoints (login, signup) — 5 attempts per 60s
auth_limiter = RateLimiter(rate=5 / 60, burst=5)
