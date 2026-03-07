from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from agentgate.server.app import app

client = TestClient(app)


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


def test_health():
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert "version" in data


# ---------------------------------------------------------------------------
# Landing page
# ---------------------------------------------------------------------------


def test_landing_page():
    response = client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "<title>" in response.text
    assert "AgentGate" in response.text


def test_landing_page_seo_meta():
    response = client.get("/")
    html = response.text
    assert 'meta name="description"' in html
    assert 'property="og:title"' in html
    assert 'rel="canonical"' in html
    assert "application/ld+json" in html


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------


def test_dashboard_page():
    response = client.get("/dashboard")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "Dashboard" in response.text
    assert "AgentGate" in response.text


# ---------------------------------------------------------------------------
# Auth — POST /agents/ requires API key
# ---------------------------------------------------------------------------


def test_register_agent_no_auth():
    response = client.post("/agents/", json={"name": "test", "url": "http://test.com"})
    assert response.status_code == 401


def test_register_agent_wrong_key():
    response = client.post(
        "/agents/",
        json={"name": "test", "url": "http://test.com"},
        headers={"Authorization": "Bearer wrong-key"},
    )
    assert response.status_code in (401, 500)


# ---------------------------------------------------------------------------
# GET /agents/ — public, no auth
# ---------------------------------------------------------------------------


def _mock_async_session_with_agents(agents):
    """Create a mock async_session that returns the given agents."""
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = agents

    mock_session = AsyncMock()
    mock_session.execute.return_value = mock_result
    mock_session.get = AsyncMock(side_effect=lambda model, id: next(
        (a for a in agents if a.id == id), None
    ))

    mock_ctx = AsyncMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_ctx.__aexit__ = AsyncMock(return_value=False)

    mock_factory = MagicMock(return_value=mock_ctx)
    return mock_factory


def _make_fake_agent(**kwargs):
    """Create a fake agent object with default values."""
    import uuid
    from datetime import datetime, timezone

    defaults = {
        "id": uuid.uuid4(),
        "name": "test-agent",
        "description": "A test agent",
        "url": "http://test.com",
        "version": "1.0.0",
        "skills": [{"id": "echo", "name": "Echo", "description": "Echoes input"}],
        "auth_type": "none",
        "api_key_hash": None,
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
    }
    defaults.update(kwargs)
    agent = MagicMock()
    for k, v in defaults.items():
        setattr(agent, k, v)
    return agent


def test_list_agents_empty():
    mock_factory = _mock_async_session_with_agents([])
    with patch("agentgate.server.routes.async_session", mock_factory):
        response = client.get("/agents/")
    assert response.status_code == 200
    assert response.json() == []


def test_list_agents_with_data():
    agent = _make_fake_agent(name="my-agent")
    mock_factory = _mock_async_session_with_agents([agent])
    with patch("agentgate.server.routes.async_session", mock_factory):
        response = client.get("/agents/")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["name"] == "my-agent"


# ---------------------------------------------------------------------------
# GET /agents/{id} — public
# ---------------------------------------------------------------------------


def test_get_agent_by_id():
    import uuid

    agent_id = uuid.uuid4()
    agent = _make_fake_agent(id=agent_id, name="found-agent")
    mock_factory = _mock_async_session_with_agents([agent])
    with patch("agentgate.server.routes.async_session", mock_factory):
        response = client.get(f"/agents/{agent_id}")
    assert response.status_code == 200
    assert response.json()["name"] == "found-agent"


def test_get_agent_not_found():
    import uuid

    mock_factory = _mock_async_session_with_agents([])
    with patch("agentgate.server.routes.async_session", mock_factory):
        response = client.get(f"/agents/{uuid.uuid4()}")
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# DELETE /agents/{id} — requires auth
# ---------------------------------------------------------------------------


def test_delete_agent_no_auth():
    import uuid

    response = client.delete(f"/agents/{uuid.uuid4()}")
    assert response.status_code == 401


