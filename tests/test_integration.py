"""E2E integration tests with a real PostgreSQL database.

Run with:
    docker compose -f docker-compose.test.yml up -d
    DATABASE_URL=postgresql+asyncpg://agentgate_test:testpass@localhost:5433/agentgate_test \
    API_KEY=test-admin-key \
    pytest tests/test_integration.py -v

These tests are skipped if DATABASE_URL is not set or the DB is unreachable.
"""

import os

import pytest

# Skip entire module if no test DATABASE_URL
pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="DATABASE_URL not set — run with docker-compose.test.yml",
)


@pytest.fixture(scope="session", autouse=True)
def setup_database():
    """Run Alembic migrations on the test database."""
    from alembic import command
    from alembic.config import Config

    alembic_cfg = Config("alembic.ini")
    db_url = os.environ["DATABASE_URL"].replace("+asyncpg", "")
    alembic_cfg.set_main_option("sqlalchemy.url", db_url)
    command.upgrade(alembic_cfg, "head")
    yield
    command.downgrade(alembic_cfg, "base")


@pytest.fixture(scope="session")
def test_client():
    """Create a FastAPI test client with real DB."""
    os.environ.setdefault("API_KEY", "test-admin-key")
    os.environ.setdefault("SECRET_KEY", "test-secret")

    from fastapi.testclient import TestClient

    from agentgate.server.app import app

    with TestClient(app) as client:
        yield client


@pytest.fixture(scope="session")
def api_key():
    return os.environ.get("API_KEY", "test-admin-key")


@pytest.fixture(scope="session")
def auth_headers(api_key):
    return {"Authorization": f"Bearer {api_key}"}


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


