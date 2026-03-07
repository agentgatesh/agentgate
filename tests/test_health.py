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
    # verify_api_key_or_org checks admin key then DB for org key
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None
    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=mock_result)
    mock_ctx = AsyncMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_ctx.__aexit__ = AsyncMock(return_value=False)
    mock_factory = MagicMock(return_value=mock_ctx)

    with (
        patch("agentgate.server.routes.async_session", mock_factory),
        patch("agentgate.server.routes.settings") as mock_settings,
    ):
        mock_settings.api_key = "correct-key"
        response = client.post(
            "/agents/",
            json={"name": "test", "url": "http://test.com"},
            headers={"Authorization": "Bearer wrong-key"},
        )
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# GET /agents/ — public, no auth
# ---------------------------------------------------------------------------


def _mock_async_session_with_agents(agents):
    """Create a mock async_session that returns the given agents."""
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = agents
    mock_result.scalar_one_or_none.return_value = agents[0] if agents else None

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
        "tags": [],
        "auth_type": "none",
        "webhook_url": None,
        "api_key_hash": None,
        "org_id": None,
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
    assert agent.webhook_url is None


def test_agent_create_with_webhook():
    from agentgate.server.schemas import AgentCreate

    agent = AgentCreate(name="test", url="http://test.com", webhook_url="http://hook.com/notify")
    assert agent.webhook_url == "http://hook.com/notify"


# ---------------------------------------------------------------------------
# GET /agents/?skill= — filter by skill
# ---------------------------------------------------------------------------


def test_list_agents_filter_by_skill():
    agent1 = _make_fake_agent(
        name="calc-agent",
        skills=[{"id": "calculate", "name": "Calculate", "description": "Math"}],
    )
    agent2 = _make_fake_agent(
        name="echo-agent",
        skills=[{"id": "echo", "name": "Echo", "description": "Echoes input"}],
    )
    mock_factory = _mock_async_session_with_agents([agent1, agent2])
    with patch("agentgate.server.routes.async_session", mock_factory):
        response = client.get("/agents/?skill=calculate")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["name"] == "calc-agent"


def test_list_agents_filter_by_skill_no_match():
    agent1 = _make_fake_agent(
        name="echo-agent",
        skills=[{"id": "echo", "name": "Echo", "description": "Echoes input"}],
    )
    mock_factory = _mock_async_session_with_agents([agent1])
    with patch("agentgate.server.routes.async_session", mock_factory):
        response = client.get("/agents/?skill=nonexistent")
    assert response.status_code == 200
    assert response.json() == []


def test_list_agents_filter_by_skill_name():
    agent1 = _make_fake_agent(
        name="calc-agent",
        skills=[{"id": "calc", "name": "Calculate", "description": "Math"}],
    )
    mock_factory = _mock_async_session_with_agents([agent1])
    with patch("agentgate.server.routes.async_session", mock_factory):
        response = client.get("/agents/?skill=Calculate")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1


# ---------------------------------------------------------------------------
# GET /agents/{id}/health — agent health status
# ---------------------------------------------------------------------------


