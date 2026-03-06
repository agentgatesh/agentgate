"""Lightweight in-memory metrics for task routing."""

import time
from collections import defaultdict
from threading import Lock

_lock = Lock()

# Global counters
_total_requests: int = 0
_total_errors: int = 0
_errors_by_type: dict[str, int] = defaultdict(int)
_requests_by_agent: dict[str, int] = defaultdict(int)
_latencies: list[float] = []
_agent_latencies: dict[str, list[float]] = defaultdict(list)

# Keep last N latencies to avoid unbounded memory
_MAX_LATENCIES = 1000


def record_request(agent_name: str, latency_ms: float, error_type: str | None = None):
    """Record a task routing request."""
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
    global _total_requests, _total_errors, _latencies
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
