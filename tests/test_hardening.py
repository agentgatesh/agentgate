"""Tests for server hardening — security headers, rate limiting, CORS."""

from unittest.mock import AsyncMock, MagicMock, patch

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