def test_agent_health_unknown():
    import uuid

    agent_id = uuid.uuid4()
    agent = _make_fake_agent(id=agent_id, name="health-agent")
    mock_factory = _mock_async_session_with_agents([agent])
    with patch("agentgate.server.routes.async_session", mock_factory):
        response = client.get(f"/agents/{agent_id}/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "unknown"
    assert data["agent"] == "health-agent"


def test_agent_health_with_data():
    import uuid

    from agentgate.server import healthcheck

    agent_id = uuid.uuid4()
    agent = _make_fake_agent(id=agent_id, name="health-agent-2")
    mock_factory = _mock_async_session_with_agents([agent])

    healthcheck._health_status[str(agent_id)] = {
        "status": "healthy",
        "last_check": "2026-03-07T12:00:00+00:00",
        "latency_ms": 42.0,
        "error": None,
    }

    try:
        with patch("agentgate.server.routes.async_session", mock_factory):
            response = client.get(f"/agents/{agent_id}/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert data["latency_ms"] == 42.0
    finally:
        healthcheck._health_status.pop(str(agent_id), None)


def test_agent_health_not_found():
    import uuid

    mock_factory = _mock_async_session_with_agents([])
    with patch("agentgate.server.routes.async_session", mock_factory):
        response = client.get(f"/agents/{uuid.uuid4()}/health")
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# GET /health/agents — all agents health
# ---------------------------------------------------------------------------


def test_all_agents_health():
    response = client.get("/health/agents")
    assert response.status_code == 200
    assert isinstance(response.json(), dict)


# ---------------------------------------------------------------------------
# Health check unit tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_check_agent_healthy():
    from agentgate.server import healthcheck

    mock_response = MagicMock()
    mock_response.status_code = 200

    with patch("agentgate.server.healthcheck.httpx.AsyncClient") as mock_cls:
        mock_inst = AsyncMock()
        mock_inst.get.return_value = mock_response
        mock_inst.__aenter__ = AsyncMock(return_value=mock_inst)
        mock_inst.__aexit__ = AsyncMock(return_value=False)
        mock_cls.return_value = mock_inst

        await healthcheck.check_agent("test-id", "test-agent", "http://test:9000")

    assert healthcheck._health_status["test-id"]["status"] == "healthy"
    healthcheck._health_status.pop("test-id", None)


@pytest.mark.asyncio
async def test_check_agent_unhealthy():
    import httpx as real_httpx

    from agentgate.server import healthcheck

    with patch("agentgate.server.healthcheck.httpx.AsyncClient") as mock_cls:
        mock_inst = AsyncMock()
        mock_inst.get.side_effect = real_httpx.ConnectError("Connection refused")
        mock_inst.__aenter__ = AsyncMock(return_value=mock_inst)
        mock_inst.__aexit__ = AsyncMock(return_value=False)
        mock_cls.return_value = mock_inst

        await healthcheck.check_agent("bad-id", "bad-agent", "http://bad:9000")

    assert healthcheck._health_status["bad-id"]["status"] == "unhealthy"
    assert healthcheck._health_status["bad-id"]["error"] == "connect_error"
    healthcheck._health_status.pop("bad-id", None)


# ---------------------------------------------------------------------------
# Version bump helper
# ---------------------------------------------------------------------------


def test_bump_version():
    from agentgate.cli.main import _bump_version

    assert _bump_version("0.1.0", "patch") == "0.1.1"
    assert _bump_version("0.1.0", "minor") == "0.2.0"
    assert _bump_version("0.1.0", "major") == "1.0.0"
    assert _bump_version("1.2.3", "patch") == "1.2.4"


# ---------------------------------------------------------------------------
# Agent logs endpoint — GET /agents/{id}/logs (auth required)
# ---------------------------------------------------------------------------


def test_agent_logs_no_auth():
    import uuid

    response = client.get(f"/agents/{uuid.uuid4()}/logs")
    assert response.status_code == 401


def test_agent_logs_empty():
    import uuid

    agent_id = uuid.uuid4()
    agent = _make_fake_agent(id=agent_id)

    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = []

    mock_session = AsyncMock()
    mock_session.get = AsyncMock(return_value=agent)
    mock_session.execute = AsyncMock(return_value=mock_result)

    mock_ctx = AsyncMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_ctx.__aexit__ = AsyncMock(return_value=False)
    mock_factory = MagicMock(return_value=mock_ctx)

    with (
        patch("agentgate.server.routes.async_session", mock_factory),
        patch("agentgate.server.routes.settings") as mock_settings,
    ):
        mock_settings.api_key = "test-key"
        response = client.get(
            f"/agents/{agent_id}/logs",
            headers={"Authorization": "Bearer test-key"},
        )
    assert response.status_code == 200
    assert response.json() == []


# ---------------------------------------------------------------------------
# Agent usage endpoint — GET /agents/{id}/usage (auth required)
# ---------------------------------------------------------------------------


def test_agent_usage_no_auth():
    import uuid

    response = client.get(f"/agents/{uuid.uuid4()}/usage")
    assert response.status_code == 401


def test_agent_usage_success():
    import uuid
    from datetime import datetime, timezone

    agent_id = uuid.uuid4()
    agent = _make_fake_agent(id=agent_id, name="usage-agent")

    # Mock the aggregation result
    mock_row = MagicMock()
    mock_row.total_invocations = 42
    mock_row.total_errors = 3
    mock_row.avg_latency_ms = 150.5
    mock_row.last_invocation = datetime.now(timezone.utc)

    mock_result = MagicMock()
    mock_result.one.return_value = mock_row

    mock_session = AsyncMock()
    mock_session.get = AsyncMock(return_value=agent)
    mock_session.execute = AsyncMock(return_value=mock_result)

    mock_ctx = AsyncMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_ctx.__aexit__ = AsyncMock(return_value=False)
    mock_factory = MagicMock(return_value=mock_ctx)

    with (
        patch("agentgate.server.routes.async_session", mock_factory),
        patch("agentgate.server.routes.settings") as mock_settings,
    ):
        mock_settings.api_key = "test-key"
        response = client.get(
            f"/agents/{agent_id}/usage",
            headers={"Authorization": "Bearer test-key"},
        )
    assert response.status_code == 200
    data = response.json()
    assert data["agent_name"] == "usage-agent"
    assert data["total_invocations"] == 42
    assert data["total_errors"] == 3
    assert data["avg_latency_ms"] == 150.5


# ---------------------------------------------------------------------------
# Per-agent API key auth
# ---------------------------------------------------------------------------


def test_agent_create_with_api_key():
    from agentgate.server.schemas import AgentCreate

    agent = AgentCreate(
        name="test", url="http://test.com", agent_api_key="secret-123",
    )
    assert agent.agent_api_key == "secret-123"
    # agent_api_key should be excluded from model_dump
    assert "agent_api_key" not in agent.model_dump()


def test_hash_api_key():
    from agentgate.server.routes import _hash_api_key

    h = _hash_api_key("test-key")
    assert len(h) == 64  # SHA-256 hex digest
    assert _hash_api_key("test-key") == h  # Deterministic


def test_route_task_per_agent_auth_required():
    """Agent with api_key_hash should require auth on task routing."""
    import uuid

    agent_id = uuid.uuid4()
    agent = _make_fake_agent(
        id=agent_id, name="protected-agent",
        api_key_hash="abc123",  # Has a key hash → requires auth
    )
    mock_factory = _mock_async_session_with_agents([agent])

    with patch("agentgate.server.routes.async_session", mock_factory):
        # No auth header → should be 401
        response = client.post(
            f"/agents/{agent_id}/task",
            json={"id": "t1", "message": {"parts": [{"type": "text", "text": "hi"}]}},
        )
    assert response.status_code == 401


def test_route_task_no_auth_when_no_key_hash():
    """Agent without api_key_hash should not require auth on task routing."""
    import uuid

    agent_id = uuid.uuid4()
    agent = _make_fake_agent(id=agent_id, name="open-agent", api_key_hash=None)
    mock_factory = _mock_async_session_with_agents([agent])

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"result": "ok"}

    with (
        patch("agentgate.server.routes.async_session", mock_factory),
        patch("agentgate.server.routes.httpx.AsyncClient") as mock_cls,
    ):
        mock_inst = AsyncMock()
        mock_inst.post.return_value = mock_response
        mock_inst.__aenter__ = AsyncMock(return_value=mock_inst)
        mock_inst.__aexit__ = AsyncMock(return_value=False)
        mock_cls.return_value = mock_inst

        response = client.post(
            f"/agents/{agent_id}/task",
            json={"id": "t1", "message": {"parts": [{"type": "text", "text": "hi"}]}},
        )
    assert response.status_code == 200


# ---------------------------------------------------------------------------
# TaskLog model
# ---------------------------------------------------------------------------


def test_task_log_model():
    from agentgate.db.models import TaskLog

    assert TaskLog.__tablename__ == "task_logs"


# ---------------------------------------------------------------------------
# Landing page features
# ---------------------------------------------------------------------------


def test_landing_page_has_dashboard_link():
    response = client.get("/")
    assert "/dashboard" in response.text


def test_landing_page_has_new_features():
    response = client.get("/")
    html = response.text
    assert "Per-agent auth" in html
    assert "Invocation logs" in html
    assert "Usage tracking" in html
    assert "Health monitoring" in html


# ---------------------------------------------------------------------------
# Redis fallback — rate limiter works without Redis
# ---------------------------------------------------------------------------


def test_rate_limiter_memory_fallback():
    from agentgate.server.ratelimit import RateLimiter

    limiter = RateLimiter(rate=10.0, burst=2)
    assert limiter.allow("test-ip")
    assert limiter.allow("test-ip")
    assert not limiter.allow("test-ip")  # Burst exhausted


# ---------------------------------------------------------------------------
# Log retention
# ---------------------------------------------------------------------------


def test_log_retention_config():
    from agentgate.core.config import Settings

    s = Settings(database_url="sqlite://", log_retention_days=7, log_max_per_agent=5000)
    assert s.log_retention_days == 7
    assert s.log_max_per_agent == 5000


@pytest.mark.asyncio
async def test_cleanup_old_logs_runs():
    """cleanup_old_logs should run without error even with mocked session."""
    from agentgate.server.log_retention import cleanup_old_logs

    mock_result = MagicMock()
    mock_result.rowcount = 0
    mock_result.all.return_value = []

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=mock_result)
    mock_session.commit = AsyncMock()

    mock_ctx = AsyncMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_ctx.__aexit__ = AsyncMock(return_value=False)
    mock_factory = MagicMock(return_value=mock_ctx)

    with patch("agentgate.server.log_retention.async_session", mock_factory):
        await cleanup_old_logs()

    mock_session.commit.assert_called_once()


