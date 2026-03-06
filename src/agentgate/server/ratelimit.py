"""Simple in-memory rate limiter (token bucket per IP)."""

import time
from threading import Lock


class RateLimiter:
    """Token bucket rate limiter.

    Args:
        rate: Number of requests allowed per second.
        burst: Maximum burst size (bucket capacity).
    """

    def __init__(self, rate: float = 10.0, burst: int = 20):
        self.rate = rate
        self.burst = burst
        self._buckets: dict[str, tuple[float, float]] = {}  # ip -> (tokens, last_time)
        self._lock = Lock()

    def allow(self, key: str) -> bool:
        """Check if a request from `key` is allowed."""
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
