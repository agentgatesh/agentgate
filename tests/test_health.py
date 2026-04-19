import json
import uuid
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


def test_dashboard_redirects_to_account():
    response = client.get("/dashboard", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"] == "/account"


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
    mock_session.add = MagicMock()
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
    mock_session.add = MagicMock()
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
        "price_per_task": 0.0,
        "org_id": None,
        "deployed": False,
        "container_id": None,
        "container_port": None,
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
    mock_session.add = MagicMock()
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
    mock_session.add = MagicMock()
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
    from agentgate.server.auth import hash_api_key

    h = hash_api_key("test-key")
    assert len(h) == 64  # SHA-256 hex digest
    assert hash_api_key("test-key") == h  # Deterministic


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


def test_landing_page_has_account_link():
    response = client.get("/")
    assert "/account" in response.text


def test_landing_page_has_new_features():
    response = client.get("/")
    html = response.text
    assert "One-command deploy" in html
    assert "Built-in billing" in html
    assert "Health monitoring" in html
    assert "Organizations" in html


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
    mock_session.add = MagicMock()
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
    mock_session.add = MagicMock()
    mock_session.commit = AsyncMock()

    def mock_refresh(a):
        a.id = agent_id
        a.created_at = now
        a.updated_at = now
        a.org_id = None
        a.webhook_url = None
        a.deployed = False

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


# ---------------------------------------------------------------------------
# WebSocket endpoint exists
# ---------------------------------------------------------------------------


def test_ws_endpoint_exists():
    from agentgate.server.routes import route_task_ws

    assert callable(route_task_ws)


# ---------------------------------------------------------------------------
# Advanced search API — GET /agents/search
# ---------------------------------------------------------------------------


def test_search_agents_no_query():
    agent = _make_fake_agent(name="search-agent")
    mock_factory = _mock_async_session_with_agents([agent])
    with patch("agentgate.server.routes.async_session", mock_factory):
        response = client.get("/agents/search")
    assert response.status_code == 200
    data = response.json()
    assert "total" in data
    assert "agents" in data
    assert data["total"] >= 1


def test_search_agents_by_query():
    agent1 = _make_fake_agent(name="nlp-bot", description="Natural language processing")
    agent2 = _make_fake_agent(name="vision-bot", description="Image recognition")
    mock_factory = _mock_async_session_with_agents([agent1, agent2])
    with patch("agentgate.server.routes.async_session", mock_factory):
        response = client.get("/agents/search?q=nlp")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1
    assert data["agents"][0]["name"] == "nlp-bot"


def test_search_agents_multi_tag():
    agent1 = _make_fake_agent(name="a1", tags=["nlp", "chat"])
    agent2 = _make_fake_agent(name="a2", tags=["nlp", "vision"])
    agent3 = _make_fake_agent(name="a3", tags=["vision"])
    mock_factory = _mock_async_session_with_agents([agent1, agent2, agent3])
    with patch("agentgate.server.routes.async_session", mock_factory):
        response = client.get("/agents/search?tags=nlp,chat")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1
    assert data["agents"][0]["name"] == "a1"


def test_search_agents_sort_name():
    agent1 = _make_fake_agent(name="zebra")
    agent2 = _make_fake_agent(name="alpha")
    mock_factory = _mock_async_session_with_agents([agent1, agent2])
    with patch("agentgate.server.routes.async_session", mock_factory):
        response = client.get("/agents/search?sort=name")
    assert response.status_code == 200
    data = response.json()
    assert data["agents"][0]["name"] == "alpha"
    assert data["agents"][1]["name"] == "zebra"


def test_search_agents_pagination():
    agents = [_make_fake_agent(name=f"agent-{i}") for i in range(5)]
    mock_factory = _mock_async_session_with_agents(agents)
    with patch("agentgate.server.routes.async_session", mock_factory):
        response = client.get("/agents/search?limit=2&offset=1")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 5
    assert len(data["agents"]) == 2
    assert data["offset"] == 1


def test_search_agents_no_match():
    agent = _make_fake_agent(name="test")
    mock_factory = _mock_async_session_with_agents([agent])
    with patch("agentgate.server.routes.async_session", mock_factory):
        response = client.get("/agents/search?q=nonexistent")
    assert response.status_code == 200
    assert response.json()["total"] == 0


# ---------------------------------------------------------------------------
# Plugin registry — load_from_config
# ---------------------------------------------------------------------------


def test_plugin_load_from_config_missing_file():
    from agentgate.server.plugins import PluginManager

    pm = PluginManager()
    loaded = pm.load_from_config("/nonexistent/plugins.yaml")
    assert loaded == 0


def test_plugin_load_from_config_valid(tmp_path):
    import yaml

    from agentgate.server.plugins import PluginManager

    # Create a test plugin module
    plugin_dir = tmp_path / "test_plugins"
    plugin_dir.mkdir()
    (plugin_dir / "__init__.py").write_text("")
    (plugin_dir / "my_plugin.py").write_text(
        "async def my_hook(ctx):\n    return ctx\n"
    )

    # Create YAML config
    config = {
        "plugins": [
            {"module": "test_plugins.my_plugin", "hook": "pre_task", "function": "my_hook"},
        ]
    }
    config_file = tmp_path / "plugins.yaml"
    config_file.write_text(yaml.dump(config))

    import sys
    sys.path.insert(0, str(tmp_path))
    try:
        pm = PluginManager()
        loaded = pm.load_from_config(str(config_file))
        assert loaded == 1
        assert len(pm.pre_hooks) == 1
    finally:
        sys.path.remove(str(tmp_path))


def test_plugin_load_from_config_bad_module(tmp_path):
    import yaml

    from agentgate.server.plugins import PluginManager

    config = {
        "plugins": [
            {"module": "nonexistent.module", "hook": "pre_task", "function": "hook"},
        ]
    }
    config_file = tmp_path / "plugins.yaml"
    config_file.write_text(yaml.dump(config))

    pm = PluginManager()
    loaded = pm.load_from_config(str(config_file))
    assert loaded == 0


def test_plugin_info():
    from agentgate.server.plugins import PluginManager

    pm = PluginManager()

    async def hook(ctx):
        pass

    pm.add_pre_hook(hook)
    pm.add_post_hook(hook)
    info = pm.plugin_info
    assert len(info) == 2
    assert info[0]["type"] == "pre_task"
    assert info[1]["type"] == "post_task"


# ---------------------------------------------------------------------------
# Rate limit dashboard
# ---------------------------------------------------------------------------


def test_ratelimits_page():
    response = client.get("/ratelimits")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "Rate Limits" in response.text
    assert "AgentGate" in response.text


def test_ratelimits_data_no_auth():
    with patch("agentgate.server.app.settings") as mock_settings:
        mock_settings.api_key = "test-key"
        response = client.get("/ratelimits/data")
    assert response.status_code == 401


def test_ratelimits_data_with_auth():
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = []

    mock_session = AsyncMock()
    mock_session.add = MagicMock()
    mock_session.execute = AsyncMock(return_value=mock_result)

    mock_ctx = AsyncMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_ctx.__aexit__ = AsyncMock(return_value=False)
    mock_factory = MagicMock(return_value=mock_ctx)

    with (
        patch("agentgate.server.app.settings") as mock_settings,
        patch("agentgate.server.app.async_session", mock_factory),
    ):
        mock_settings.api_key = "test-key"
        response = client.get(
            "/ratelimits/data",
            headers={"Authorization": "Bearer test-key"},
        )
    assert response.status_code == 200
    data = response.json()
    assert "global" in data
    assert "organizations" in data
    assert "rate" in data["global"]
    assert "burst" in data["global"]


# ---------------------------------------------------------------------------
# Plugins info endpoint
# ---------------------------------------------------------------------------


def test_plugins_info_no_auth():
    with patch("agentgate.server.app.settings") as mock_settings:
        mock_settings.api_key = "test-key"
        response = client.get("/plugins/info")
    assert response.status_code == 401


def test_plugins_info_with_auth():
    with patch("agentgate.server.app.settings") as mock_settings:
        mock_settings.api_key = "test-key"
        response = client.get(
            "/plugins/info",
            headers={"Authorization": "Bearer test-key"},
        )
    assert response.status_code == 200
    data = response.json()
    assert "plugins" in data
    assert "total" in data


# ---------------------------------------------------------------------------
# SDK — search_agents method
# ---------------------------------------------------------------------------


def test_sdk_sync_has_search():
    from agentgate.sdk.client import AgentGateClient

    c = AgentGateClient("http://localhost:8000")
    assert hasattr(c, "search_agents")
    c.close()


def test_sdk_async_has_search():
    from agentgate.sdk.async_client import AsyncAgentGateClient

    c = AsyncAgentGateClient("http://localhost:8000")
    assert hasattr(c, "search_agents")


# ---------------------------------------------------------------------------
# Config — plugin_config setting
# ---------------------------------------------------------------------------


def test_plugin_config_setting():
    from agentgate.core.config import Settings

    s = Settings(database_url="sqlite://", plugin_config="/path/to/plugins.yaml")
    assert s.plugin_config == "/path/to/plugins.yaml"


# ---------------------------------------------------------------------------
# Rate limit dashboard HTML file exists
# ---------------------------------------------------------------------------


def test_ratelimits_html_exists():
    from pathlib import Path

    html = (
        Path(__file__).parent.parent
        / "src/agentgate/server/static/ratelimits.html"
    )
    assert html.exists()


# ---------------------------------------------------------------------------
# Reviews — Model & Schema
# ---------------------------------------------------------------------------


def test_review_model_fields():
    from agentgate.db.models import Review

    assert hasattr(Review, "id")
    assert hasattr(Review, "agent_id")
    assert hasattr(Review, "rating")
    assert hasattr(Review, "comment")
    assert hasattr(Review, "reviewer")
    assert hasattr(Review, "created_at")


def test_review_create_schema_valid():
    from agentgate.server.schemas import ReviewCreate

    r = ReviewCreate(rating=5, comment="Great!", reviewer="alice")
    assert r.rating == 5
    assert r.comment == "Great!"
    assert r.reviewer == "alice"


def test_review_create_schema_defaults():
    from agentgate.server.schemas import ReviewCreate

    r = ReviewCreate(rating=3)
    assert r.comment == ""
    assert r.reviewer == "anonymous"


def test_review_create_schema_rating_bounds():
    from pydantic import ValidationError

    from agentgate.server.schemas import ReviewCreate

    with pytest.raises(ValidationError):
        ReviewCreate(rating=0)
    with pytest.raises(ValidationError):
        ReviewCreate(rating=6)


def test_review_response_schema():
    import uuid
    from datetime import datetime

    from agentgate.server.schemas import ReviewResponse

    r = ReviewResponse(
        id=uuid.uuid4(),
        agent_id=uuid.uuid4(),
        rating=4,
        comment="nice",
        reviewer="bob",
        created_at=datetime.now(),
    )
    assert r.rating == 4
    assert r.reviewer == "bob"


# ---------------------------------------------------------------------------
# Reviews — API endpoints (mocked DB)
# ---------------------------------------------------------------------------


@patch("agentgate.server.routes.async_session")
def test_create_review_agent_not_found(mock_session):
    mock_sess = AsyncMock()
    mock_sess.get = AsyncMock(return_value=None)
    mock_session.return_value.__aenter__ = AsyncMock(return_value=mock_sess)
    mock_session.return_value.__aexit__ = AsyncMock(return_value=False)

    response = client.post(
        "/agents/00000000-0000-0000-0000-000000000001/reviews",
        json={"rating": 5, "comment": "test"},
    )
    assert response.status_code == 404


@patch("agentgate.server.routes.async_session")
def test_create_review_success(mock_session):
    import uuid
    from datetime import datetime, timezone

    mock_agent = MagicMock()
    mock_agent.id = uuid.UUID("00000000-0000-0000-0000-000000000001")

    review_id = uuid.uuid4()
    mock_review = MagicMock()
    mock_review.id = review_id
    mock_review.agent_id = mock_agent.id
    mock_review.rating = 5
    mock_review.comment = "Excellent"
    mock_review.reviewer = "tester"
    mock_review.created_at = datetime.now(timezone.utc)

    mock_sess = AsyncMock()
    mock_sess.get = AsyncMock(return_value=mock_agent)
    mock_sess.add = MagicMock()
    mock_sess.commit = AsyncMock()

    async def fake_refresh(obj):
        obj.id = mock_review.id
        obj.agent_id = mock_review.agent_id
        obj.rating = mock_review.rating
        obj.comment = mock_review.comment
        obj.reviewer = mock_review.reviewer
        obj.created_at = mock_review.created_at

    mock_sess.refresh = fake_refresh
    mock_session.return_value.__aenter__ = AsyncMock(return_value=mock_sess)
    mock_session.return_value.__aexit__ = AsyncMock(return_value=False)

    response = client.post(
        "/agents/00000000-0000-0000-0000-000000000001/reviews",
        json={"rating": 5, "comment": "Excellent", "reviewer": "tester"},
    )
    assert response.status_code == 201
    data = response.json()
    assert data["rating"] == 5
    assert data["comment"] == "Excellent"
    assert data["reviewer"] == "tester"


@patch("agentgate.server.routes.async_session")
def test_list_reviews_agent_not_found(mock_session):
    mock_sess = AsyncMock()
    mock_sess.get = AsyncMock(return_value=None)
    mock_session.return_value.__aenter__ = AsyncMock(return_value=mock_sess)
    mock_session.return_value.__aexit__ = AsyncMock(return_value=False)

    response = client.get("/agents/00000000-0000-0000-0000-000000000001/reviews")
    assert response.status_code == 404


@patch("agentgate.server.routes.async_session")
def test_review_stats_agent_not_found(mock_session):
    mock_sess = AsyncMock()
    mock_sess.get = AsyncMock(return_value=None)
    mock_session.return_value.__aenter__ = AsyncMock(return_value=mock_sess)
    mock_session.return_value.__aexit__ = AsyncMock(return_value=False)

    response = client.get(
        "/agents/00000000-0000-0000-0000-000000000001/reviews/stats",
    )
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Reviews — search includes avg_rating/review_count
# ---------------------------------------------------------------------------


@patch("agentgate.server.routes.async_session")
def test_search_sort_by_rating_accepted(mock_session):
    """Verify that sort=rating is accepted (doesn't return 422)."""
    mock_agent = MagicMock()
    mock_agent.id = "00000000-0000-0000-0000-000000000001"
    mock_agent.name = "test"
    mock_agent.description = "desc"
    mock_agent.url = "http://test"
    mock_agent.version = "1.0"
    mock_agent.skills = []
    mock_agent.tags = []
    mock_agent.org_id = None
    mock_agent.created_at = MagicMock(isoformat=MagicMock(return_value="2026-01-01"))
    mock_agent.updated_at = MagicMock(isoformat=MagicMock(return_value="2026-01-01"))

    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [mock_agent]

    # Need two execute calls: one for agents, one for review stats
    mock_review_result = MagicMock()
    mock_review_result.all.return_value = []

    mock_sess = AsyncMock()
    mock_sess.execute = AsyncMock(side_effect=[mock_result, mock_review_result])
    mock_session.return_value.__aenter__ = AsyncMock(return_value=mock_sess)
    mock_session.return_value.__aexit__ = AsyncMock(return_value=False)

    response = client.get("/agents/search?sort=rating")
    assert response.status_code == 200
    data = response.json()
    assert "agents" in data


# ---------------------------------------------------------------------------
# Reviews — migration file exists
# ---------------------------------------------------------------------------


def test_reviews_chains_migration_exists():
    from pathlib import Path

    migration = (
        Path(__file__).parent.parent
        / "src/agentgate/db/migrations/versions/g7h8i9j0k1l2_reviews_and_chains.py"
    )
    assert migration.exists()


# ---------------------------------------------------------------------------
# Chains — Model & Schema
# ---------------------------------------------------------------------------


def test_chain_model_fields():
    from agentgate.db.models import Chain

    assert hasattr(Chain, "id")
    assert hasattr(Chain, "name")
    assert hasattr(Chain, "description")
    assert hasattr(Chain, "steps")
    assert hasattr(Chain, "org_id")
    assert hasattr(Chain, "created_at")
    assert hasattr(Chain, "updated_at")


def test_chain_create_schema_valid():
    from agentgate.server.schemas import ChainCreate, ChainStep

    c = ChainCreate(
        name="my-chain",
        steps=[ChainStep(agent_id="abc-123")],
    )
    assert c.name == "my-chain"
    assert len(c.steps) == 1
    assert c.steps[0].agent_id == "abc-123"


def test_chain_create_schema_requires_steps():
    from pydantic import ValidationError

    from agentgate.server.schemas import ChainCreate

    with pytest.raises(ValidationError):
        ChainCreate(name="empty", steps=[])


def test_chain_step_input_template():
    from agentgate.server.schemas import ChainStep

    step = ChainStep(agent_id="x", input_template="Translate: {previous}")
    assert step.input_template == "Translate: {previous}"


def test_chain_update_schema():
    from agentgate.server.schemas import ChainUpdate

    u = ChainUpdate(name="renamed")
    assert u.name == "renamed"
    assert u.steps is None


def test_chain_response_schema():
    import uuid
    from datetime import datetime

    from agentgate.server.schemas import ChainResponse

    r = ChainResponse(
        id=uuid.uuid4(),
        name="test-chain",
        description="desc",
        steps=[{"agent_id": "a"}],
        created_at=datetime.now(),
        updated_at=datetime.now(),
    )
    assert r.name == "test-chain"


# ---------------------------------------------------------------------------
# Chains — API endpoints (mocked DB)
# ---------------------------------------------------------------------------


@patch("agentgate.server.routes.async_session")
@patch("agentgate.server.chain_routes.async_session")
def test_create_chain_requires_auth(mock_chain_session, mock_routes_session):
    response = client.post(
        "/chains/",
        json={
            "name": "test-chain",
            "steps": [{"agent_id": "00000000-0000-0000-0000-000000000001"}],
        },
    )
    # No auth header => 401 or 403
    assert response.status_code in (401, 403)


@patch("agentgate.server.routes.async_session")
@patch("agentgate.server.chain_routes.async_session")
def test_get_chain_not_found(mock_chain_session, mock_routes_session):
    mock_sess = AsyncMock()
    mock_sess.get = AsyncMock(return_value=None)
    mock_chain_session.return_value.__aenter__ = AsyncMock(return_value=mock_sess)
    mock_chain_session.return_value.__aexit__ = AsyncMock(return_value=False)

    # Mock routes session for verify_api_key_or_org
    mock_routes_session.return_value.__aenter__ = AsyncMock(return_value=mock_sess)
    mock_routes_session.return_value.__aexit__ = AsyncMock(return_value=False)

    response = client.get(
        "/chains/00000000-0000-0000-0000-000000000001",
        headers={"Authorization": "Bearer test-api-key"},
    )
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Chains — _extract_text helper
# ---------------------------------------------------------------------------


def test_extract_text_top_level_artifacts():
    from agentgate.server.chain_routes import _extract_text

    resp = {
        "id": "task-1",
        "status": {"state": "completed"},
        "artifacts": [
            {"parts": [{"type": "text", "text": "8"}]}
        ],
    }
    assert _extract_text(resp) == "8"


def test_extract_text_a2a_result_artifacts():
    from agentgate.server.chain_routes import _extract_text

    resp = {
        "result": {
            "artifacts": [
                {"parts": [{"type": "text", "text": "Hello from artifact"}]}
            ]
        }
    }
    assert _extract_text(resp) == "Hello from artifact"


def test_extract_text_a2a_result_message():
    from agentgate.server.chain_routes import _extract_text

    resp = {
        "result": {
            "message": {
                "parts": [{"type": "text", "text": "Hello from message"}]
            }
        }
    }
    assert _extract_text(resp) == "Hello from message"


def test_extract_text_direct_message():
    from agentgate.server.chain_routes import _extract_text

    resp = {
        "message": {
            "parts": [{"type": "text", "text": "Direct message"}]
        }
    }
    assert _extract_text(resp) == "Direct message"


def test_extract_text_result_string():
    from agentgate.server.chain_routes import _extract_text

    resp = {"result": "plain string result"}
    assert _extract_text(resp) == "plain string result"


def test_extract_text_text_field():
    from agentgate.server.chain_routes import _extract_text

    resp = {"text": "simple text"}
    assert _extract_text(resp) == "simple text"


def test_extract_text_fallback_json():
    from agentgate.server.chain_routes import _extract_text

    resp = {"foo": "bar"}
    result = _extract_text(resp)
    assert "foo" in result
    assert "bar" in result


# ---------------------------------------------------------------------------
# Chains — router mounted at /v1
# ---------------------------------------------------------------------------


def test_chains_v1_routing():
    """Verify /v1/chains/ is mounted."""
    response = client.post(
        "/v1/chains/",
        json={
            "name": "test",
            "steps": [{"agent_id": "00000000-0000-0000-0000-000000000001"}],
        },
    )
    # Should fail with 401/403 (no auth), not 404 (route not found)
    assert response.status_code in (401, 403)


# ---------------------------------------------------------------------------
# SDK — review methods exist
# ---------------------------------------------------------------------------


def test_sdk_review_methods_exist():
    from agentgate.sdk.client import AgentGateClient

    c = AgentGateClient("http://localhost:8000")
    assert hasattr(c, "create_review")
    assert hasattr(c, "list_reviews")
    assert hasattr(c, "get_review_stats")
    c.close()


def test_async_sdk_review_methods_exist():
    from agentgate.sdk.async_client import AsyncAgentGateClient

    c = AsyncAgentGateClient("http://localhost:8000")
    assert hasattr(c, "create_review")
    assert hasattr(c, "list_reviews")
    assert hasattr(c, "get_review_stats")


# ---------------------------------------------------------------------------
# SDK — chain methods exist
# ---------------------------------------------------------------------------


def test_sdk_chain_methods_exist():
    from agentgate.sdk.client import AgentGateClient

    c = AgentGateClient("http://localhost:8000")
    assert hasattr(c, "create_chain")
    assert hasattr(c, "list_chains")
    assert hasattr(c, "get_chain")
    assert hasattr(c, "update_chain")
    assert hasattr(c, "delete_chain")
    assert hasattr(c, "run_chain")
    c.close()


def test_async_sdk_chain_methods_exist():
    from agentgate.sdk.async_client import AsyncAgentGateClient

    c = AsyncAgentGateClient("http://localhost:8000")
    assert hasattr(c, "create_chain")
    assert hasattr(c, "list_chains")
    assert hasattr(c, "get_chain")
    assert hasattr(c, "update_chain")
    assert hasattr(c, "delete_chain")
    assert hasattr(c, "run_chain")


# ---------------------------------------------------------------------------
# FASE 3: Monetization — Models
# ---------------------------------------------------------------------------


def test_agent_model_has_price_per_task():
    from agentgate.db.models import Agent
    agent = Agent(name="test", url="http://test.com", price_per_task=0.01)
    assert agent.price_per_task == 0.01


def test_agent_model_price_field_exists():
    from agentgate.db.models import Agent
    assert hasattr(Agent, "price_per_task")


def test_org_model_has_balance():
    from agentgate.db.models import Organization
    org = Organization(name="test", api_key_hash="abc", balance=100.0)
    assert org.balance == 100.0


def test_org_model_balance_field_exists():
    from agentgate.db.models import Organization
    assert hasattr(Organization, "balance")


def test_org_model_has_tier():
    from agentgate.db.models import Organization
    org = Organization(name="test", api_key_hash="abc", tier="pro")
    assert org.tier == "pro"


def test_org_model_tier_field_exists():
    from agentgate.db.models import Organization
    assert hasattr(Organization, "tier")


def test_transaction_model():
    import uuid

    from agentgate.db.models import Transaction
    tx = Transaction(
        payer_org_id=uuid.uuid4(),
        agent_id=uuid.uuid4(),
        agent_name="test-agent",
        amount=0.01,
        fee=0.0003,
        net=0.0097,
        tx_type="task",
    )
    assert tx.amount == 0.01
    assert tx.fee == 0.0003
    assert tx.net == 0.0097
    assert tx.tx_type == "task"


# ---------------------------------------------------------------------------
# FASE 3: Monetization — Schemas
# ---------------------------------------------------------------------------


def test_agent_create_schema_has_price():
    from agentgate.server.schemas import AgentCreate
    a = AgentCreate(name="test", url="http://test.com", price_per_task=0.05)
    assert a.price_per_task == 0.05


def test_agent_create_schema_default_price():
    from agentgate.server.schemas import AgentCreate
    a = AgentCreate(name="test", url="http://test.com")
    assert a.price_per_task == 0.0


def test_agent_response_schema_has_price():
    import uuid
    from datetime import datetime, timezone

    from agentgate.server.schemas import AgentResponse
    r = AgentResponse(
        id=uuid.uuid4(), name="t", description="", url="http://t.com",
        version="1.0.0", skills=[], price_per_task=0.05,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    assert r.price_per_task == 0.05


def test_org_create_schema_has_tier():
    from agentgate.server.schemas import OrgCreate
    o = OrgCreate(name="test", api_key="test-key-123", tier="pro")
    assert o.tier == "pro"


def test_org_create_schema_default_tier():
    from agentgate.server.schemas import OrgCreate
    o = OrgCreate(name="test", api_key="test-key-123")
    assert o.tier == "free"


def test_org_create_schema_invalid_tier():
    from pydantic import ValidationError

    from agentgate.server.schemas import OrgCreate
    with pytest.raises(ValidationError):
        OrgCreate(name="test", api_key="test-key-123", tier="invalid")


def test_org_response_schema_has_balance_tier():
    import uuid
    from datetime import datetime, timezone

    from agentgate.server.schemas import OrgResponse
    r = OrgResponse(
        id=uuid.uuid4(), name="t", cost_per_invocation=0.001,
        rate_limit=10.0, rate_burst=20, balance=50.0, tier="pro",
        created_at=datetime.now(timezone.utc),
    )
    assert r.balance == 50.0
    assert r.tier == "pro"


# ---------------------------------------------------------------------------
# FASE 3: Monetization — Migration
# ---------------------------------------------------------------------------


def test_monetization_migration_exists():
    import os
    path = os.path.join(
        os.path.dirname(__file__), "..",
        "src/agentgate/db/migrations/versions/h8i9j0k1l2m3_monetization.py",
    )
    assert os.path.exists(path)


# ---------------------------------------------------------------------------
# FASE 3: Monetization — Billing engine
# ---------------------------------------------------------------------------


def test_tier_fee_percentages():
    from agentgate.server.routes import TIER_FEE_PCT
    assert TIER_FEE_PCT["free"] == 0.03
    assert TIER_FEE_PCT["pro"] == 0.025
    assert TIER_FEE_PCT["enterprise"] == 0.02


def test_tier_limits_defined():
    from agentgate.server.org_routes import TIER_LIMITS
    assert "free" in TIER_LIMITS
    assert "pro" in TIER_LIMITS
    assert "enterprise" in TIER_LIMITS
    assert TIER_LIMITS["free"]["max_agents"] == 5
    assert TIER_LIMITS["pro"]["max_agents"] == 50
    assert TIER_LIMITS["enterprise"]["max_agents"] == 500


# ---------------------------------------------------------------------------
# FASE 3: Monetization — Wallet endpoint (mocked)
# ---------------------------------------------------------------------------


def test_wallet_endpoint_requires_auth():
    import uuid
    response = client.get(f"/orgs/{uuid.uuid4()}/wallet")
    assert response.status_code in (401, 403)


def test_topup_endpoint_requires_auth():
    import uuid
    response = client.post(f"/orgs/{uuid.uuid4()}/topup", json={"amount": 10.0})
    assert response.status_code in (401, 403)


def test_transactions_endpoint_requires_auth():
    import uuid
    response = client.get(f"/orgs/{uuid.uuid4()}/transactions")
    assert response.status_code in (401, 403)


def test_transaction_summary_endpoint_requires_auth():
    import uuid
    response = client.get(f"/orgs/{uuid.uuid4()}/transactions/summary")
    assert response.status_code in (401, 403)


# ---------------------------------------------------------------------------
# FASE 3: Monetization — Billing logic unit tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_process_billing_free_agent():
    """Free agent (price=0) should not charge anything."""
    from agentgate.server.routes import _process_billing
    agent = MagicMock()
    agent.price_per_task = 0
    charged, err = await _process_billing(agent, None, "task-1")
    assert charged is True
    assert err is None


@pytest.mark.asyncio
async def test_process_billing_admin_key():
    """Admin key (no org) should bypass billing."""
    from agentgate.server.routes import _process_billing
    agent = MagicMock()
    agent.price_per_task = 1.0
    charged, err = await _process_billing(agent, None, "task-1")
    assert charged is True
    assert err is None


@pytest.mark.asyncio
async def test_process_billing_insufficient_funds():
    """Atomic debit UPDATE returns rowcount=0 when balance < price."""
    from agentgate.server.routes import _process_billing

    agent = MagicMock()
    agent.id = "agent-id"
    agent.name = "pricey"
    agent.price_per_task = 10.0
    agent.org_id = None

    org = MagicMock()
    org.id = "org-id"
    org.name = "poor-org"
    org.balance = 5.0
    org.tier = "free"

    factory, session = _mock_billing_session(rowcount=0, fallback_balance=5.0)

    with patch("agentgate.server.billing.async_session", factory):
        charged, err = await _process_billing(agent, org, "task-1")

    assert charged is False
    assert "Insufficient balance" in err
    # No further updates, no Transaction row, no commit after a failed debit
    session.add.assert_not_called()
    session.commit.assert_not_called()


# ---------------------------------------------------------------------------
# FASE 3: Monetization — V1 routing
# ---------------------------------------------------------------------------


def test_v1_wallet_endpoint_exists():
    import uuid
    response = client.get(f"/v1/orgs/{uuid.uuid4()}/wallet")
    assert response.status_code in (401, 403)


def test_v1_transactions_endpoint_exists():
    import uuid
    response = client.get(f"/v1/orgs/{uuid.uuid4()}/transactions")
    assert response.status_code in (401, 403)


# ---------------------------------------------------------------------------
# FASE 3: SDK — monetization methods exist
# ---------------------------------------------------------------------------


def test_sdk_wallet_methods_exist():
    from agentgate.sdk.client import AgentGateClient
    c = AgentGateClient("http://localhost:8000")
    assert hasattr(c, "get_org_wallet")
    assert hasattr(c, "topup_org")
    assert hasattr(c, "list_org_transactions")
    assert hasattr(c, "get_org_transaction_summary")
    c.close()


def test_async_sdk_wallet_methods_exist():
    from agentgate.sdk.async_client import AsyncAgentGateClient
    c = AsyncAgentGateClient("http://localhost:8000")
    assert hasattr(c, "get_org_wallet")
    assert hasattr(c, "topup_org")
    assert hasattr(c, "list_org_transactions")
    assert hasattr(c, "get_org_transaction_summary")


# ---------------------------------------------------------------------------
# FASE 3: Tier upgrade/downgrade endpoint
# ---------------------------------------------------------------------------


def test_tier_change_endpoint_requires_auth():
    import uuid
    response = client.post(f"/orgs/{uuid.uuid4()}/tier", json={"tier": "pro"})
    assert response.status_code in (401, 403)


def test_v1_tier_change_endpoint_exists():
    import uuid
    response = client.post(f"/v1/orgs/{uuid.uuid4()}/tier", json={"tier": "pro"})
    assert response.status_code in (401, 403)


def test_tier_change_endpoint_exists():
    from agentgate.server.org_routes import change_org_tier
    assert callable(change_org_tier)


def test_tier_change_invalid_tier():
    """Invalid tier should return 400."""
    import uuid

    from agentgate.server.org_routes import resolve_org_or_admin

    org_id = uuid.uuid4()

    # Override the dependency to simulate admin (returns None)
    app.dependency_overrides[resolve_org_or_admin] = lambda: None

    try:
        response = client.post(
            f"/orgs/{org_id}/tier",
            json={"tier": "platinum"},
            headers={"Authorization": "Bearer admin-key"},
        )
        assert response.status_code == 400
        assert "Invalid tier" in response.json()["detail"]
    finally:
        app.dependency_overrides.pop(resolve_org_or_admin, None)


# ---------------------------------------------------------------------------
# FASE 3: Billing E2E — _process_billing full flow
# ---------------------------------------------------------------------------


def _mock_billing_session(rowcount: int = 1, fallback_balance: float = 0.0):
    """Build a mock session for billing.process_charge.

    process_charge calls session.execute(update(...)) several times; each
    execute result needs a .rowcount. If the first debit fails, it then
    calls session.get() to read a fresh balance for the error message.
    """
    exec_result = MagicMock()
    exec_result.rowcount = rowcount

    fallback_org = MagicMock()
    fallback_org.balance = fallback_balance

    mock_session = AsyncMock()
    mock_session.add = MagicMock()
    mock_session.commit = AsyncMock()
    mock_session.execute = AsyncMock(return_value=exec_result)
    mock_session.get = AsyncMock(return_value=fallback_org)

    mock_ctx = AsyncMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_ctx.__aexit__ = AsyncMock(return_value=False)
    return MagicMock(return_value=mock_ctx), mock_session


@pytest.mark.asyncio
async def test_process_billing_success_charges_payer():
    """Full billing flow: atomic debit succeeds, transaction recorded."""
    from agentgate.server.routes import _process_billing

    agent = MagicMock()
    agent.id = "agent-id"
    agent.name = "paid-agent"
    agent.price_per_task = 1.0
    agent.org_id = None

    payer_org = MagicMock()
    payer_org.id = "payer-id"
    payer_org.name = "payer-org"
    payer_org.balance = 10.0
    payer_org.tier = "free"

    factory, session = _mock_billing_session(rowcount=1)

    with patch("agentgate.server.billing.async_session", factory):
        charged, err = await _process_billing(agent, payer_org, "task-123")

    assert charged is True
    assert err is None
    # Debit + platform fee credit (no receiver since agent.org_id is None)
    assert session.execute.await_count == 2
    session.add.assert_called_once()  # Transaction row inserted
    session.commit.assert_called_once()


@pytest.mark.asyncio
async def test_process_billing_with_receiver():
    """Billing with an agent owner: receiver is credited via UPDATE."""
    import uuid

    from agentgate.server.routes import _process_billing

    payer_id = uuid.uuid4()
    receiver_id = uuid.uuid4()

    agent = MagicMock()
    agent.id = uuid.uuid4()
    agent.name = "premium-agent"
    agent.price_per_task = 2.0
    agent.org_id = receiver_id

    payer_org = MagicMock()
    payer_org.id = payer_id
    payer_org.name = "payer"
    payer_org.balance = 50.0
    payer_org.tier = "pro"

    factory, session = _mock_billing_session(rowcount=1)

    with patch("agentgate.server.billing.async_session", factory):
        charged, err = await _process_billing(agent, payer_org, "task-456")

    assert charged is True
    assert err is None
    # 3 updates: debit payer, credit receiver, credit platform fee
    assert session.execute.await_count == 3
    # Transaction row inserted with correct pro-tier fee
    tx = session.add.call_args[0][0]
    fee = round(2.0 * 0.025, 6)
    assert tx.amount == 2.0
    assert tx.fee == fee
    assert tx.net == round(2.0 - fee, 6)
    assert tx.receiver_org_id == receiver_id


@pytest.mark.asyncio
async def test_process_billing_enterprise_fee():
    """Enterprise tier should have 2% fee on the Transaction row."""
    from agentgate.server.routes import _process_billing

    agent = MagicMock()
    agent.id = "agent-id"
    agent.name = "ent-agent"
    agent.price_per_task = 10.0
    agent.org_id = None

    payer_org = MagicMock()
    payer_org.id = "payer-id"
    payer_org.name = "ent-payer"
    payer_org.balance = 100.0
    payer_org.tier = "enterprise"

    factory, session = _mock_billing_session(rowcount=1)

    with patch("agentgate.server.billing.async_session", factory):
        charged, err = await _process_billing(agent, payer_org, "task-789")

    assert charged is True
    tx = session.add.call_args[0][0]
    assert tx.amount == 10.0
    assert tx.fee == round(10.0 * 0.02, 6)
    assert tx.net == round(10.0 - 10.0 * 0.02, 6)


def test_route_task_402_insufficient_balance():
    """Paid agent with org lacking balance should return 402."""
    import uuid

    agent_id = uuid.uuid4()
    org_id = uuid.uuid4()

    agent = _make_fake_agent(
        id=agent_id, name="expensive-agent", price_per_task=100.0,
        api_key_hash=None, org_id=None,
    )

    mock_org = MagicMock()
    mock_org.id = org_id
    mock_org.balance = 1.0
    mock_org.tier = "free"

    mock_result_agent = MagicMock()
    mock_result_agent.scalar_one_or_none.return_value = None

    # Build a session that returns the agent for .get() and the org for org lookup
    mock_session = AsyncMock()
    mock_session.add = MagicMock()

    async def mock_get(model, obj_id):
        if obj_id == agent_id:
            return agent
        return None

    mock_session.get = AsyncMock(side_effect=mock_get)

    # For org lookup by key hash
    mock_org_result = MagicMock()
    mock_org_result.scalar_one_or_none.return_value = mock_org
    mock_session.execute = AsyncMock(return_value=mock_org_result)

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
            f"/agents/{agent_id}/task",
            json={"id": "t1", "message": {"parts": [{"type": "text", "text": "hi"}]}},
            headers={"Authorization": "Bearer org-key-123"},
        )
    assert response.status_code == 402
    assert "Insufficient balance" in response.json()["detail"]


# ---------------------------------------------------------------------------
# FASE 3: SDK — tier change methods exist
# ---------------------------------------------------------------------------


def test_sdk_sync_tier_change_method():
    from agentgate.sdk.client import AgentGateClient
    c = AgentGateClient("http://localhost:8000")
    assert hasattr(c, "change_org_tier")
    c.close()


def test_async_sdk_tier_change_method():
    from agentgate.sdk.async_client import AsyncAgentGateClient
    c = AsyncAgentGateClient("http://localhost:8000")
    assert hasattr(c, "change_org_tier")


# ---------------------------------------------------------------------------
# Sessione #19: Billing post-task (charge after success, not before)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_process_billing_called_after_success():
    """Billing should only charge after successful A2A response."""
    import uuid

    agent_id = uuid.uuid4()
    org_id = uuid.uuid4()

    agent = _make_fake_agent(
        id=agent_id, name="paid-agent", price_per_task=1.0,
        api_key_hash=None, org_id=None,
    )

    mock_org = MagicMock()
    mock_org.id = org_id
    mock_org.balance = 10.0
    mock_org.tier = "free"

    mock_session = AsyncMock()
    mock_session.add = MagicMock()

    async def mock_get(model, obj_id):
        if obj_id == agent_id:
            return agent
        if obj_id == org_id:
            return mock_org
        return None

    mock_session.get = AsyncMock(side_effect=mock_get)

    mock_org_result = MagicMock()
    mock_org_result.scalar_one_or_none.return_value = mock_org
    mock_session.execute = AsyncMock(return_value=mock_org_result)

    mock_ctx = AsyncMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_ctx.__aexit__ = AsyncMock(return_value=False)
    mock_factory = MagicMock(return_value=mock_ctx)

    # Mock httpx to simulate agent FAILURE (ConnectError)
    with (
        patch("agentgate.server.routes.async_session", mock_factory),
        patch("agentgate.server.routes.settings") as mock_settings,
        patch("agentgate.server.routes._process_billing") as mock_billing,
    ):
        mock_settings.api_key = "admin-key"
        response = client.post(
            f"/agents/{agent_id}/task",
            json={"id": "t1", "message": {"parts": [{"type": "text", "text": "hi"}]}},
            headers={"Authorization": "Bearer org-key-123"},
        )

    # Agent unreachable => 502, billing should NOT have been called
    assert response.status_code == 502
    mock_billing.assert_not_called()


@pytest.mark.asyncio
async def test_process_billing_called_on_success():
    """Billing IS called after successful A2A response."""
    import uuid


    agent_id = uuid.uuid4()
    org_id = uuid.uuid4()

    agent = _make_fake_agent(
        id=agent_id, name="paid-ok", price_per_task=0.5,
        api_key_hash=None, org_id=None,
    )

    mock_org = MagicMock()
    mock_org.id = org_id
    mock_org.balance = 10.0
    mock_org.tier = "free"

    mock_session = AsyncMock()
    mock_session.add = MagicMock()

    async def mock_get(model, obj_id):
        if obj_id == agent_id:
            return agent
        if obj_id == org_id:
            return mock_org
        return None

    mock_session.get = AsyncMock(side_effect=mock_get)
    mock_session.execute = AsyncMock()
    mock_session.add = MagicMock()
    mock_session.commit = AsyncMock()

    mock_org_result = MagicMock()
    mock_org_result.scalar_one_or_none.return_value = mock_org
    mock_session.execute = AsyncMock(return_value=mock_org_result)

    mock_ctx = AsyncMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_ctx.__aexit__ = AsyncMock(return_value=False)
    mock_factory = MagicMock(return_value=mock_ctx)

    # Mock httpx to simulate SUCCESS
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"status": {"state": "completed"}}

    with (
        patch("agentgate.server.routes.async_session", mock_factory),
        patch("agentgate.server.routes.settings") as mock_settings,
        patch("agentgate.server.routes._process_billing",
              new_callable=AsyncMock,
              return_value=(True, None)) as mock_billing,
        patch("agentgate.server.routes.httpx.AsyncClient") as mock_httpx,
    ):
        mock_settings.api_key = "admin-key"
        mock_client_inst = AsyncMock()
        mock_client_inst.post = AsyncMock(return_value=mock_resp)
        mock_client_inst.__aenter__ = AsyncMock(return_value=mock_client_inst)
        mock_client_inst.__aexit__ = AsyncMock(return_value=False)
        mock_httpx.return_value = mock_client_inst

        response = client.post(
            f"/agents/{agent_id}/task",
            json={"id": "t2", "message": {"parts": [{"type": "text", "text": "hi"}]}},
            headers={"Authorization": "Bearer org-key-123"},
        )

    assert response.status_code == 200
    mock_billing.assert_called_once()


