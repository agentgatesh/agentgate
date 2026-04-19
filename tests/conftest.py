"""Shared test fixtures."""

import pytest


@pytest.fixture(autouse=True)
def _reset_rate_limiters():
    """Reset rate limiters between tests to prevent cross-test interference."""
    from agentgate.server.ratelimit import (
        admin_login_limiter,
        auth_limiter,
        task_limiter,
    )

    auth_limiter._buckets.clear()
    admin_login_limiter._buckets.clear()
    task_limiter._buckets.clear()
    yield
    auth_limiter._buckets.clear()
    admin_login_limiter._buckets.clear()
    task_limiter._buckets.clear()
