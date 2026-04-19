"""Tests for auth_routes — email/password login, session cookies, OAuth flows."""

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient

from agentgate.server.app import app
from agentgate.server.auth_routes import (
    _make_session,
    _verify_session,
    hash_password,
    verify_password,
)

client = TestClient(app)


# ---------------------------------------------------------------------------
# Password hashing
# ---------------------------------------------------------------------------


def test_hash_password_produces_salt_and_key():
    h = hash_password("testpass")
    assert ":" in h
    salt_hex, key_hex = h.split(":")
    assert len(salt_hex) == 32  # 16 bytes hex
    assert len(key_hex) == 64  # 32 bytes hex


def test_verify_password_correct():
    h = hash_password("mypassword")
    assert verify_password("mypassword", h) is True


def test_verify_password_wrong():
    h = hash_password("mypassword")
    assert verify_password("wrongpassword", h) is False


def test_verify_password_invalid_format():
    assert verify_password("anything", "not-a-valid-hash") is False


def test_verify_password_empty_stored():
    assert verify_password("anything", "") is False


# ---------------------------------------------------------------------------
# Session tokens
# ---------------------------------------------------------------------------


def test_make_and_verify_session():
    token = _make_session("org-123", "user@test.com")
    payload = _verify_session(token)
    assert payload is not None
    assert payload["org_id"] == "org-123"
    assert payload["email"] == "user@test.com"


def test_verify_session_tampered():
    token = _make_session("org-123", "user@test.com")
    # Tamper with the signature
    parts = token.rsplit("|", 1)
    tampered = parts[0] + "|" + "a" * 64
    assert _verify_session(tampered) is None


def test_verify_session_expired():

    with patch("agentgate.server.auth_routes.time") as mock_time:
        # Create session in the past
        mock_time.time.return_value = 1000
        token = _make_session("org-123", "user@test.com")

    # Verify with current time (way past expiry)
    assert _verify_session(token) is None


def test_verify_session_invalid_format():
    assert _verify_session("no-pipe-here") is None
    assert _verify_session("") is None


# ---------------------------------------------------------------------------
# POST /auth/login
# ---------------------------------------------------------------------------


def _make_fake_org(**kwargs):
    defaults = {
        "id": uuid.uuid4(),
        "name": "test-org",
        "email": "test@example.com",
        "password_hash": hash_password("testpass123"),
        "oauth_provider": None,
        "oauth_id": None,
        "api_key_hash": "fakehash",
        "tier": "free",
        "balance": 0.0,
        "rate_limit": 10.0,
        "rate_burst": 20,
        "cost_per_invocation": 0.001,
        "billing_alert_threshold": None,
        "secondary_api_key_hash": None,
        "created_at": datetime.now(timezone.utc),
    }
    defaults.update(kwargs)
    org = MagicMock()
    for k, v in defaults.items():
        setattr(org, k, v)
    return org


def _mock_db_with_org(org):
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = org
    mock_result.scalar.return_value = 0  # used by session-revocation count query
    mock_session = AsyncMock()
    mock_session.add = MagicMock()
    mock_session.execute = AsyncMock(return_value=mock_result)
    mock_ctx = AsyncMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_ctx.__aexit__ = AsyncMock(return_value=False)
    return MagicMock(return_value=mock_ctx)


def test_login_success():
    org = _make_fake_org()
    mock_factory = _mock_db_with_org(org)

    with patch("agentgate.server.auth_routes.async_session", mock_factory):
        response = client.post(
            "/auth/login",
            json={"email": "test@example.com", "password": "testpass123"},
        )

    assert response.status_code == 200
    data = response.json()
    assert data["message"] == "Login successful"
    assert data["org_id"] == str(org.id)
    assert "session" in response.cookies


def test_login_wrong_password():
    org = _make_fake_org()
    mock_factory = _mock_db_with_org(org)

    with patch("agentgate.server.auth_routes.async_session", mock_factory):
        response = client.post(
            "/auth/login",
            json={"email": "test@example.com", "password": "wrongpass"},
        )

    assert response.status_code == 401
    assert "Invalid email or password" in response.json()["detail"]


def test_login_nonexistent_email():
    mock_factory = _mock_db_with_org(None)

    with patch("agentgate.server.auth_routes.async_session", mock_factory):
        response = client.post(
            "/auth/login",
            json={"email": "nobody@example.com", "password": "somepass"},
        )

    assert response.status_code == 401


def test_login_missing_fields():
    response = client.post("/auth/login", json={"email": "", "password": ""})
    assert response.status_code == 400


def test_login_no_password_hash():
    """OAuth-only user trying email/password login."""
    org = _make_fake_org(password_hash=None)
    mock_factory = _mock_db_with_org(org)

    with patch("agentgate.server.auth_routes.async_session", mock_factory):
        response = client.post(
            "/auth/login",
            json={"email": "test@example.com", "password": "testpass123"},
        )

    assert response.status_code == 401


# ---------------------------------------------------------------------------
# POST /auth/logout
# ---------------------------------------------------------------------------


def test_logout():
    response = client.post("/auth/logout")
    assert response.status_code == 200
    assert response.json()["message"] == "Logged out"