# ---------------------------------------------------------------------------
# Sessione #19: Marketplace shows pricing
# ---------------------------------------------------------------------------


def test_marketplace_page_has_pricing():
    response = client.get("/marketplace")
    assert response.status_code == 200
    html = response.text
    assert "price_per_task" in html or "card-price" in html


def test_marketplace_page_has_sort_by_price():
    response = client.get("/marketplace")
    html = response.text
    assert "price-low" in html or "Price" in html


def test_marketplace_has_pricing_link():
    response = client.get("/marketplace")
    assert "/pricing" in response.text


# ---------------------------------------------------------------------------
# Sessione #19: Guide page has monetization section
# ---------------------------------------------------------------------------


def test_guide_page_has_monetization():
    response = client.get("/guide")
    assert response.status_code == 200
    html = response.text
    assert "Pricing" in html or "pricing-tiers" in html
    assert "Wallet" in html or "wallet-topup" in html
    assert "Transaction" in html or "transactions" in html


def test_guide_page_has_billing_flow():
    response = client.get("/guide")
    html = response.text
    assert "billing-flow" in html or "Billing Flow" in html


def test_guide_page_has_sdk_wallet_example():
    response = client.get("/guide")
    html = response.text
    assert "get_org_wallet" in html or "topup_org" in html


