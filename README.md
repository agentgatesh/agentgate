# AgentGate

The unified gateway to deploy, connect, and monetize AI agents via MCP + A2A + UCP.

[![CI](https://github.com/agentgatesh/agentgate/actions/workflows/ci.yml/badge.svg)](https://github.com/agentgatesh/agentgate/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/agentgatesh)](https://pypi.org/project/agentgatesh/)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)](https://www.python.org/)
[![License: AGPL-3.0](https://img.shields.io/badge/license-AGPL--3.0-green)](LICENSE)

## What is AgentGate?

AgentGate is an open-source gateway that makes it easy to deploy, discover, and connect AI agents using standard protocols (MCP, A2A, UCP). Think of it as **Vercel + npm + Stripe, but for AI agents**.

- **Deploy in 5 minutes** — One command, your agent is live with an A2A-compliant Agent Card
- **Registry & Discovery** — Find agents by capability via `.well-known/agent.json`
- **Agent-to-Agent Routing** — Route tasks between agents through a single gateway
- **Agent Chaining** — Chain multiple agents together (e.g., calc -> echo)
- **Health Monitoring** — Automatic periodic health checks for all registered agents
- **Webhooks** — Get notified when your agent is invoked
- **Metrics Dashboard** — Live metrics with per-agent latency, error tracking, and rate limiting
- **Built-in Monetization** — Your agent can charge for its services (coming soon)

## Quick Start

### 1. Install

```bash
pip install agentgatesh
```

### 2. Create your agent config

```bash
mkdir my-agent && cd my-agent
cat > agentgate.yaml << 'EOF'
name: my-agent
description: A helpful AI agent
url: https://my-agent.example.com
version: 1.0.0
skills:
  - id: chat
    name: Chat
    description: General conversation
EOF
```

### 3. Deploy

```bash
export AGENTGATE_API_KEY=your-api-key
agentgate deploy ./my-agent
```

Your agent is now live with an A2A-compliant Agent Card.

### 4. Verify

```bash
agentgate list
agentgate status
```

## CLI Reference

| Command | Description |
|---------|-------------|
| `agentgate status` | Show server status |
| `agentgate list` | List all deployed agents |
| `agentgate list --skill chat` | Filter agents by skill |
| `agentgate deploy ./path` | Deploy an agent from a directory |
| `agentgate update <agent-id>` | Update an agent (--name, --version, etc.) |
| `agentgate delete <agent-id>` | Delete a deployed agent |
| `agentgate bump patch` | Bump version (major/minor/patch) + git tag |

All commands that modify data require `--api-key` or the `AGENTGATE_API_KEY` environment variable.

## Python SDK

```python
from agentgate.sdk import AgentGateClient

# Connect to AgentGate
client = AgentGateClient("https://agentgate.sh", api_key="your-key")

# List agents
agents = client.list_agents()

# Filter agents by skill
calc_agents = client.list_agents(skill="calculate")

# Register a new agent
agent = client.register_agent(
    name="my-agent",
    url="https://my-agent.example.com",
    description="A helpful agent",
)

# Send a task to an agent (A2A routing via AgentGate)
result = client.send_task(agent["id"], "Hello, agent!")
print(result["artifacts"][0]["parts"][0]["text"])

# Check agent health
health = client.get_agent_health(agent["id"])
print(health["status"])  # "healthy" or "unhealthy"

# Update an agent
client.update_agent(agent["id"], version="2.0.0")

# Delete an agent
client.delete_agent(agent["id"])

# Context manager for automatic cleanup
with AgentGateClient("https://agentgate.sh") as c:
    print(c.health())
```

## Agent Chaining

Agents can call other agents through AgentGate routing, enabling multi-step workflows:

```
Client -> chain-agent -> AgentGate -> calc-agent (compute)
                      -> AgentGate -> echo-agent (format)
```

See [examples/chain-agent/](examples/chain-agent/) for a working example that chains `calc-agent` and `echo-agent` together.

## Webhooks

Register a `webhook_url` when deploying an agent to receive notifications when it's invoked:

```yaml
# agentgate.yaml
name: my-agent
url: https://my-agent.example.com
webhook_url: https://my-server.com/webhook
```

When a task is routed to your agent, AgentGate sends a POST to your webhook:

```json
{
  "event": "task.completed",
  "agent_id": "uuid",
  "agent_name": "my-agent",
  "task_id": "task-1",
  "latency_ms": 42.5
}
```

## Health Monitoring

AgentGate automatically pings all registered agents every 60 seconds and tracks their health status:

- `GET /agents/{id}/health` — Health status for a specific agent
- `GET /health/agents` — Health status for all agents

Each agent's `/health` endpoint is checked. Status is `healthy`, `unhealthy`, or `unknown` (if no check has run yet).

## Metrics Dashboard

Access the live metrics dashboard at `/dashboard`. It shows:

- Total requests, errors, and average latency
- Per-agent request counts and latency (avg/p99)
- Error breakdown by type
- Auto-refreshes every 5 seconds

The dashboard and `/metrics` endpoint require an API key when one is configured.

## API Endpoints

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `GET` | `/health` | No | Server health check |
| `GET` | `/` | No | Landing page |
| `GET` | `/dashboard` | No | Metrics dashboard (UI) |
| `GET` | `/.well-known/agent.json` | No | A2A discovery endpoint |
| `GET` | `/health/agents` | No | All agents health status |
| `GET` | `/agents/` | No | List all agents (`?skill=` filter) |
| `GET` | `/agents/{id}` | No | Get agent details |
| `GET` | `/agents/{id}/card` | No | Get A2A Agent Card |
| `GET` | `/agents/{id}/health` | No | Get agent health status |
| `POST` | `/agents/` | Yes | Register a new agent |
| `PUT` | `/agents/{id}` | Yes | Update an agent |
| `DELETE` | `/agents/{id}` | Yes | Delete an agent |
| `POST` | `/agents/{id}/task` | No | Route A2A task to agent (rate limited) |
| `GET` | `/metrics` | Yes | Task routing metrics (JSON) |

## Development

```bash
# Clone the repo
git clone https://github.com/agentgatesh/agentgate.git
cd agentgate

# Install dependencies (requires uv)
uv sync --dev

# Run tests
uv run pytest -v

# Lint
uv run ruff check src/ tests/

# Start services locally
docker compose up -d

# Check status
uv run agentgate status --server http://localhost:8000
```

## Version Management

```bash
# Bump patch version (0.1.0 -> 0.1.1), create git tag
agentgate bump patch

# Bump minor version (0.1.0 -> 0.2.0)
agentgate bump minor

# Push to trigger PyPI publish
git push && git push --tags
```

## Architecture

- **Framework**: FastAPI + Uvicorn
- **Database**: PostgreSQL 16 + SQLAlchemy async + Alembic
- **CLI**: Click
- **Packaging**: uv + hatchling
- **Deploy**: Docker Compose + Caddy reverse proxy
- **Monitoring**: In-memory metrics + health checks
- **Rate Limiting**: Token bucket per IP (10 req/s, burst 20)

## License

AGPL-3.0 — see [LICENSE](LICENSE) for details.
