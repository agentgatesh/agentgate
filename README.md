# AgentGate

The unified gateway to deploy, connect, and monetize AI agents via MCP + A2A + UCP.

[![CI](https://github.com/agentgatesh/agentgate/actions/workflows/ci.yml/badge.svg)](https://github.com/agentgatesh/agentgate/actions/workflows/ci.yml)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)](https://www.python.org/)
[![License: AGPL-3.0](https://img.shields.io/badge/license-AGPL--3.0-green)](LICENSE)

## What is AgentGate?

AgentGate is an open-source gateway that makes it easy to deploy, discover, and connect AI agents using standard protocols (MCP, A2A, UCP). Think of it as **Vercel + npm + Stripe, but for AI agents**.

- **Deploy in 5 minutes** — One command, your agent is live with an A2A-compliant Agent Card
- **Registry & Discovery** — Find agents by capability via `.well-known/agent.json`
- **Built-in Monetization** — Your agent can charge for its services (coming soon)

## Quick Start

### 1. Install

```bash
pip install agentgate
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
| `agentgate deploy ./path` | Deploy an agent from a directory |
| `agentgate update <agent-id>` | Update an agent (--name, --version, etc.) |
| `agentgate delete <agent-id>` | Delete a deployed agent |

All commands that modify data require `--api-key` or the `AGENTGATE_API_KEY` environment variable.

## Python SDK

```python
from agentgate.sdk import AgentGateClient

# Connect to AgentGate
client = AgentGateClient("https://agentgate.sh", api_key="your-key")

# List agents
agents = client.list_agents()

# Register a new agent
agent = client.register_agent(
    name="my-agent",
    url="https://my-agent.example.com",
    description="A helpful agent",
)

# Send a task to an agent (A2A routing via AgentGate)
result = client.send_task(agent["id"], "Hello, agent!")
print(result["artifacts"][0]["parts"][0]["text"])

# Update an agent
client.update_agent(agent["id"], version="2.0.0")

# Delete an agent
client.delete_agent(agent["id"])

# Context manager for automatic cleanup
with AgentGateClient("https://agentgate.sh") as c:
    print(c.health())
```

## API Endpoints

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `GET` | `/health` | No | Server health check |
| `GET` | `/` | No | Landing page |
| `GET` | `/.well-known/agent.json` | No | A2A discovery endpoint |
| `GET` | `/agents/` | No | List all agents |
| `GET` | `/agents/{id}` | No | Get agent details |
| `GET` | `/agents/{id}/card` | No | Get A2A Agent Card |
| `POST` | `/agents/` | Yes | Register a new agent |
| `PUT` | `/agents/{id}` | Yes | Update an agent |
| `DELETE` | `/agents/{id}` | Yes | Delete an agent |
| `POST` | `/agents/{id}/task` | No | Route A2A task to agent |

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

## Architecture

- **Framework**: FastAPI + Uvicorn
- **Database**: PostgreSQL 16 + SQLAlchemy async + Alembic
- **CLI**: Click
- **Packaging**: uv + hatchling
- **Deploy**: Docker Compose + Caddy reverse proxy

## License

AGPL-3.0 — see [LICENSE](LICENSE) for details.