# ---------------------------------------------------------------------------
# FASE 3: Pricing page
# ---------------------------------------------------------------------------


def test_pricing_page():
    response = client.get("/pricing")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "Pricing" in response.text
    assert "AgentGate" in response.text


def test_pricing_page_has_tiers():
    response = client.get("/pricing")
    html = response.text
    assert "Free" in html
    assert "Pro" in html
    assert "Enterprise" in html


# ---------------------------------------------------------------------------
# FASE 3: Billing dashboard page
# ---------------------------------------------------------------------------


def test_billing_redirects_to_account():
    response = client.get("/billing", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"] == "/account"


# ---------------------------------------------------------------------------
# Landing page has pricing link
# ---------------------------------------------------------------------------


def test_landing_page_has_pricing_link():
    response = client.get("/")
    assert "/pricing" in response.text


# ---------------------------------------------------------------------------
# Sessione #20: Streaming & WebSocket billing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stream_billing_not_called_on_error():
    """SSE streaming: billing should NOT be called when agent fails."""
    import uuid

    agent_id = uuid.uuid4()
    org_id = uuid.uuid4()

    agent = _make_fake_agent(
        id=agent_id, name="stream-paid", price_per_task=1.0,
        api_key_hash=None, org_id=None,
    )

    mock_org = MagicMock()
    mock_org.id = org_id
    mock_org.balance = 10.0
    mock_org.tier = "free"

    mock_session = AsyncMock()
    mock_session.add = MagicMock()

    async def mock_get(model, obj_id):
        if obj_id == agent_id:
            return agent
        if obj_id == org_id:
            return mock_org
        return None

    mock_session.get = AsyncMock(side_effect=mock_get)

    mock_org_result = MagicMock()
    mock_org_result.scalar_one_or_none.return_value = mock_org
    mock_session.execute = AsyncMock(return_value=mock_org_result)

    mock_ctx = AsyncMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_ctx.__aexit__ = AsyncMock(return_value=False)
    mock_factory = MagicMock(return_value=mock_ctx)

    with (
        patch("agentgate.server.routes.async_session", mock_factory),
        patch("agentgate.server.routes.settings") as mock_settings,
        patch("agentgate.server.routes._process_billing") as mock_billing,
    ):
        mock_settings.api_key = "admin-key"
        response = client.post(
            f"/agents/{agent_id}/task/stream",
            json={"id": "s1", "message": {"parts": [{"type": "text", "text": "hi"}]}},
            headers={"Authorization": "Bearer org-key-123"},
        )

    # Agent unreachable => error event, billing NOT called
    assert response.status_code == 200  # SSE always returns 200
    assert "error" in response.text
    mock_billing.assert_not_called()


@pytest.mark.asyncio
async def test_stream_billing_called_on_success():
    """SSE streaming: billing IS called after successful stream."""
    import uuid

    agent_id = uuid.uuid4()
    org_id = uuid.uuid4()

    agent = _make_fake_agent(
        id=agent_id, name="stream-ok", price_per_task=0.5,
        api_key_hash=None, org_id=None,
    )

    mock_org = MagicMock()
    mock_org.id = org_id
    mock_org.balance = 10.0
    mock_org.tier = "free"

    mock_session = AsyncMock()
    mock_session.add = MagicMock()

    async def mock_get(model, obj_id):
        if obj_id == agent_id:
            return agent
        if obj_id == org_id:
            return mock_org
        return None

    mock_session.get = AsyncMock(side_effect=mock_get)

    mock_org_result = MagicMock()
    mock_org_result.scalar_one_or_none.return_value = mock_org
    mock_session.execute = AsyncMock(return_value=mock_org_result)

    mock_ctx = AsyncMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_ctx.__aexit__ = AsyncMock(return_value=False)
    mock_factory = MagicMock(return_value=mock_ctx)

    # Build a mock streaming response
    mock_stream_resp = AsyncMock()
    mock_stream_resp.status_code = 200

    async def mock_aiter_text():
        yield '{"status": "completed"}'

    mock_stream_resp.aiter_text = mock_aiter_text
    mock_stream_resp.__aenter__ = AsyncMock(return_value=mock_stream_resp)
    mock_stream_resp.__aexit__ = AsyncMock(return_value=False)

    mock_client_inst = AsyncMock()
    mock_client_inst.stream = MagicMock(return_value=mock_stream_resp)
    mock_client_inst.__aenter__ = AsyncMock(return_value=mock_client_inst)
    mock_client_inst.__aexit__ = AsyncMock(return_value=False)

    with (
        patch("agentgate.server.routes.async_session", mock_factory),
        patch("agentgate.server.routes.settings") as mock_settings,
        patch("agentgate.server.routes._process_billing",
              new_callable=AsyncMock,
              return_value=(True, None)) as mock_billing,
        patch("agentgate.server.routes.httpx.AsyncClient") as mock_httpx,
    ):
        mock_settings.api_key = "admin-key"
        mock_httpx.return_value = mock_client_inst

        response = client.post(
            f"/agents/{agent_id}/task/stream",
            json={"id": "s2", "message": {"parts": [{"type": "text", "text": "hi"}]}},
            headers={"Authorization": "Bearer org-key-123"},
        )

    assert response.status_code == 200
    assert "result" in response.text
    mock_billing.assert_called_once()


@pytest.mark.asyncio
async def test_stream_billing_402_insufficient_balance():
    """SSE streaming: returns 402 when balance is insufficient."""
    import uuid

    agent_id = uuid.uuid4()
    org_id = uuid.uuid4()

    agent = _make_fake_agent(
        id=agent_id, name="stream-expensive", price_per_task=100.0,
        api_key_hash=None, org_id=None,
    )

    mock_org = MagicMock()
    mock_org.id = org_id
    mock_org.balance = 1.0
    mock_org.tier = "free"

    mock_session = AsyncMock()
    mock_session.add = MagicMock()

    async def mock_get(model, obj_id):
        if obj_id == agent_id:
            return agent
        return None

    mock_session.get = AsyncMock(side_effect=mock_get)

    mock_org_result = MagicMock()
    mock_org_result.scalar_one_or_none.return_value = mock_org
    mock_session.execute = AsyncMock(return_value=mock_org_result)

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
            f"/agents/{agent_id}/task/stream",
            json={"id": "s3", "message": {"parts": [{"type": "text", "text": "hi"}]}},
            headers={"Authorization": "Bearer org-key-123"},
        )

    # Pre-check fails with 402
    assert response.status_code == 402
    assert "Insufficient balance" in response.text