# ---------------------------------------------------------------------------
# Billing breakdown endpoint — GET /agents/{id}/usage/breakdown
# ---------------------------------------------------------------------------


def test_billing_breakdown_no_auth():
    import uuid

    response = client.get(f"/agents/{uuid.uuid4()}/usage/breakdown")
    assert response.status_code == 401


def test_billing_breakdown_not_found():
    import uuid

    mock_factory = _mock_async_session_with_agents([])
    with (
        patch("agentgate.server.routes.async_session", mock_factory),
        patch("agentgate.server.routes.settings") as mock_settings,
    ):
        mock_settings.api_key = "test-key"
        response = client.get(
            f"/agents/{uuid.uuid4()}/usage/breakdown",
            headers={"Authorization": "Bearer test-key"},
        )
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Admin dashboard page
# ---------------------------------------------------------------------------


def test_admin_page():
    response = client.get("/admin")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "Admin" in response.text
    assert "AgentGate" in response.text


# ---------------------------------------------------------------------------
# Multi-tenancy — Organizations
# ---------------------------------------------------------------------------


def test_org_create_no_auth():
    response = client.post("/orgs/", json={"name": "test-org", "api_key": "secret-key-123"})
    assert response.status_code == 401


def test_org_list_no_auth():
    response = client.get("/orgs/")
    assert response.status_code == 401


