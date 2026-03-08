"""Tests for account_routes — session cookie auth, dashboard, agents, billing, profile."""

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient

from agentgate.server.app import app
from agentgate.server.auth_routes import _make_session, hash_password

client = TestClient(app)


# ---------------------------------------------------------------------------
# Helpers
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
        "balance": 5.0,
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


def _session_cookie(org):
    return _make_session(str(org.id), org.email or "")


def _mock_db_returning(value):
    """Mock async_session that returns `value` for scalar_one_or_none and session.get."""
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = value
    mock_result.scalars.return_value.all.return_value = []
    mock_result.scalar.return_value = 0
    mock_result.one.return_value = MagicMock(
        total=0, errors=0, total_spent=0, total_earned=0,
        total_fees=0, tx_count=0,
    )
    mock_result.all.return_value = []

    mock_session = AsyncMock()
    mock_session.add = MagicMock()
    mock_session.execute = AsyncMock(return_value=mock_result)
    mock_session.get = AsyncMock(return_value=value)
    mock_session.commit = AsyncMock()
    mock_session.refresh = AsyncMock()

    mock_ctx = AsyncMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_ctx.__aexit__ = AsyncMock(return_value=False)
    return MagicMock(return_value=mock_ctx), mock_session


# ---------------------------------------------------------------------------
# Auth guard — all endpoints require session cookie
# ---------------------------------------------------------------------------


def test_dashboard_no_cookie():
    response = client.get("/account/api/dashboard")
    assert response.status_code == 401


def test_agents_no_cookie():
    response = client.get("/account/api/agents")
    assert response.status_code == 401


def test_billing_no_cookie():
    response = client.get("/account/api/billing")
    assert response.status_code == 401


def test_transactions_no_cookie():
    response = client.get("/account/api/transactions")
    assert response.status_code == 401


def test_profile_no_cookie():
    response = client.get("/account/api/profile")
    assert response.status_code == 401


def test_change_password_no_cookie():
    response = client.post("/account/api/change-password", json={"new_password": "newpass123"})
    assert response.status_code == 401


def test_reset_key_no_cookie():
    response = client.post("/account/api/reset-key")
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------


def test_dashboard_authenticated():
    org = _make_fake_org()
    mock_factory, _ = _mock_db_returning(org)

    cookie = _session_cookie(org)
    with patch("agentgate.server.auth_routes.async_session", mock_factory), \
         patch("agentgate.server.account_routes.async_session", mock_factory):
        response = client.get("/account/api/dashboard", cookies={"session": cookie})

    assert response.status_code == 200
    data = response.json()
    assert data["org_name"] == "test-org"
    assert data["tier"] == "free"
    assert "agent_count" in data
    assert "total_tasks" in data
    assert "balance" in data


# ---------------------------------------------------------------------------
# Agents
# ---------------------------------------------------------------------------


def test_agents_authenticated():
    org = _make_fake_org()
    mock_factory, _ = _mock_db_returning(org)

    cookie = _session_cookie(org)
    with patch("agentgate.server.auth_routes.async_session", mock_factory), \
         patch("agentgate.server.account_routes.async_session", mock_factory):
        response = client.get("/account/api/agents", cookies={"session": cookie})

    assert response.status_code == 200
    assert isinstance(response.json(), list)


# ---------------------------------------------------------------------------
# Billing
# ---------------------------------------------------------------------------


def test_billing_authenticated():
    org = _make_fake_org()
    mock_factory, _ = _mock_db_returning(org)

    cookie = _session_cookie(org)
    with patch("agentgate.server.auth_routes.async_session", mock_factory), \
         patch("agentgate.server.account_routes.async_session", mock_factory):
        response = client.get("/account/api/billing", cookies={"session": cookie})

    assert response.status_code == 200
    data = response.json()
    assert "balance" in data
    assert "tier" in data
    assert "tier_limits" in data
    assert "total_spent" in data


# ---------------------------------------------------------------------------
# Transactions
# ---------------------------------------------------------------------------


def test_transactions_authenticated():
    org = _make_fake_org()
    mock_factory, _ = _mock_db_returning(org)

    cookie = _session_cookie(org)
    with patch("agentgate.server.auth_routes.async_session", mock_factory), \
         patch("agentgate.server.account_routes.async_session", mock_factory):
        response = client.get("/account/api/transactions", cookies={"session": cookie})

    assert response.status_code == 200
    assert isinstance(response.json(), list)