@pytest.mark.asyncio
async def test_ws_billing_402_insufficient_balance():
    """WebSocket: returns error when balance is insufficient."""
    import uuid

    agent_id = uuid.uuid4()
    org_id = uuid.uuid4()

    agent = _make_fake_agent(
        id=agent_id, name="ws-expensive", price_per_task=100.0,
        api_key_hash=None, org_id=None,
    )

    mock_org = MagicMock()
    mock_org.id = org_id
    mock_org.balance = 1.0
    mock_org.tier = "free"
    mock_org.name = "test-org"

    mock_session = AsyncMock()
    mock_session.add = MagicMock()

    async def mock_get(model, obj_id):
        if obj_id == agent_id:
            return agent
        if obj_id == org_id:
            return mock_org
        return None

    mock_session.get = AsyncMock(side_effect=mock_get)

    mock_org_result = MagicMock()
    mock_org_result.scalar_one_or_none.return_value = mock_org
    mock_session.execute = AsyncMock(return_value=mock_org_result)

    mock_ctx = AsyncMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_ctx.__aexit__ = AsyncMock(return_value=False)
    mock_factory = MagicMock(return_value=mock_ctx)

    with (
        patch("agentgate.server.routes.async_session", mock_factory),
        patch("agentgate.server.routes.settings") as mock_settings,
    ):
        mock_settings.api_key = "admin-key"
        with client.websocket_connect(f"/agents/{agent_id}/task/ws") as ws:
            # Auth first
            ws.send_text(json.dumps({"type": "auth", "token": "org-key-123"}))
            auth_resp = ws.receive_json()
            assert auth_resp["type"] == "status"

            # Send task — should fail with insufficient balance
            ws.send_text(json.dumps({
                "id": "w1",
                "message": {"parts": [{"type": "text", "text": "hi"}]},
            }))
            resp = ws.receive_json()
            assert resp["type"] == "error"
            assert "Insufficient balance" in resp["data"]