def test_organization_model():
    from agentgate.db.models import Organization

    assert Organization.__tablename__ == "organizations"


def test_org_schema():
    from agentgate.server.schemas import OrgCreate

    org = OrgCreate(name="test-org", api_key="mysecretkey")
    assert org.name == "test-org"
    # api_key should be excluded from model_dump
    assert "api_key" not in org.model_dump()


def test_agent_create_with_org_id():
    import uuid

    from agentgate.server.schemas import AgentCreate

    org_id = uuid.uuid4()
    agent = AgentCreate(name="test", url="http://test.com", org_id=org_id)
    assert agent.org_id == org_id


def test_agent_response_has_org_id():
    import uuid

    agent = _make_fake_agent(org_id=uuid.uuid4())
    mock_factory = _mock_async_session_with_agents([agent])
    with patch("agentgate.server.routes.async_session", mock_factory):
        response = client.get(f"/agents/{agent.id}")
    assert response.status_code == 200
    assert "org_id" in response.json()


# ---------------------------------------------------------------------------
# Async SDK client
# ---------------------------------------------------------------------------


def test_async_client_import():
    from agentgate.sdk.async_client import AsyncAgentGateClient

    c = AsyncAgentGateClient("http://localhost:8000", api_key="test")
    assert c.server_url == "http://localhost:8000"
    assert c.api_key == "test"


def test_async_client_exported():
    from agentgate.sdk import AsyncAgentGateClient

    assert AsyncAgentGateClient is not None


# ---------------------------------------------------------------------------
# CLI billing command
# ---------------------------------------------------------------------------


def test_billing_cli_command_exists():
    from agentgate.cli.main import billing

    assert billing is not None


# ---------------------------------------------------------------------------
# Guide / documentation page
# ---------------------------------------------------------------------------


def test_guide_page():
    response = client.get("/guide")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "Documentation" in response.text
    assert "AgentGate" in response.text


def test_guide_page_has_sections():
    response = client.get("/guide")
    html = response.text
    assert "Quickstart" in html
    assert "API Reference" in html
    assert "SDK" in html
    assert "Org-Scoped Auth" in html
    assert "Rate Limiting" in html


# ---------------------------------------------------------------------------
# Org-scoped auth — resolve_org_or_admin
# ---------------------------------------------------------------------------


def test_org_scoped_get_no_auth():
    import uuid

    response = client.get(f"/orgs/{uuid.uuid4()}")
    assert response.status_code in (401, 403)


def test_org_scoped_agents_no_auth():
    import uuid

    response = client.get(f"/orgs/{uuid.uuid4()}/agents")
    assert response.status_code in (401, 403)


# ---------------------------------------------------------------------------
# Org update schema
# ---------------------------------------------------------------------------


def test_org_update_schema():
    from agentgate.server.schemas import OrgUpdate

    update = OrgUpdate(cost_per_invocation=0.005, rate_limit=50.0)
    dumped = update.model_dump(exclude_none=True)
    assert dumped["cost_per_invocation"] == 0.005
    assert dumped["rate_limit"] == 50.0
    assert "name" not in dumped


