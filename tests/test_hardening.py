"""Tests for server hardening — security headers, rate limiting, CORS."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from agentgate.server.app import app

client = TestClient(app)


# ---------------------------------------------------------------------------
# Security headers
# ---------------------------------------------------------------------------


def test_security_headers_present():
    response = client.get("/health")
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"
    assert "camera=()" in response.headers["Permissions-Policy"]


def test_hsts_not_in_debug():
    """HSTS should not be set in debug/test mode."""
    response = client.get("/health")
    # debug is False by default in tests, so HSTS should be present
    # If debug were True, it would be absent
    assert "Strict-Transport-Security" in response.headers


def test_security_headers_on_html_pages():
    response = client.get("/")
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["X-Content-Type-Options"] == "nosniff"


def test_security_headers_on_api_endpoints():
    response = client.get("/health/agents")
    assert response.headers["X-Content-Type-Options"] == "nosniff"


# ---------------------------------------------------------------------------
# API deprecation header — legacy /agents etc. vs /v1/agents
# ---------------------------------------------------------------------------


def test_legacy_api_path_has_deprecation_header():
    # /deploy/... requires auth so it returns 401 without DB access, but
    # the middleware still runs and stamps the deprecation header.
    response = client.get("/deploy/missing/status")
    assert response.headers.get("Deprecation") == "true"
    assert response.headers.get("Sunset")
    link = response.headers.get("Link", "")
    assert "/v1/deploy/" in link
    assert 'rel="successor-version"' in link


def test_v1_api_path_has_no_deprecation_header():
    response = client.get("/v1/deploy/missing/status")
    assert "Deprecation" not in response.headers


def test_non_api_path_has_no_deprecation_header():
    response = client.get("/health")
    assert "Deprecation" not in response.headers
    response = client.get("/")
    assert "Deprecation" not in response.headers


# ---------------------------------------------------------------------------
# CORS
# ---------------------------------------------------------------------------


def test_cors_preflight():
    response = client.options(
        "/health",
        headers={
            "Origin": "https://agentgate.sh",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert response.headers.get("access-control-allow-origin") == "https://agentgate.sh"


def test_cors_rejects_unknown_origin():
    response = client.options(
        "/health",
        headers={
            "Origin": "https://evil.com",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert response.headers.get("access-control-allow-origin") != "https://evil.com"


# ---------------------------------------------------------------------------
# Auth rate limiting
# ---------------------------------------------------------------------------


def _mock_db_empty():
    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None
    mock_session.execute = AsyncMock(return_value=mock_result)
    mock_factory = MagicMock()
    mock_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
    mock_factory.return_value.__aexit__ = AsyncMock(return_value=False)
    return mock_factory


def test_login_rate_limit():
    """Login should be rate-limited after too many attempts."""
    from agentgate.server.ratelimit import auth_limiter

    # Clear any existing state
    auth_limiter._buckets.clear()

    mock_factory = _mock_db_empty()

    with patch("agentgate.server.auth_routes.async_session", mock_factory):
        # Exhaust the burst (5 attempts)
        for _ in range(5):
            client.post("/auth/login", json={"email": "test@x.com", "password": "wrong"})

        # 6th attempt should be rate-limited
        response = client.post("/auth/login", json={"email": "test@x.com", "password": "wrong"})
        assert response.status_code == 429
        assert "Too many" in response.json()["detail"]


def test_admin_login_rate_limit():
    """Admin login should be rate-limited after too many attempts."""
    from agentgate.server.ratelimit import auth_limiter

    auth_limiter._buckets.clear()

    # Exhaust the burst
    for _ in range(5):
        client.post("/admin/api/login", json={"username": "x", "password": "y"})

    response = client.post("/admin/api/login", json={"username": "x", "password": "y"})
    assert response.status_code == 429


def test_signup_rate_limit():
    """Signup should be rate-limited after too many attempts."""
    from agentgate.server.ratelimit import auth_limiter

    auth_limiter._buckets.clear()

    mock_factory = _mock_db_empty()

    with patch("agentgate.server.org_routes.async_session", mock_factory):
        for _ in range(5):
            client.post("/orgs/signup", json={"name": "test", "email": "t@x.com"})

        response = client.post("/orgs/signup", json={"name": "test", "email": "t@x.com"})
        assert response.status_code == 429


# ---------------------------------------------------------------------------
# Disposable-email block
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_disposable_list_loaded():
    """The service loads either the bundled list or the hardcoded fallback."""
    from agentgate.server.disposable import list_size

    assert list_size() > 0


@pytest.mark.asyncio
async def test_known_disposable_domain_is_blocked():
    """mailinator.com is a canonical throwaway and must be flagged."""
    from agentgate.server.disposable import is_disposable

    assert await is_disposable("attacker@mailinator.com") is True


@pytest.mark.asyncio
async def test_real_domain_is_not_blocked():
    """A normal mainstream domain should pass."""
    from agentgate.server.disposable import is_disposable

    assert await is_disposable("user@gmail.com") is False


@pytest.mark.asyncio
async def test_empty_email_is_not_blocked():
    """Empty / malformed input must short-circuit to False (fail open)."""
    from agentgate.server.disposable import is_disposable

    assert await is_disposable("") is False
    assert await is_disposable("no-at-sign") is False


def test_signup_rejects_disposable_generic_message():
    """POST /orgs/signup with a throwaway domain returns 400 'Invalid email'."""
    response = client.post(
        "/orgs/signup",
        json={
            "name": "test-throwaway-" + str(hash("nonce"))[:6],
            "email": "attacker@mailinator.com",
            "password": "strong-password-1",
        },
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "Invalid email"