@pytest.mark.asyncio
async def test_ws_billing_called_on_success():
    """WebSocket: billing IS called after successful task."""
    import uuid

    agent_id = uuid.uuid4()
    org_id = uuid.uuid4()

    agent = _make_fake_agent(
        id=agent_id, name="ws-paid-ok", price_per_task=0.5,
        api_key_hash=None, org_id=None,
    )

    mock_org = MagicMock()
    mock_org.id = org_id
    mock_org.balance = 10.0
    mock_org.tier = "free"
    mock_org.name = "test-org"

    mock_session = AsyncMock()
    mock_session.add = MagicMock()

    async def mock_get(model, obj_id):
        if obj_id == agent_id:
            return agent
        if obj_id == org_id:
            return mock_org
        return None

    mock_session.get = AsyncMock(side_effect=mock_get)

    mock_org_result = MagicMock()
    mock_org_result.scalar_one_or_none.return_value = mock_org
    mock_session.execute = AsyncMock(return_value=mock_org_result)

    mock_ctx = AsyncMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_ctx.__aexit__ = AsyncMock(return_value=False)
    mock_factory = MagicMock(return_value=mock_ctx)

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"status": {"state": "completed"}}

    with (
        patch("agentgate.server.routes.async_session", mock_factory),
        patch("agentgate.server.routes.settings") as mock_settings,
        patch("agentgate.server.routes._process_billing",
              new_callable=AsyncMock,
              return_value=(True, None)) as mock_billing,
        patch("agentgate.server.routes.httpx.AsyncClient") as mock_httpx,
    ):
        mock_settings.api_key = "admin-key"
        mock_client_inst = AsyncMock()
        mock_client_inst.post = AsyncMock(return_value=mock_resp)
        mock_client_inst.__aenter__ = AsyncMock(return_value=mock_client_inst)
        mock_client_inst.__aexit__ = AsyncMock(return_value=False)
        mock_httpx.return_value = mock_client_inst

        with client.websocket_connect(f"/agents/{agent_id}/task/ws") as ws:
            # Auth first
            ws.send_text(json.dumps({"type": "auth", "token": "org-key-123"}))
            auth_resp = ws.receive_json()
            assert auth_resp["type"] == "status"

            # Send task
            ws.send_text(json.dumps({
                "id": "w2",
                "message": {"parts": [{"type": "text", "text": "hi"}]},
            }))
            # status message
            status_resp = ws.receive_json()
            assert status_resp["type"] == "status"
            # result
            result_resp = ws.receive_json()
            assert result_resp["type"] == "result"

    mock_billing.assert_called_once()