# ---------------------------------------------------------------------------
# GET /auth/me
# ---------------------------------------------------------------------------


def test_me_authenticated():
    org = _make_fake_org()
    mock_factory = _mock_db_with_org(org)

    with patch("agentgate.server.auth_routes.async_session", mock_factory):
        # First login to get cookie
        login_resp = client.post(
            "/auth/login",
            json={"email": "test@example.com", "password": "testpass123"},
        )
        session_cookie = login_resp.cookies.get("session")

    # Use cookie for /auth/me
    with patch("agentgate.server.auth_routes.async_session", mock_factory):
        response = client.get(
            "/auth/me",
            cookies={"session": session_cookie},
        )

    assert response.status_code == 200
    data = response.json()
    assert data["org_id"] == str(org.id)
    assert data["email"] == org.email
    assert data["tier"] == org.tier


def test_me_no_cookie():
    response = client.get("/auth/me")
    assert response.status_code == 401


def test_me_invalid_cookie():
    response = client.get("/auth/me", cookies={"session": "invalid-token"})
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# GET /auth/google (redirect)
# ---------------------------------------------------------------------------


def test_google_redirect():
    with patch("agentgate.server.auth_routes.settings") as mock_settings:
        mock_settings.google_client_id = "test-google-id"
        mock_settings.base_url = "http://localhost:8000"
        mock_settings.debug = True
        response = client.get("/auth/google", follow_redirects=False)

    assert response.status_code == 302
    location = response.headers["location"]
    assert "accounts.google.com" in location
    assert "test-google-id" in location
    assert "oauth_state" in response.cookies


def test_google_redirect_not_configured():
    with patch("agentgate.server.auth_routes.settings") as mock_settings:
        mock_settings.google_client_id = ""
        response = client.get("/auth/google")

    assert response.status_code == 500


# ---------------------------------------------------------------------------
# GET /auth/github (redirect)
# ---------------------------------------------------------------------------


def test_github_redirect():
    with patch("agentgate.server.auth_routes.settings") as mock_settings:
        mock_settings.github_client_id = "test-github-id"
        mock_settings.base_url = "http://localhost:8000"
        mock_settings.debug = True
        response = client.get("/auth/github", follow_redirects=False)

    assert response.status_code == 302
    location = response.headers["location"]
    assert "github.com" in location
    assert "test-github-id" in location
    assert "oauth_state" in response.cookies


def test_github_redirect_not_configured():
    with patch("agentgate.server.auth_routes.settings") as mock_settings:
        mock_settings.github_client_id = ""
        response = client.get("/auth/github")

    assert response.status_code == 500


# ---------------------------------------------------------------------------
# Google callback — state validation
# ---------------------------------------------------------------------------


def test_google_callback_invalid_state():
    response = client.get(
        "/auth/google/callback?code=testcode&state=bad",
        cookies={"oauth_state": "different"},
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert "error=invalid_state" in response.headers["location"]


def test_google_callback_no_code():
    response = client.get(
        "/auth/google/callback?state=abc",
        cookies={"oauth_state": "abc"},
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert "error=invalid_state" in response.headers["location"]


# ---------------------------------------------------------------------------
# GitHub callback — state validation
# ---------------------------------------------------------------------------


def test_github_callback_invalid_state():
    response = client.get(
        "/auth/github/callback?code=testcode&state=bad",
        cookies={"oauth_state": "different"},
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert "error=invalid_state" in response.headers["location"]


def test_github_callback_no_code():
    response = client.get(
        "/auth/github/callback?state=abc",
        cookies={"oauth_state": "abc"},
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert "error=invalid_state" in response.headers["location"]


# ---------------------------------------------------------------------------
# Login page
# ---------------------------------------------------------------------------


def test_login_page():
    response = client.get("/login")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "Sign in" in response.text
    assert "Google" in response.text
    assert "GitHub" in response.text


# ---------------------------------------------------------------------------
# Signup with password
# ---------------------------------------------------------------------------


def test_signup_with_password():
    """POST /orgs/signup with password should hash and store it."""
    mock_result_none = MagicMock()
    mock_result_none.scalar_one_or_none.return_value = None

    mock_session = AsyncMock()
    mock_session.add = MagicMock()
    mock_session.execute = AsyncMock(return_value=mock_result_none)
    mock_session.commit = AsyncMock()
    mock_session.refresh = AsyncMock()

    mock_ctx = AsyncMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_ctx.__aexit__ = AsyncMock(return_value=False)
    mock_factory = MagicMock(return_value=mock_ctx)

    with patch("agentgate.server.org_routes.async_session", mock_factory):
        response = client.post(
            "/orgs/signup",
            json={
                "name": "new-org",
                "email": "new@example.com",
                "password": "securepass123",
            },
        )

    assert response.status_code == 201
    data = response.json()
    assert "api_key" in data
    assert data["org_name"] == "new-org"

    # Verify password was hashed (check that add was called with org that has password_hash)
    added_org = mock_session.add.call_args[0][0]
    assert added_org.password_hash is not None
    assert ":" in added_org.password_hash