def test_health_endpoint(test_client):
    r = test_client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_v1_health_endpoint(test_client):
    r = test_client.get("/v1/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


# ---------------------------------------------------------------------------
# Agent CRUD (real DB)
# ---------------------------------------------------------------------------


def test_register_agent(test_client, auth_headers):
    r = test_client.post(
        "/agents/",
        json={
            "name": "integration-test-agent",
            "url": "http://localhost:9999",
            "description": "E2E test agent",
            "version": "1.0.0",
            "skills": [{"id": "test", "name": "Test Skill"}],
        },
        headers=auth_headers,
    )
    assert r.status_code == 201
    data = r.json()
    assert data["name"] == "integration-test-agent"
    assert data["version"] == "1.0.0"
    assert data["id"]


def test_register_agent_v2(test_client, auth_headers):
    """Register a second version of the same agent name."""
    r = test_client.post(
        "/agents/",
        json={
            "name": "integration-test-agent",
            "url": "http://localhost:9998",
            "description": "E2E test agent v2",
            "version": "2.0.0",
            "skills": [{"id": "test", "name": "Test Skill"}],
        },
        headers=auth_headers,
    )
    assert r.status_code == 201
    assert r.json()["version"] == "2.0.0"


def test_list_agents(test_client):
    r = test_client.get("/agents/")
    assert r.status_code == 200
    agents = r.json()
    names = [a["name"] for a in agents]
    assert "integration-test-agent" in names


def test_v1_list_agents(test_client):
    """Versioned API returns same data."""
    r = test_client.get("/v1/agents/")
    assert r.status_code == 200
    agents = r.json()
    names = [a["name"] for a in agents]
    assert "integration-test-agent" in names


def test_get_agent_by_name_versions(test_client):
    """Get all versions of an agent by name."""
    r = test_client.get("/agents/by-name/integration-test-agent")
    assert r.status_code == 200
    agents = r.json()
    assert len(agents) >= 2
    versions = [a["version"] for a in agents]
    assert "1.0.0" in versions
    assert "2.0.0" in versions


def test_get_agent_by_name_filter_version(test_client):
    """Filter by specific version."""
    r = test_client.get("/agents/by-name/integration-test-agent?version=1.0.0")
    assert r.status_code == 200
    agents = r.json()
    assert len(agents) == 1
    assert agents[0]["version"] == "1.0.0"


def test_get_agent_latest(test_client):
    """Get the latest version of an agent."""
    r = test_client.get("/agents/by-name/integration-test-agent/latest")
    assert r.status_code == 200
    agent = r.json()
    assert agent["version"] == "2.0.0"


def test_get_agent_by_id(test_client):
    r = test_client.get("/agents/")
    agents = r.json()
    agent = next(a for a in agents if a["name"] == "integration-test-agent")

    r2 = test_client.get(f"/agents/{agent['id']}")
    assert r2.status_code == 200
    assert r2.json()["name"] == "integration-test-agent"


def test_update_agent(test_client, auth_headers):
    r = test_client.get("/agents/")
    agents = r.json()
    agent = next(
        a for a in agents
        if a["name"] == "integration-test-agent" and a["version"] == "1.0.0"
    )

    r2 = test_client.put(
        f"/agents/{agent['id']}",
        json={"description": "Updated via E2E test"},
        headers=auth_headers,
    )
    assert r2.status_code == 200
    assert r2.json()["description"] == "Updated via E2E test"


def test_get_agent_card(test_client):
    r = test_client.get("/agents/")
    agents = r.json()
    agent = next(a for a in agents if a["name"] == "integration-test-agent")

    r2 = test_client.get(f"/agents/{agent['id']}/card")
    assert r2.status_code == 200
    card = r2.json()
    assert card["name"] == "integration-test-agent"
    assert "skills" in card


def test_agent_health(test_client):
    r = test_client.get("/agents/")
    agents = r.json()
    agent = next(a for a in agents if a["name"] == "integration-test-agent")

    r2 = test_client.get(f"/agents/{agent['id']}/health")
    assert r2.status_code == 200


def test_agent_logs_empty(test_client, auth_headers):
    r = test_client.get("/agents/")
    agents = r.json()
    agent = next(a for a in agents if a["name"] == "integration-test-agent")

    r2 = test_client.get(f"/agents/{agent['id']}/logs", headers=auth_headers)
    assert r2.status_code == 200
    assert r2.json() == []


def test_agent_usage(test_client, auth_headers):
    r = test_client.get("/agents/")
    agents = r.json()
    agent = next(a for a in agents if a["name"] == "integration-test-agent")

    r2 = test_client.get(f"/agents/{agent['id']}/usage", headers=auth_headers)
    assert r2.status_code == 200
    data = r2.json()
    assert data["total_invocations"] == 0


# ---------------------------------------------------------------------------
# Organization CRUD (real DB)
# ---------------------------------------------------------------------------


def test_create_org(test_client, auth_headers):
    r = test_client.post(
        "/orgs/",
        json={
            "name": "test-org",
            "api_key": "test-org-key-12345",
            "cost_per_invocation": 0.005,
            "rate_limit": 5.0,
            "rate_burst": 10,
        },
        headers=auth_headers,
    )
    assert r.status_code == 201
    data = r.json()
    assert data["name"] == "test-org"
    assert data["cost_per_invocation"] == 0.005


def test_list_orgs(test_client, auth_headers):
    r = test_client.get("/orgs/", headers=auth_headers)
    assert r.status_code == 200
    orgs = r.json()
    names = [o["name"] for o in orgs]
    assert "test-org" in names


def test_get_org(test_client, auth_headers):
    r = test_client.get("/orgs/", headers=auth_headers)
    org = next(o for o in r.json() if o["name"] == "test-org")

    r2 = test_client.get(f"/orgs/{org['id']}", headers=auth_headers)
    assert r2.status_code == 200
    assert r2.json()["name"] == "test-org"


def test_update_org(test_client, auth_headers):
    r = test_client.get("/orgs/", headers=auth_headers)
    org = next(o for o in r.json() if o["name"] == "test-org")

    r2 = test_client.put(
        f"/orgs/{org['id']}",
        json={"cost_per_invocation": 0.01},
        headers=auth_headers,
    )
    assert r2.status_code == 200
    assert r2.json()["cost_per_invocation"] == 0.01


def test_org_billing(test_client, auth_headers):
    r = test_client.get("/orgs/", headers=auth_headers)
    org = next(o for o in r.json() if o["name"] == "test-org")

    r2 = test_client.get(f"/orgs/{org['id']}/billing", headers=auth_headers)
    assert r2.status_code == 200
    data = r2.json()
    assert data["total_invocations"] == 0
    assert data["total_cost"] == 0.0


def test_org_billing_breakdown(test_client, auth_headers):
    r = test_client.get("/orgs/", headers=auth_headers)
    org = next(o for o in r.json() if o["name"] == "test-org")

    r2 = test_client.get(f"/orgs/{org['id']}/billing/breakdown", headers=auth_headers)
    assert r2.status_code == 200
    assert r2.json()["breakdown"] == []


# ---------------------------------------------------------------------------
# Well-known / Discovery
# ---------------------------------------------------------------------------


def test_well_known_agent_json(test_client):
    r = test_client.get("/.well-known/agent.json")
    assert r.status_code == 200
    data = r.json()
    assert data["name"] == "AgentGate"
    assert "agents" in data


def test_metrics_requires_auth(test_client):
    r = test_client.get("/metrics")
    assert r.status_code in (401, 403)


def test_metrics_with_auth(test_client, auth_headers):
    r = test_client.get("/metrics", headers=auth_headers)
    assert r.status_code == 200


# ---------------------------------------------------------------------------
# Cleanup — delete test data
# ---------------------------------------------------------------------------


def test_delete_agents(test_client, auth_headers):
    """Clean up test agents."""
    r = test_client.get("/agents/")
    agents = [a for a in r.json() if a["name"] == "integration-test-agent"]
    for agent in agents:
        r2 = test_client.delete(f"/agents/{agent['id']}", headers=auth_headers)
        assert r2.status_code == 204


def test_delete_org(test_client, auth_headers):
    """Clean up test org."""
    r = test_client.get("/orgs/", headers=auth_headers)
    orgs = [o for o in r.json() if o["name"] == "test-org"]
    for org in orgs:
        r2 = test_client.delete(f"/orgs/{org['id']}", headers=auth_headers)
        assert r2.status_code == 204