# ---------------------------------------------------------------------------
# UCP — /.well-known/ucp
# ---------------------------------------------------------------------------


def test_well_known_ucp():
    response = client.get("/.well-known/ucp")
    assert response.status_code == 200
    data = response.json()
    assert "ucp" in data
    assert data["ucp"]["version"] == "2026-03-01"
    assert "dev.ucp.shopping" in data["ucp"]["services"]
    assert len(data["ucp"]["capabilities"]) >= 1
    assert data["ucp"]["capabilities"][0]["name"] == "dev.ucp.shopping.checkout"
    assert data["platform"]["name"] == "AgentGate"


# ---------------------------------------------------------------------------
# UCP — Catalog
# ---------------------------------------------------------------------------


def test_ucp_catalog():
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = []
    mock_session = AsyncMock()
    mock_session.add = MagicMock()
    mock_session.execute = AsyncMock(return_value=mock_result)
    mock_ctx = AsyncMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_ctx.__aexit__ = AsyncMock(return_value=False)
    mock_factory = MagicMock(return_value=mock_ctx)

    with patch("agentgate.server.ucp_routes.async_session", mock_factory):
        response = client.get("/ucp/catalog")
    assert response.status_code == 200
    data = response.json()
    assert "products" in data
    assert "total" in data
    assert data["ucp"]["capability"] == "dev.ucp.shopping.catalog"


# ---------------------------------------------------------------------------
# UCP — Checkout sessions
# ---------------------------------------------------------------------------


def test_ucp_checkout_no_agent_id():
    response = client.post("/ucp/checkout", json={"task": {"id": "t1"}})
    assert response.status_code == 400


def test_ucp_checkout_no_task():
    response = client.post("/ucp/checkout", json={"agent_id": "test"})
    assert response.status_code == 400


def test_ucp_checkout_invalid_agent():
    mock_session = AsyncMock()
    mock_session.add = MagicMock()
    mock_session.get = AsyncMock(return_value=None)
    mock_ctx = AsyncMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_ctx.__aexit__ = AsyncMock(return_value=False)
    mock_factory = MagicMock(return_value=mock_ctx)

    with patch("agentgate.server.ucp_routes.async_session", mock_factory):
        response = client.post("/ucp/checkout", json={
            "agent_id": str(uuid.uuid4()),
            "task": {"id": "t1", "message": {"parts": [{"type": "text", "text": "hi"}]}},
        })
    assert response.status_code == 404