def test_delete_agent_success():
    import uuid

    agent_id = uuid.uuid4()
    agent = _make_fake_agent(id=agent_id)
    mock_factory = _mock_async_session_with_agents([agent])
    with patch("agentgate.server.routes.async_session", mock_factory), \
         patch("agentgate.server.routes.settings") as mock_settings:
        mock_settings.api_key = "test-key"
        response = client.delete(
            f"/agents/{agent_id}",
            headers={"Authorization": "Bearer test-key"},
        )
    assert response.status_code == 204


def test_delete_agent_not_found():
    import uuid

    mock_factory = _mock_async_session_with_agents([])
    with patch("agentgate.server.routes.settings") as mock_settings, \
         patch("agentgate.server.routes.async_session", mock_factory):
        mock_settings.api_key = "test-key"
        response = client.delete(
            f"/agents/{uuid.uuid4()}",
            headers={"Authorization": "Bearer test-key"},
        )
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# PUT /agents/{id} — requires auth
# ---------------------------------------------------------------------------


def test_update_agent_no_auth():
    import uuid

    response = client.put(
        f"/agents/{uuid.uuid4()}",
        json={"name": "new-name"},
    )
    assert response.status_code == 401


def test_update_agent_success():
    import uuid

    agent_id = uuid.uuid4()
    agent = _make_fake_agent(id=agent_id, name="old-name")
    mock_factory = _mock_async_session_with_agents([agent])
    with patch("agentgate.server.routes.async_session", mock_factory), \
         patch("agentgate.server.routes.settings") as mock_settings:
        mock_settings.api_key = "test-key"
        response = client.put(
            f"/agents/{agent_id}",
            json={"name": "new-name"},
            headers={"Authorization": "Bearer test-key"},
        )
    assert response.status_code == 200
    assert response.json()["name"] == "new-name"


def test_update_agent_not_found():
    import uuid

    mock_factory = _mock_async_session_with_agents([])
    with patch("agentgate.server.routes.settings") as mock_settings, \
         patch("agentgate.server.routes.async_session", mock_factory):
        mock_settings.api_key = "test-key"
        response = client.put(
            f"/agents/{uuid.uuid4()}",
            json={"name": "new-name"},
            headers={"Authorization": "Bearer test-key"},
        )
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# POST /agents/{id}/task — A2A routing (proxy)
# ---------------------------------------------------------------------------


def test_route_task_agent_not_found():
    import uuid

    mock_factory = _mock_async_session_with_agents([])
    with patch("agentgate.server.routes.async_session", mock_factory):
        response = client.post(
            f"/agents/{uuid.uuid4()}/task",
            json={"id": "task-1", "message": {"parts": [{"type": "text", "text": "Hi"}]}},
        )
    assert response.status_code == 404


def test_route_task_success():
    import uuid

    agent_id = uuid.uuid4()
    agent = _make_fake_agent(id=agent_id, url="http://fake-agent:9000")
    mock_factory = _mock_async_session_with_agents([agent])

    a2a_response = {
        "id": "task-1",
        "status": {"state": "completed"},
        "artifacts": [{"parts": [{"type": "text", "text": "Echo: Hi"}]}],
    }

    mock_httpx_response = MagicMock()
    mock_httpx_response.status_code = 200
    mock_httpx_response.json.return_value = a2a_response

    with patch("agentgate.server.routes.async_session", mock_factory), \
         patch("agentgate.server.routes.httpx.AsyncClient") as mock_client_cls:
        mock_client_instance = AsyncMock()
        mock_client_instance.post.return_value = mock_httpx_response
        mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
        mock_client_instance.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client_instance

        response = client.post(
            f"/agents/{agent_id}/task",
            json={"id": "task-1", "message": {"parts": [{"type": "text", "text": "Hi"}]}},
        )
    assert response.status_code == 200
    data = response.json()
    assert data["status"]["state"] == "completed"
    assert data["artifacts"][0]["parts"][0]["text"] == "Echo: Hi"


