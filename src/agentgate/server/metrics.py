"""Metrics for task routing with Redis support and in-memory fallback."""

import logging
import time
from collections import defaultdict
from threading import Lock

logger = logging.getLogger("agentgate.metrics")

_lock = Lock()

# In-memory counters (fallback)
_total_requests: int = 0
_total_errors: int = 0
_errors_by_type: dict[str, int] = defaultdict(int)
_requests_by_agent: dict[str, int] = defaultdict(int)
_latencies: list[float] = []
_agent_latencies: dict[str, list[float]] = defaultdict(list)

_MAX_LATENCIES = 1000

_redis = None
_redis_checked = False


def _get_redis():
    """Lazy-load Redis client for metrics."""
    global _redis, _redis_checked
    if _redis_checked:
        return _redis
    _redis_checked = True
    try:
        from agentgate.core.redis import get_redis

        _redis = get_redis()
        if _redis:
            logger.info("Metrics using Redis")
    except Exception:
        _redis = None
    return _redis


def record_request(agent_name: str, latency_ms: float, error_type: str | None = None):
    """Record a task routing request."""
    r = _get_redis()
    if r:
        _record_redis(r, agent_name, latency_ms, error_type)
    else:
        _record_memory(agent_name, latency_ms, error_type)


def _record_redis(r, agent_name: str, latency_ms: float, error_type: str | None):
    try:
        pipe = r.pipeline()
        pipe.incr("metrics:total_requests")
        pipe.incr(f"metrics:agent:{agent_name}:requests")
        pipe.lpush(f"metrics:agent:{agent_name}:latencies", str(round(latency_ms, 1)))
        pipe.ltrim(f"metrics:agent:{agent_name}:latencies", 0, _MAX_LATENCIES - 1)
        pipe.lpush("metrics:latencies", str(round(latency_ms, 1)))
        pipe.ltrim("metrics:latencies", 0, _MAX_LATENCIES - 1)
        if error_type:
            pipe.incr("metrics:total_errors")
            pipe.incr(f"metrics:errors:{error_type}")
        pipe.execute()
    except Exception:
        logger.warning("Redis metrics error, falling back to in-memory")
        _record_memory(agent_name, latency_ms, error_type)


def _record_memory(agent_name: str, latency_ms: float, error_type: str | None):
    global _total_requests, _total_errors
    with _lock:
        _total_requests += 1
        _requests_by_agent[agent_name] += 1
        _latencies.append(latency_ms)
        if len(_latencies) > _MAX_LATENCIES:
            _latencies.pop(0)
        _agent_latencies[agent_name].append(latency_ms)
        if len(_agent_latencies[agent_name]) > _MAX_LATENCIES:
            _agent_latencies[agent_name].pop(0)
        if error_type:
            _total_errors += 1
            _errors_by_type[error_type] += 1


def get_metrics() -> dict:
    """Return current metrics snapshot."""
    r = _get_redis()
    if r:
        return _get_metrics_redis(r)
    return _get_metrics_memory()


def _get_metrics_redis(r) -> dict:
    try:
        total_requests = int(r.get("metrics:total_requests") or 0)
        total_errors = int(r.get("metrics:total_errors") or 0)

        # Get all latencies
        all_lats = [float(x) for x in r.lrange("metrics:latencies", 0, -1)]
        avg_latency = sum(all_lats) / len(all_lats) if all_lats else 0

        # Get error types
        errors_by_type = {}
        for key in r.scan_iter("metrics:errors:*"):
            error_name = key.split(":")[-1]
            errors_by_type[error_name] = int(r.get(key) or 0)

        # Get per-agent metrics
        per_agent = {}
        for key in r.scan_iter("metrics:agent:*:requests"):
            parts = key.split(":")
            agent_name = parts[2]
            requests = int(r.get(key) or 0)
            lats = [float(x) for x in r.lrange(f"metrics:agent:{agent_name}:latencies", 0, -1)]
            per_agent[agent_name] = {
                "requests": requests,
                "avg_latency_ms": round(sum(lats) / len(lats), 1) if lats else 0,
                "p99_latency_ms": round(
                    sorted(lats)[int(len(lats) * 0.99)] if lats else 0, 1
                ),
            }

        return {
            "total_requests": total_requests,
            "total_errors": total_errors,
            "errors_by_type": errors_by_type,
            "avg_latency_ms": round(avg_latency, 1),
            "agents": per_agent,
        }
    except Exception:
        logger.warning("Redis metrics read error, falling back to in-memory")
        return _get_metrics_memory()


def _get_metrics_memory() -> dict:
    with _lock:
        avg_latency = sum(_latencies) / len(_latencies) if _latencies else 0
        per_agent = {}
        for name, lats in _agent_latencies.items():
            per_agent[name] = {
                "requests": _requests_by_agent[name],
                "avg_latency_ms": round(sum(lats) / len(lats), 1) if lats else 0,
                "p99_latency_ms": round(
                    sorted(lats)[int(len(lats) * 0.99)] if lats else 0, 1
                ),
            }
        return {
            "total_requests": _total_requests,
            "total_errors": _total_errors,
            "errors_by_type": dict(_errors_by_type),
            "avg_latency_ms": round(avg_latency, 1),
            "agents": per_agent,
        }


def reset():
    """Reset all metrics (for testing)."""
    global _total_requests, _total_errors, _latencies, _redis_checked
    _redis_checked = False
    with _lock:
        _total_requests = 0
        _total_errors = 0
        _errors_by_type.clear()
        _requests_by_agent.clear()
        _latencies.clear()
        _agent_latencies.clear()


class Timer:
    """Context manager to measure elapsed time in milliseconds."""

    def __enter__(self):
        self.start = time.perf_counter()
        return self

    @property
    def elapsed_ms(self) -> float:
        return (time.perf_counter() - self.start) * 1000

    def __exit__(self, *args):
        pass