# ---------------------------------------------------------------------------
# Profile
# ---------------------------------------------------------------------------


def test_profile_authenticated():
    org = _make_fake_org()
    mock_factory, _ = _mock_db_returning(org)

    cookie = _session_cookie(org)
    with patch("agentgate.server.auth_routes.async_session", mock_factory), \
         patch("agentgate.server.account_routes.async_session", mock_factory):
        response = client.get("/account/api/profile", cookies={"session": cookie})

    assert response.status_code == 200
    data = response.json()
    assert data["org_name"] == "test-org"
    assert data["email"] == "test@example.com"
    assert data["has_password"] is True
    assert data["tier"] == "free"


def test_profile_oauth_user():
    org = _make_fake_org(password_hash=None, oauth_provider="google")
    mock_factory, _ = _mock_db_returning(org)

    cookie = _session_cookie(org)
    with patch("agentgate.server.auth_routes.async_session", mock_factory), \
         patch("agentgate.server.account_routes.async_session", mock_factory):
        response = client.get("/account/api/profile", cookies={"session": cookie})

    assert response.status_code == 200
    data = response.json()
    assert data["has_password"] is False
    assert data["oauth_provider"] == "google"


# ---------------------------------------------------------------------------
# Change password
# ---------------------------------------------------------------------------


def test_change_password_success():
    org = _make_fake_org()
    mock_factory, mock_session = _mock_db_returning(org)

    cookie = _session_cookie(org)
    with patch("agentgate.server.auth_routes.async_session", mock_factory), \
         patch("agentgate.server.account_routes.async_session", mock_factory):
        response = client.post(
            "/account/api/change-password",
            json={"current_password": "testpass123", "new_password": "newpass12345"},
            cookies={"session": cookie},
        )

    assert response.status_code == 200
    assert "updated" in response.json()["message"].lower()


def test_change_password_wrong_current():
    org = _make_fake_org()
    mock_factory, _ = _mock_db_returning(org)

    cookie = _session_cookie(org)
    with patch("agentgate.server.auth_routes.async_session", mock_factory), \
         patch("agentgate.server.account_routes.async_session", mock_factory):
        response = client.post(
            "/account/api/change-password",
            json={"current_password": "wrongpass", "new_password": "newpass12345"},
            cookies={"session": cookie},
        )

    assert response.status_code == 401


def test_change_password_too_short():
    org = _make_fake_org()
    mock_factory, _ = _mock_db_returning(org)

    cookie = _session_cookie(org)
    with patch("agentgate.server.auth_routes.async_session", mock_factory), \
         patch("agentgate.server.account_routes.async_session", mock_factory):
        response = client.post(
            "/account/api/change-password",
            json={"current_password": "testpass123", "new_password": "short"},
            cookies={"session": cookie},
        )

    assert response.status_code == 400


def test_set_password_oauth_user():
    """OAuth-only user setting password for first time (no current_password needed)."""
    org = _make_fake_org(password_hash=None, oauth_provider="github")
    mock_factory, _ = _mock_db_returning(org)

    cookie = _session_cookie(org)
    with patch("agentgate.server.auth_routes.async_session", mock_factory), \
         patch("agentgate.server.account_routes.async_session", mock_factory):
        response = client.post(
            "/account/api/change-password",
            json={"new_password": "newpass12345"},
            cookies={"session": cookie},
        )

    assert response.status_code == 200


# ---------------------------------------------------------------------------
# Reset API key
# ---------------------------------------------------------------------------


def test_reset_key_success():
    org = _make_fake_org()
    mock_factory, _ = _mock_db_returning(org)

    cookie = _session_cookie(org)
    with patch("agentgate.server.auth_routes.async_session", mock_factory), \
         patch("agentgate.server.account_routes.async_session", mock_factory):
        response = client.post("/account/api/reset-key", cookies={"session": cookie})

    assert response.status_code == 200
    data = response.json()
    assert "api_key" in data
    assert len(data["api_key"]) > 20


# ---------------------------------------------------------------------------
# Account page HTML
# ---------------------------------------------------------------------------


def test_account_page_redirects_to_login():
    response = client.get("/account", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"] == "/login"


def test_account_page_authenticated():
    org = _make_fake_org()
    mock_factory, _ = _mock_db_returning(org)

    cookie = _session_cookie(org)
    with patch("agentgate.server.auth_routes.async_session", mock_factory):
        response = client.get("/account", cookies={"session": cookie})

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "Dashboard" in response.text