def test_route_task_agent_unreachable():
    import uuid

    import httpx as real_httpx

    agent_id = uuid.uuid4()
    agent = _make_fake_agent(id=agent_id, url="http://unreachable:9000")
    mock_factory = _mock_async_session_with_agents([agent])

    with patch("agentgate.server.routes.async_session", mock_factory), \
         patch("agentgate.server.routes.httpx.AsyncClient") as mock_client_cls:
        mock_client_instance = AsyncMock()
        mock_client_instance.post.side_effect = real_httpx.ConnectError("Connection refused")
        mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
        mock_client_instance.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client_instance

        response = client.post(
            f"/agents/{agent_id}/task",
            json={"id": "task-1", "message": {"parts": [{"type": "text", "text": "Hi"}]}},
        )
    assert response.status_code == 502


def test_route_task_agent_timeout():
    import uuid

    import httpx as real_httpx

    agent_id = uuid.uuid4()
    agent = _make_fake_agent(id=agent_id, url="http://slow-agent:9000")
    mock_factory = _mock_async_session_with_agents([agent])

    with patch("agentgate.server.routes.async_session", mock_factory), \
         patch("agentgate.server.routes.httpx.AsyncClient") as mock_client_cls:
        mock_client_instance = AsyncMock()
        mock_client_instance.post.side_effect = real_httpx.TimeoutException("Timed out")
        mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
        mock_client_instance.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client_instance

        response = client.post(
            f"/agents/{agent_id}/task",
            json={"id": "task-1", "message": {"parts": [{"type": "text", "text": "Hi"}]}},
        )
    assert response.status_code == 504


# ---------------------------------------------------------------------------
# POST /agents/{id}/task — metrics recording
# ---------------------------------------------------------------------------


def test_route_task_records_metrics():
    import uuid

    from agentgate.server import metrics
    metrics.reset()

    agent_id = uuid.uuid4()
    agent = _make_fake_agent(id=agent_id, name="metrics-agent", url="http://m:9000")
    mock_factory = _mock_async_session_with_agents([agent])

    a2a_response = {
        "id": "task-1",
        "status": {"state": "completed"},
        "artifacts": [{"parts": [{"type": "text", "text": "ok"}]}],
    }

    mock_httpx_response = MagicMock()
    mock_httpx_response.status_code = 200
    mock_httpx_response.json.return_value = a2a_response

    with patch("agentgate.server.routes.async_session", mock_factory), \
         patch("agentgate.server.routes.httpx.AsyncClient") as mock_client_cls:
        mock_client_instance = AsyncMock()
        mock_client_instance.post.return_value = mock_httpx_response
        mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
        mock_client_instance.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client_instance

        client.post(
            f"/agents/{agent_id}/task",
            json={"id": "task-1", "message": {"parts": [{"type": "text", "text": "Hi"}]}},
        )

    m = metrics.get_metrics()
    assert m["total_requests"] >= 1
    assert "metrics-agent" in m["agents"]
    assert m["agents"]["metrics-agent"]["requests"] >= 1
    assert m["agents"]["metrics-agent"]["avg_latency_ms"] >= 0
    metrics.reset()


# ---------------------------------------------------------------------------
# GET /metrics — metrics endpoint
# ---------------------------------------------------------------------------


def test_metrics_endpoint_no_auth():
    """GET /metrics without auth returns 401 when api_key is set."""
    with patch("agentgate.server.app.settings") as mock_settings:
        mock_settings.api_key = "test-key"
        response = client.get("/metrics")
    assert response.status_code == 401


def test_metrics_endpoint_with_auth():
    from agentgate.server import metrics
    metrics.reset()
    with patch("agentgate.server.app.settings") as mock_settings:
        mock_settings.api_key = "test-key"
        response = client.get(
            "/metrics",
            headers={"Authorization": "Bearer test-key"},
        )
    assert response.status_code == 200
    data = response.json()
    assert "total_requests" in data
    assert "total_errors" in data
    assert "agents" in data


def test_metrics_endpoint_no_key_configured():
    """GET /metrics without api_key configured is open."""
    from agentgate.server import metrics
    metrics.reset()
    with patch("agentgate.server.app.settings") as mock_settings:
        mock_settings.api_key = ""
        response = client.get("/metrics")
    assert response.status_code == 200