def test_ucp_checkout_free_agent():
    """Creating checkout for a free agent should return 400."""
    agent_id = str(uuid.uuid4())
    mock_agent = MagicMock()
    mock_agent.id = uuid.UUID(agent_id)
    mock_agent.name = "free-agent"
    mock_agent.price_per_task = 0.0

    mock_session = AsyncMock()
    mock_session.add = MagicMock()
    mock_session.get = AsyncMock(return_value=mock_agent)
    mock_ctx = AsyncMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_ctx.__aexit__ = AsyncMock(return_value=False)
    mock_factory = MagicMock(return_value=mock_ctx)

    with patch("agentgate.server.ucp_routes.async_session", mock_factory):
        response = client.post("/ucp/checkout", json={
            "agent_id": agent_id,
            "task": {"id": "t1"},
        })
    assert response.status_code == 400
    assert "free" in response.json()["detail"].lower()


def test_ucp_checkout_create_session():
    """Create a checkout session for a paid agent."""
    agent_id = str(uuid.uuid4())
    mock_agent = MagicMock()
    mock_agent.id = uuid.UUID(agent_id)
    mock_agent.name = "paid-agent"
    mock_agent.price_per_task = 1.50

    mock_session = AsyncMock()
    mock_session.add = MagicMock()
    mock_session.get = AsyncMock(return_value=mock_agent)
    mock_ctx = AsyncMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_ctx.__aexit__ = AsyncMock(return_value=False)
    mock_factory = MagicMock(return_value=mock_ctx)

    with patch("agentgate.server.ucp_routes.async_session", mock_factory):
        response = client.post("/ucp/checkout", json={
            "agent_id": agent_id,
            "task": {"id": "t1", "message": {"parts": [{"type": "text", "text": "hi"}]}},
        })
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "pending"
    assert data["agent_name"] == "paid-agent"
    assert data["amount"] == 1.50
    assert data["ucp"]["version"] == "2026-03-01"
    assert "session_id" in data


def test_ucp_checkout_get_session():
    """Get a previously created checkout session."""
    from agentgate.server.ucp_routes import _checkout_sessions

    session_id = "test-session-get"
    _checkout_sessions[session_id] = {
        "session_id": session_id,
        "status": "pending",
        "agent_id": str(uuid.uuid4()),
        "agent_name": "test",
        "amount": 1.0,
    }

    response = client.get(f"/ucp/checkout/{session_id}")
    assert response.status_code == 200
    assert response.json()["session_id"] == session_id

    # Cleanup
    del _checkout_sessions[session_id]


def test_ucp_checkout_get_missing():
    response = client.get("/ucp/checkout/nonexistent")
    assert response.status_code == 404


def test_ucp_checkout_complete_missing():
    response = client.post("/ucp/checkout/nonexistent/complete")
    assert response.status_code == 404


def test_ucp_checkout_complete_already_completed():
    from agentgate.server.ucp_routes import _checkout_sessions

    session_id = "test-session-done"
    _checkout_sessions[session_id] = {"status": "completed"}

    response = client.post(f"/ucp/checkout/{session_id}/complete")
    assert response.status_code == 400

    del _checkout_sessions[session_id]


# ---------------------------------------------------------------------------
# UCP — Agent card with UCP capability
# ---------------------------------------------------------------------------


def test_agent_card_ucp_paid():
    """Paid agent card should include UCP checkout capability."""
    agent_id = str(uuid.uuid4())
    mock_agent = MagicMock()
    mock_agent.id = uuid.UUID(agent_id)
    mock_agent.name = "paid-agent-card"
    mock_agent.description = "A paid agent"
    mock_agent.url = "http://paid.example.com"
    mock_agent.version = "1.0.0"
    mock_agent.skills = [{"id": "test", "name": "Test"}]
    mock_agent.price_per_task = 2.00

    mock_session = AsyncMock()
    mock_session.add = MagicMock()
    mock_session.get = AsyncMock(return_value=mock_agent)
    mock_ctx = AsyncMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_ctx.__aexit__ = AsyncMock(return_value=False)
    mock_factory = MagicMock(return_value=mock_ctx)

    with patch("agentgate.server.routes.async_session", mock_factory):
        response = client.get(f"/agents/{agent_id}/card")
    assert response.status_code == 200
    data = response.json()
    assert "ucp" in data
    assert data["ucp"]["version"] == "2026-03-01"
    assert "dev.ucp.shopping.checkout" in data["ucp"]["capabilities"]
    assert data["ucp"]["price_per_task"] == 2.00


def test_agent_card_ucp_free():
    """Free agent card should NOT include UCP data."""
    agent_id = str(uuid.uuid4())
    mock_agent = MagicMock()
    mock_agent.id = uuid.UUID(agent_id)
    mock_agent.name = "free-agent-card"
    mock_agent.description = "A free agent"
    mock_agent.url = "http://free.example.com"
    mock_agent.version = "1.0.0"
    mock_agent.skills = []
    mock_agent.price_per_task = 0.0

    mock_session = AsyncMock()
    mock_session.add = MagicMock()
    mock_session.get = AsyncMock(return_value=mock_agent)
    mock_ctx = AsyncMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_ctx.__aexit__ = AsyncMock(return_value=False)
    mock_factory = MagicMock(return_value=mock_ctx)

    with patch("agentgate.server.routes.async_session", mock_factory):
        response = client.get(f"/agents/{agent_id}/card")
    assert response.status_code == 200
    data = response.json()
    assert "ucp" not in data


# ---------------------------------------------------------------------------
# UCP — Task routing with UCP metadata
# ---------------------------------------------------------------------------


def test_task_routing_ucp_metadata():
    """Task routing to paid agent should include UCP metadata in response."""
    agent_id = str(uuid.uuid4())
    mock_agent = MagicMock()
    mock_agent.id = uuid.UUID(agent_id)
    mock_agent.name = "paid-echo"
    mock_agent.url = "http://paid.example.com"
    mock_agent.version = "1.0.0"
    mock_agent.skills = []
    mock_agent.tags = []
    mock_agent.org_id = None
    mock_agent.api_key_hash = None
    mock_agent.webhook_url = None
    mock_agent.price_per_task = 0.50

    mock_session = AsyncMock()
    mock_session.add = MagicMock()
    mock_session.get = AsyncMock(return_value=mock_agent)
    mock_ctx = AsyncMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_ctx.__aexit__ = AsyncMock(return_value=False)
    mock_factory = MagicMock(return_value=mock_ctx)

    # Mock A2A response
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "result": {"parts": [{"type": "text", "text": "hello"}]},
    }

    with (
        patch("agentgate.server.routes.async_session", mock_factory),
        patch("agentgate.server.routes.settings") as mock_settings,
        patch("agentgate.server.routes._process_billing",
              new_callable=AsyncMock,
              return_value=(True, None)),
        patch("agentgate.server.routes.httpx.AsyncClient") as mock_httpx,
    ):
        mock_settings.api_key = "admin-key"
        mock_client_inst = AsyncMock()
        mock_client_inst.post = AsyncMock(return_value=mock_resp)
        mock_client_inst.__aenter__ = AsyncMock(return_value=mock_client_inst)
        mock_client_inst.__aexit__ = AsyncMock(return_value=False)
        mock_httpx.return_value = mock_client_inst

        response = client.post(
            f"/agents/{agent_id}/task",
            json={
                "id": "ucp-test-1",
                "message": {"parts": [{"type": "text", "text": "hi"}]},
            },
        )

    assert response.status_code == 200
    data = response.json()
    assert "ucp" in data
    assert data["ucp"]["version"] == "2026-03-01"
    assert data["ucp"]["price_per_task"] == 0.50
    assert data["ucp"]["capability"] == "dev.ucp.shopping.checkout"
    assert "result" in data


# ---------------------------------------------------------------------------
# SDK — UCP methods (sync)
# ---------------------------------------------------------------------------


def test_sdk_ucp_discover():
    """SDK ucp_discover should call /.well-known/ucp."""
    from agentgate.sdk.client import AgentGateClient

    sdk = AgentGateClient("http://testserver", api_key="test-key")
    sdk._client = client  # reuse TestClient

    # The well-known endpoint exists
    response = sdk.ucp_discover()
    assert "ucp" in response
    assert response["ucp"]["version"] == "2026-03-01"
    assert "platform" in response


def test_sdk_ucp_catalog():
    """SDK ucp_catalog should call /ucp/catalog."""
    from agentgate.sdk.client import AgentGateClient

    sdk = AgentGateClient("http://testserver", api_key="test-key")
    sdk._client = client

    with patch("agentgate.server.ucp_routes.async_session") as mock_factory:
        mock_session = AsyncMock()
        mock_session.add = MagicMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)
        mock_factory.return_value = mock_ctx

        response = sdk.ucp_catalog()
    assert "products" in response
    assert response["total"] == 0


def test_sdk_ucp_checkout_create():
    """SDK ucp_checkout_create should POST /ucp/checkout."""
    from agentgate.sdk.client import AgentGateClient

    sdk = AgentGateClient("http://testserver", api_key="test-key")
    sdk._client = client

    # Without agent_id → 400
    from agentgate.sdk.client import AgentGateError

    try:
        sdk.ucp_checkout_create("", {"id": "t1", "message": "hi"})
    except AgentGateError as e:
        assert e.status_code == 400


def test_sdk_ucp_checkout_get_missing():
    """SDK ucp_checkout_get should return 404 for unknown session."""
    from agentgate.sdk.client import AgentGateClient, AgentGateError

    sdk = AgentGateClient("http://testserver", api_key="test-key")
    sdk._client = client

    try:
        sdk.ucp_checkout_get("nonexistent-session")
        assert False, "Should have raised"
    except AgentGateError as e:
        assert e.status_code == 404


