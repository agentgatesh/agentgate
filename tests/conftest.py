"""Shared test fixtures."""

import pytest


@pytest.fixture(autouse=True)
def _reset_auth_limiter():
    """Reset auth rate limiter between tests to prevent cross-test interference."""
    from agentgate.server.ratelimit import auth_limiter

    yield
    auth_limiter._buckets.clear()