# ---------------------------------------------------------------------------
# Rate limiting — POST /agents/{id}/task
# ---------------------------------------------------------------------------


def test_rate_limiter_unit():
    from agentgate.server.ratelimit import RateLimiter

    limiter = RateLimiter(rate=2.0, burst=3)
    assert limiter.allow("ip1") is True
    assert limiter.allow("ip1") is True
    assert limiter.allow("ip1") is True
    assert limiter.allow("ip1") is False
    # Different key should still be allowed
    assert limiter.allow("ip2") is True


def test_route_task_rate_limited():
    import uuid

    from agentgate.server import ratelimit

    agent_id = uuid.uuid4()
    agent = _make_fake_agent(id=agent_id, url="http://rl:9000")
    mock_factory = _mock_async_session_with_agents([agent])

    # Replace the global limiter with a very strict one (burst=1)
    original = ratelimit.task_limiter
    ratelimit.task_limiter = ratelimit.RateLimiter(rate=0.0, burst=1)

    try:
        with patch("agentgate.server.routes.task_limiter", ratelimit.task_limiter), \
             patch("agentgate.server.routes.async_session", mock_factory), \
             patch("agentgate.server.routes.httpx.AsyncClient") as mock_cls:
            mock_inst = AsyncMock()
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {"id": "t", "status": {"state": "completed"}}
            mock_inst.post.return_value = mock_resp
            mock_inst.__aenter__ = AsyncMock(return_value=mock_inst)
            mock_inst.__aexit__ = AsyncMock(return_value=False)
            mock_cls.return_value = mock_inst

            payload = {"id": "t", "message": {"parts": [{"type": "text", "text": "x"}]}}
            r1 = client.post(f"/agents/{agent_id}/task", json=payload)
            assert r1.status_code == 200

            r2 = client.post(f"/agents/{agent_id}/task", json=payload)
            assert r2.status_code == 429
    finally:
        ratelimit.task_limiter = original


# ---------------------------------------------------------------------------
# GET /agents/{id}/card — Agent Card
# ---------------------------------------------------------------------------


def test_agent_card():
    import uuid

    agent_id = uuid.uuid4()
    agent = _make_fake_agent(id=agent_id, name="card-agent", description="Card test")
    mock_factory = _mock_async_session_with_agents([agent])
    with patch("agentgate.server.routes.async_session", mock_factory):
        response = client.get(f"/agents/{agent_id}/card")
    assert response.status_code == 200
    card = response.json()
    assert card["name"] == "card-agent"
    assert "provider" in card
    assert card["provider"]["organization"] == "AgentGate"
    assert "authentication" in card
    assert "skills" in card


# ---------------------------------------------------------------------------
# .well-known/agent.json
# ---------------------------------------------------------------------------


def test_well_known_agent_json():
    agent = _make_fake_agent(name="discovery-agent")
    mock_factory = _mock_async_session_with_agents([agent])
    with patch("agentgate.server.app.async_session", mock_factory):
        response = client.get("/.well-known/agent.json")
    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "AgentGate"
    assert "agents" in data
    assert len(data["agents"]) == 1
    assert data["agents"][0]["name"] == "discovery-agent"


# ---------------------------------------------------------------------------
# Schema validation
# ---------------------------------------------------------------------------


def test_agent_create_requires_name():
    from pydantic import ValidationError

    from agentgate.server.schemas import AgentCreate

    with pytest.raises(ValidationError):
        AgentCreate(url="http://test.com")


def test_agent_create_requires_url():
    from pydantic import ValidationError

    from agentgate.server.schemas import AgentCreate

    with pytest.raises(ValidationError):
        AgentCreate(name="test")


def test_agent_create_valid():
    from agentgate.server.schemas import AgentCreate

    agent = AgentCreate(name="test", url="http://test.com")
    assert agent.name == "test"
    assert agent.version == "1.0.0"
    assert agent.skills == []