def test_sdk_ucp_checkout_complete_missing():
    """SDK ucp_checkout_complete should return 404 for unknown session."""
    from agentgate.sdk.client import AgentGateClient, AgentGateError

    sdk = AgentGateClient("http://testserver", api_key="test-key")
    sdk._client = client

    try:
        sdk.ucp_checkout_complete("nonexistent-session")
        assert False, "Should have raised"
    except AgentGateError as e:
        assert e.status_code == 404


# ---------------------------------------------------------------------------
# Self-service Signup
# ---------------------------------------------------------------------------


def test_signup_page():
    response = client.get("/signup")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "Create your account" in response.text
    assert "AgentGate" in response.text


def test_signup_schema():
    from agentgate.server.schemas import SignupRequest

    req = SignupRequest(name="test-org", email="test@example.com")
    assert req.name == "test-org"
    assert req.email == "test@example.com"


def test_signup_schema_validation():
    from pydantic import ValidationError

    from agentgate.server.schemas import SignupRequest

    with pytest.raises(ValidationError):
        SignupRequest(name="", email="test@example.com")
    with pytest.raises(ValidationError):
        SignupRequest(name="test", email="")


def test_signup_endpoint_creates_org():
    mock_session = AsyncMock()
    mock_session.add = MagicMock()  # add() is sync, not async
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None  # No existing org
    mock_session.execute.return_value = mock_result

    mock_org = MagicMock()
    mock_org.id = uuid.uuid4()
    mock_org.name = "new-org"
    mock_org.tier = "free"

    async def fake_refresh(obj):
        obj.id = mock_org.id
        obj.name = "new-org"
        obj.tier = "free"

    mock_session.refresh = fake_refresh

    with patch("agentgate.server.org_routes.async_session") as mock_ctx:
        mock_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_ctx.return_value.__aexit__ = AsyncMock(return_value=False)

        response = client.post(
            "/orgs/signup",
            json={"name": "new-org", "email": "user@example.com"},
        )

    assert response.status_code == 201
    data = response.json()
    assert data["org_name"] == "new-org"
    assert "api_key" in data
    assert len(data["api_key"]) > 20
    assert data["tier"] == "free"


def test_signup_duplicate_org_name():
    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_existing = MagicMock()
    mock_result.scalar_one_or_none.return_value = mock_existing  # Already exists
    mock_session.execute.return_value = mock_result

    with patch("agentgate.server.org_routes.async_session") as mock_ctx:
        mock_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_ctx.return_value.__aexit__ = AsyncMock(return_value=False)

        response = client.post(
            "/orgs/signup",
            json={"name": "existing-org", "email": "user@example.com"},
        )

    assert response.status_code == 409
    assert "already taken" in response.json()["detail"]


def test_signup_no_auth_required():
    """Signup should not require any auth header (not 401/403)."""
    mock_session = AsyncMock()
    mock_session.add = MagicMock()  # add() is sync
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None
    mock_session.execute.return_value = mock_result
    mock_session.refresh = AsyncMock()

    with patch("agentgate.server.org_routes.async_session") as mock_ctx:
        mock_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_ctx.return_value.__aexit__ = AsyncMock(return_value=False)

        response = client.post(
            "/orgs/signup",
            json={"name": "noauth-org", "email": "noauth@example.com"},
        )

    # Must not be 401/403 — signup is public
    assert response.status_code != 401
    assert response.status_code != 403


def test_org_model_has_email():
    from agentgate.db.models import Organization

    assert hasattr(Organization, "email")
    org = Organization(name="test", api_key_hash="abc", email="test@example.com")
    assert org.email == "test@example.com"


def test_org_model_email_optional():
    from agentgate.db.models import Organization

    org = Organization(name="test", api_key_hash="abc")
    assert org.email is None


def test_landing_page_has_signup_link():
    response = client.get("/")
    assert response.status_code == 200
    assert "/signup" in response.text
    assert "Sign Up" in response.text


# ---------------------------------------------------------------------------
# Admin Panel
# ---------------------------------------------------------------------------


def test_admin_page_serves_html():
    response = client.get("/admin")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "Admin Panel" in response.text
    assert "Sign In" in response.text


def test_admin_login_success():
    response = client.post(
        "/admin/api/login",
        json={"username": "admin", "password": "changeme"},
    )
    assert response.status_code == 200
    data = response.json()
    assert "token" in data
    assert "|" in data["token"]


def test_admin_login_wrong_password():
    response = client.post(
        "/admin/api/login",
        json={"username": "admin", "password": "wrongpassword"},
    )
    assert response.status_code == 401


def test_admin_login_wrong_username():
    response = client.post(
        "/admin/api/login",
        json={"username": "nobody", "password": "changeme"},
    )
    assert response.status_code == 401


def _get_admin_token():
    r = client.post(
        "/admin/api/login",
        json={"username": "admin", "password": "changeme"},
    )
    return r.json()["token"]


def test_admin_dashboard_no_auth():
    response = client.get("/admin/api/dashboard")
    assert response.status_code == 401


def test_admin_dashboard_with_auth():
    token = _get_admin_token()
    mock_session = AsyncMock()

    # Mock all the scalar queries
    mock_scalar = MagicMock()
    mock_scalar.scalar.return_value = 0
    mock_session.execute.return_value = mock_scalar

    # Need to mock multiple execute calls with different returns
    call_count = [0]
    results = []
    # 6 scalar counts + 3 grouped queries
    for _ in range(6):
        m = MagicMock()
        m.scalar.return_value = 0
        results.append(m)
    for _ in range(3):
        m = MagicMock()
        m.all.return_value = []
        results.append(m)

    async def mock_execute(*args, **kwargs):
        idx = min(call_count[0], len(results) - 1)
        call_count[0] += 1
        r = results[idx]
        r.scalar.return_value = 0
        return r

    mock_session.execute = mock_execute

    with patch("agentgate.server.admin_routes.async_session") as mock_ctx:
        mock_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_ctx.return_value.__aexit__ = AsyncMock(return_value=False)

        response = client.get(
            "/admin/api/dashboard",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 200
    data = response.json()
    assert "total_users" in data
    assert "total_agents" in data
    assert "total_tasks" in data
    assert "signup_trend" in data


def test_admin_users_no_auth():
    response = client.get("/admin/api/users")
    assert response.status_code == 401


def test_admin_users_with_auth():
    token = _get_admin_token()
    mock_session = AsyncMock()

    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = []
    mock_session.execute.return_value = mock_result

    with patch("agentgate.server.admin_routes.async_session") as mock_ctx:
        mock_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_ctx.return_value.__aexit__ = AsyncMock(return_value=False)

        response = client.get(
            "/admin/api/users",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 200
    assert isinstance(response.json(), list)


def test_admin_agents_with_auth():
    token = _get_admin_token()
    mock_session = AsyncMock()

    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = []
    mock_session.execute.return_value = mock_result

    with patch("agentgate.server.admin_routes.async_session") as mock_ctx:
        mock_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_ctx.return_value.__aexit__ = AsyncMock(return_value=False)

        response = client.get(
            "/admin/api/agents",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 200
    assert isinstance(response.json(), list)


def test_admin_transactions_with_auth():
    token = _get_admin_token()
    mock_session = AsyncMock()

    mock_scalar = MagicMock()
    mock_scalar.scalar.return_value = 0
    mock_scalars = MagicMock()
    mock_scalars.scalars.return_value.all.return_value = []

    call_count = [0]

    async def mock_execute(*args, **kwargs):
        call_count[0] += 1
        if call_count[0] == 1:
            return mock_scalar
        return mock_scalars

    mock_session.execute = mock_execute

    with patch("agentgate.server.admin_routes.async_session") as mock_ctx:
        mock_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_ctx.return_value.__aexit__ = AsyncMock(return_value=False)

        response = client.get(
            "/admin/api/transactions",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 200
    data = response.json()
    assert "total" in data
    assert "transactions" in data


def test_admin_token_expired():
    # Manually create an expired token
    import time

    from agentgate.server.admin_routes import _make_token

    old_time = time.time
    time.time = lambda: old_time() - 100000  # token created far in the past
    token = _make_token("admin")
    time.time = old_time

    response = client.get(
        "/admin/api/dashboard",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 401


def test_admin_token_tampered():
    token = _get_admin_token()
    # Tamper with the signature
    parts = token.rsplit("|", 1)
    tampered = parts[0] + "|" + "0" * 64

    response = client.get(
        "/admin/api/dashboard",
        headers={"Authorization": f"Bearer {tampered}"},
    )
    assert response.status_code == 401


def test_admin_config_has_credentials():
    from agentgate.core.config import Settings

    s = Settings()
    assert hasattr(s, "admin_username")
    assert hasattr(s, "admin_password")
    assert s.admin_username == "admin"
    assert s.admin_password == "changeme"


def test_admin_delete_user_no_auth():
    response = client.delete("/admin/api/users/some-id")
    assert response.status_code == 401


def test_admin_delete_agent_no_auth():
    response = client.delete("/admin/api/agents/some-id")
    assert response.status_code == 401


def test_admin_user_detail_not_found():
    token = _get_admin_token()
    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None
    mock_session.execute.return_value = mock_result

    with patch("agentgate.server.admin_routes.async_session") as mock_ctx:
        mock_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_ctx.return_value.__aexit__ = AsyncMock(return_value=False)

        response = client.get(
            "/admin/api/users/00000000-0000-0000-0000-000000000000",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 404