# ---------------------------------------------------------------------------
# Org create with billing fields
# ---------------------------------------------------------------------------


def test_org_create_with_billing():
    from agentgate.server.schemas import OrgCreate

    org = OrgCreate(
        name="test-org", api_key="mysecretkey",
        cost_per_invocation=0.002, rate_limit=50.0, rate_burst=100,
    )
    assert org.cost_per_invocation == 0.002
    assert org.rate_limit == 50.0
    assert org.rate_burst == 100
    assert "api_key" not in org.model_dump()


# ---------------------------------------------------------------------------
# Organization model — new fields
# ---------------------------------------------------------------------------


def test_organization_model_fields():
    from agentgate.db.models import Organization

    assert hasattr(Organization, "cost_per_invocation")
    assert hasattr(Organization, "billing_alert_threshold")
    assert hasattr(Organization, "rate_limit")
    assert hasattr(Organization, "rate_burst")


# ---------------------------------------------------------------------------
# OrgResponse schema — includes billing/rate fields
# ---------------------------------------------------------------------------


def test_org_response_schema():
    import uuid
    from datetime import datetime, timezone

    from agentgate.server.schemas import OrgResponse

    data = {
        "id": uuid.uuid4(),
        "name": "test-org",
        "cost_per_invocation": 0.001,
        "billing_alert_threshold": None,
        "rate_limit": 10.0,
        "rate_burst": 20,
        "created_at": datetime.now(timezone.utc),
    }
    resp = OrgResponse(**data)
    assert resp.cost_per_invocation == 0.001
    assert resp.rate_limit == 10.0


# ---------------------------------------------------------------------------
# Org billing endpoints — auth required
# ---------------------------------------------------------------------------


def test_org_billing_no_auth():
    import uuid

    response = client.get(f"/orgs/{uuid.uuid4()}/billing")
    assert response.status_code in (401, 403)


def test_org_billing_breakdown_no_auth():
    import uuid

    response = client.get(f"/orgs/{uuid.uuid4()}/billing/breakdown")
    assert response.status_code in (401, 403)


# ---------------------------------------------------------------------------
# Org update endpoint — no auth
# ---------------------------------------------------------------------------


def test_org_update_no_auth():
    import uuid

    response = client.put(
        f"/orgs/{uuid.uuid4()}", json={"rate_limit": 50.0},
    )
    assert response.status_code in (401, 403)


# ---------------------------------------------------------------------------
# Per-org rate limiter
# ---------------------------------------------------------------------------


def test_per_org_rate_limiter():
    from agentgate.server.ratelimit import RateLimiter

    limiter = RateLimiter(rate=5.0, burst=2)
    assert limiter.allow("org:test:ip1")
    assert limiter.allow("org:test:ip1")
    assert not limiter.allow("org:test:ip1")
    # Different org key should be allowed
    assert limiter.allow("org:other:ip1")


# ---------------------------------------------------------------------------
# SDK org CRUD methods exist
# ---------------------------------------------------------------------------


def test_sdk_sync_org_methods():
    from agentgate.sdk.client import AgentGateClient

    c = AgentGateClient("http://localhost:8000", api_key="test")
    assert hasattr(c, "create_org")
    assert hasattr(c, "list_orgs")
    assert hasattr(c, "get_org")
    assert hasattr(c, "update_org")
    assert hasattr(c, "delete_org")
    assert hasattr(c, "list_org_agents")
    assert hasattr(c, "get_org_billing")
    assert hasattr(c, "get_org_billing_breakdown")
    c.close()


def test_sdk_async_org_methods():
    from agentgate.sdk.async_client import AsyncAgentGateClient

    c = AsyncAgentGateClient("http://localhost:8000", api_key="test")
    assert hasattr(c, "create_org")
    assert hasattr(c, "list_orgs")
    assert hasattr(c, "get_org")
    assert hasattr(c, "update_org")
    assert hasattr(c, "delete_org")
    assert hasattr(c, "list_org_agents")
    assert hasattr(c, "get_org_billing")
    assert hasattr(c, "get_org_billing_breakdown")


# ---------------------------------------------------------------------------
# verify_api_key_or_org exists
# ---------------------------------------------------------------------------


def test_verify_api_key_or_org_exists():
    from agentgate.server.routes import verify_api_key_or_org

    assert verify_api_key_or_org is not None


# ---------------------------------------------------------------------------
# Org-scoped register — admin key still works
# ---------------------------------------------------------------------------


def test_register_agent_with_admin_key():
    """POST /agents/ with admin key should still work (backwards compat)."""
    import uuid
    from datetime import datetime, timezone

    agent_id = uuid.uuid4()
    now = datetime.now(timezone.utc)

    mock_session = AsyncMock()
    mock_session.add = MagicMock()
    mock_session.commit = AsyncMock()

    def mock_refresh(a):
        a.id = agent_id
        a.created_at = now
        a.updated_at = now
        a.org_id = None
        a.webhook_url = None

    mock_session.refresh = AsyncMock(side_effect=mock_refresh)

    mock_ctx = AsyncMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_ctx.__aexit__ = AsyncMock(return_value=False)
    mock_factory = MagicMock(return_value=mock_ctx)

    with (
        patch("agentgate.server.routes.async_session", mock_factory),
        patch("agentgate.server.routes.settings") as mock_settings,
    ):
        mock_settings.api_key = "admin-key"
        response = client.post(
            "/agents/",
            json={"name": "test-agent", "url": "http://test.com"},
            headers={"Authorization": "Bearer admin-key"},
        )
    assert response.status_code == 201


# ---------------------------------------------------------------------------
# API versioning — /v1/ prefix
# ---------------------------------------------------------------------------


def test_v1_health():
    response = client.get("/v1/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"


def test_v1_agents_list():
    mock_factory = _mock_async_session_with_agents([])
    with patch("agentgate.server.routes.async_session", mock_factory):
        response = client.get("/v1/agents/")
    assert response.status_code == 200


def test_v1_health_agents():
    response = client.get("/v1/health/agents")
    assert response.status_code == 200


def test_v1_orgs_requires_auth():
    response = client.get("/v1/orgs/")
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# Webhook retry — _fire_webhook with retries
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_webhook_retry_on_failure():
    """_fire_webhook should retry on failure with backoff."""
    from agentgate.server.routes import _fire_webhook

    mock_response = MagicMock()
    mock_response.status_code = 200

    call_count = 0

    async def mock_post(url, json=None):
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            raise ConnectionError("fail")
        return mock_response

    mock_client = AsyncMock()
    mock_client.post = mock_post
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("agentgate.server.routes.httpx.AsyncClient", return_value=mock_client):
        with patch("agentgate.server.routes.asyncio.sleep", new_callable=AsyncMock):
            await _fire_webhook("http://example.com/hook", {"event": "test"})

    assert call_count == 3


@pytest.mark.asyncio
async def test_webhook_retry_success_first_try():
    """_fire_webhook should not retry on first success."""
    from agentgate.server.routes import _fire_webhook

    mock_response = MagicMock()
    mock_response.status_code = 200

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=mock_response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("agentgate.server.routes.httpx.AsyncClient", return_value=mock_client):
        await _fire_webhook("http://example.com/hook", {"event": "test"})

    mock_client.post.assert_called_once()


@pytest.mark.asyncio
async def test_webhook_retry_exhausted():
    """_fire_webhook should exhaust retries on persistent failure."""
    from agentgate.server.routes import _fire_webhook

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(side_effect=ConnectionError("fail"))
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("agentgate.server.routes.httpx.AsyncClient", return_value=mock_client):
        with patch("agentgate.server.routes.asyncio.sleep", new_callable=AsyncMock):
            await _fire_webhook("http://example.com/hook", {"event": "test"}, max_retries=3)

    assert mock_client.post.call_count == 3


# ---------------------------------------------------------------------------
# Agent versioning — by-name endpoints
# ---------------------------------------------------------------------------


def test_agent_versions_endpoint_not_found():
    """by-name with non-existent name returns 404."""
    mock_factory = _mock_async_session_with_agents([])
    with patch("agentgate.server.routes.async_session", mock_factory):
        response = client.get("/agents/by-name/nonexistent")
    assert response.status_code == 404


def test_agent_latest_endpoint_not_found():
    """by-name/latest with non-existent name returns 404."""
    mock_factory = _mock_async_session_with_agents([])
    with patch("agentgate.server.routes.async_session", mock_factory):
        response = client.get("/agents/by-name/nonexistent/latest")
    assert response.status_code == 404


def test_agent_versions_endpoint_exists():
    """GET /agents/by-name/{name} endpoint exists."""
    from agentgate.server.routes import get_agent_versions

    assert get_agent_versions is not None


def test_agent_latest_endpoint_exists():
    """GET /agents/by-name/{name}/latest endpoint exists."""
    from agentgate.server.routes import get_agent_latest

    assert get_agent_latest is not None


# ---------------------------------------------------------------------------
# SDK versioning methods
# ---------------------------------------------------------------------------


def test_sdk_sync_versioning_methods():
    from agentgate.sdk.client import AgentGateClient

    c = AgentGateClient("http://localhost:8000")
    assert hasattr(c, "get_agent_versions")
    assert hasattr(c, "get_agent_latest")
    c.close()


def test_sdk_async_versioning_methods():
    from agentgate.sdk.async_client import AsyncAgentGateClient

    c = AsyncAgentGateClient("http://localhost:8000")
    assert hasattr(c, "get_agent_versions")
    assert hasattr(c, "get_agent_latest")


# ---------------------------------------------------------------------------
# E2E test file exists
# ---------------------------------------------------------------------------


def test_integration_test_file_exists():
    from pathlib import Path

    test_file = Path(__file__).parent / "test_integration.py"
    assert test_file.exists()


def test_docker_compose_test_exists():
    from pathlib import Path

    compose_file = Path(__file__).parent.parent / "docker-compose.test.yml"
    assert compose_file.exists()


# ---------------------------------------------------------------------------
# Marketplace page
# ---------------------------------------------------------------------------


def test_marketplace_page():
    response = client.get("/marketplace")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "Marketplace" in response.text
    assert "AgentGate" in response.text


# ---------------------------------------------------------------------------
# SSE streaming endpoint exists
# ---------------------------------------------------------------------------


def test_sse_stream_endpoint_exists():
    from agentgate.server.routes import route_task_stream

    assert callable(route_task_stream)


def test_sse_stream_agent_not_found():
    import uuid

    mock_factory = _mock_async_session_with_agents([])
    with patch("agentgate.server.routes.async_session", mock_factory):
        response = client.post(
            f"/agents/{uuid.uuid4()}/task/stream",
            json={"id": "t1", "message": {"parts": [{"type": "text", "text": "hi"}]}},
        )
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Plugin system
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_plugin_manager_pre_hook():
    from agentgate.server.plugins import PluginManager

    pm = PluginManager()
    calls = []

    @pm.pre_task
    async def hook(ctx):
        calls.append(ctx)
        return ctx

    result = await pm.run_pre_hooks({"agent_name": "test"})
    assert len(calls) == 1
    assert result["agent_name"] == "test"


@pytest.mark.asyncio
async def test_plugin_manager_post_hook():
    from agentgate.server.plugins import PluginManager

    pm = PluginManager()
    calls = []

    @pm.post_task
    async def hook(ctx):
        calls.append(ctx["status"])

    await pm.run_post_hooks({"status": "success"})
    assert calls == ["success"]


@pytest.mark.asyncio
async def test_plugin_manager_pre_hook_modifies_context():
    from agentgate.server.plugins import PluginManager

    pm = PluginManager()

    @pm.pre_task
    async def add_field(ctx):
        ctx["extra"] = "injected"
        return ctx

    result = await pm.run_pre_hooks({"task": {}})
    assert result["extra"] == "injected"


@pytest.mark.asyncio
async def test_plugin_manager_post_hook_error_doesnt_raise():
    from agentgate.server.plugins import PluginManager

    pm = PluginManager()

    @pm.post_task
    async def bad_hook(ctx):
        raise ValueError("boom")

    # Should not raise
    await pm.run_post_hooks({"status": "ok"})


@pytest.mark.asyncio
async def test_plugin_manager_pre_hook_error_raises():
    from agentgate.server.plugins import PluginManager

    pm = PluginManager()

    @pm.pre_task
    async def bad_hook(ctx):
        raise ValueError("rejected")

    with pytest.raises(ValueError, match="rejected"):
        await pm.run_pre_hooks({"task": {}})


def test_plugin_manager_clear():
    from agentgate.server.plugins import PluginManager

    pm = PluginManager()

    async def noop(ctx):
        pass

    pm.add_pre_hook(noop)
    pm.add_post_hook(noop)
    assert len(pm.pre_hooks) == 1
    assert len(pm.post_hooks) == 1

    pm.clear()
    assert len(pm.pre_hooks) == 0
    assert len(pm.post_hooks) == 0


def test_plugin_manager_remove_hook():
    from agentgate.server.plugins import PluginManager

    pm = PluginManager()

    async def hook(ctx):
        pass

    pm.add_pre_hook(hook)
    pm.add_post_hook(hook)
    pm.remove_pre_hook(hook)
    pm.remove_post_hook(hook)
    assert len(pm.pre_hooks) == 0
    assert len(pm.post_hooks) == 0


def test_global_plugin_manager_exists():
    from agentgate.server.plugins import plugin_manager

    assert hasattr(plugin_manager, "run_pre_hooks")
    assert hasattr(plugin_manager, "run_post_hooks")


# ---------------------------------------------------------------------------
# Agent tags
# ---------------------------------------------------------------------------


def test_list_agents_with_tag_filter():
    agent1 = _make_fake_agent(name="agent-a", tags=["nlp", "chat"])
    agent2 = _make_fake_agent(name="agent-b", tags=["vision"])
    mock_factory = _mock_async_session_with_agents([agent1, agent2])
    with patch("agentgate.server.routes.async_session", mock_factory):
        response = client.get("/agents/?tag=nlp")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["name"] == "agent-a"


def test_list_agents_no_tag_match():
    agent = _make_fake_agent(name="agent-a", tags=["nlp"])
    mock_factory = _mock_async_session_with_agents([agent])
    with patch("agentgate.server.routes.async_session", mock_factory):
        response = client.get("/agents/?tag=vision")
    assert response.status_code == 200
    assert response.json() == []


def test_tags_endpoint():
    agent1 = _make_fake_agent(name="a1", tags=["nlp", "chat"])
    agent2 = _make_fake_agent(name="a2", tags=["nlp", "vision"])
    mock_factory = _mock_async_session_with_agents([agent1, agent2])
    with patch("agentgate.server.routes.async_session", mock_factory):
        response = client.get("/agents/tags")
    assert response.status_code == 200
    data = response.json()
    assert "tags" in data
    names = [t["name"] for t in data["tags"]]
    assert "nlp" in names
    assert "chat" in names
    assert "vision" in names
    nlp_tag = next(t for t in data["tags"] if t["name"] == "nlp")
    assert nlp_tag["count"] == 2


def test_tags_endpoint_empty():
    mock_factory = _mock_async_session_with_agents([])
    with patch("agentgate.server.routes.async_session", mock_factory):
        response = client.get("/agents/tags")
    assert response.status_code == 200
    assert response.json()["tags"] == []


def test_agent_response_includes_tags():
    agent = _make_fake_agent(tags=["ml", "test"])
    mock_factory = _mock_async_session_with_agents([agent])
    with patch("agentgate.server.routes.async_session", mock_factory):
        response = client.get("/agents/")
    data = response.json()
    assert data[0]["tags"] == ["ml", "test"]


# ---------------------------------------------------------------------------
# API key rotation
# ---------------------------------------------------------------------------


def test_key_rotation_endpoint_exists():
    from agentgate.server.org_routes import confirm_key_rotation, rotate_org_key

    assert callable(rotate_org_key)
    assert callable(confirm_key_rotation)


def test_sdk_sync_has_rotation_methods():
    from agentgate.sdk.client import AgentGateClient

    c = AgentGateClient("http://localhost:8000")
    assert hasattr(c, "rotate_org_key")
    assert hasattr(c, "confirm_org_key_rotation")
    c.close()


def test_sdk_async_has_rotation_methods():
    from agentgate.sdk.async_client import AsyncAgentGateClient

    c = AsyncAgentGateClient("http://localhost:8000")
    assert hasattr(c, "rotate_org_key")
    assert hasattr(c, "confirm_org_key_rotation")


def test_sdk_sync_has_tag_methods():
    from agentgate.sdk.client import AgentGateClient

    c = AgentGateClient("http://localhost:8000")
    assert hasattr(c, "list_tags")
    c.close()


def test_sdk_async_has_tag_methods():
    from agentgate.sdk.async_client import AsyncAgentGateClient

    c = AsyncAgentGateClient("http://localhost:8000")
    assert hasattr(c, "list_tags")


# ---------------------------------------------------------------------------
# Migration file exists
# ---------------------------------------------------------------------------


def test_migration_tags_key_rotation_exists():
    from pathlib import Path

    migration = (
        Path(__file__).parent.parent
        / "src/agentgate/db/migrations/versions"
        / "f6g7h8i9j0k1_tags_and_key_rotation.py"
    )
    assert migration.exists()


# ---------------------------------------------------------------------------
# Marketplace HTML file exists
# ---------------------------------------------------------------------------


def test_marketplace_html_exists():
    from pathlib import Path

    html = (
        Path(__file__).parent.parent
        / "src/agentgate/server/static/marketplace.html"
    )
    assert html.exists()


# ---------------------------------------------------------------------------
# Plugin file exists
# ---------------------------------------------------------------------------


def test_plugins_file_exists():
    from pathlib import Path

    plugins = (
        Path(__file__).parent.parent
        / "src/agentgate/server/plugins.py"
    )
    assert plugins.exists()
