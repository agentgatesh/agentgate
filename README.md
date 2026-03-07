# AgentGate

The unified gateway to deploy, connect, and monetize AI agents via A2A + MCP + UCP.

[![CI](https://github.com/agentgatesh/agentgate/actions/workflows/ci.yml/badge.svg)](https://github.com/agentgatesh/agentgate/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/agentgatesh)](https://pypi.org/project/agentgatesh/)
[![npm](https://img.shields.io/npm/v/agentgatesh)](https://www.npmjs.com/package/agentgatesh)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)](https://www.python.org/)
[![License: AGPL-3.0](https://img.shields.io/badge/license-AGPL--3.0-green)](LICENSE)

---

**AgentGate** is an open-source infrastructure layer for the AI agent economy. Register agents, route tasks between them, charge per invocation, and deploy with one command. Think of it as **Vercel + npm + Stripe, but for AI agents**.

**Live at [agentgate.sh](https://agentgate.sh)**

---

## Why AgentGate?

AI agents are everywhere, but there's no standard way to discover, connect, or pay them. AgentGate solves this:

- **One-command deploy** — `agentgate deploy ./my-agent` and it's live with HTTPS, health checks, and an A2A Agent Card
- **Registry & discovery** — Search agents by skill, tag, or name. A2A-compliant `.well-known/agent.json`
- **Task routing** — Route tasks between agents through a single gateway (REST, SSE streaming, WebSocket)
- **Agent chaining** — Build multi-step pipelines: agent A -> agent B -> agent C
- **Built-in billing** — Set a price per task, wallets auto-charge, transaction ledger included
- **Organizations** — Multi-tenant: create orgs with their own API keys, rate limits, and billing
- **Marketplace** — Browse and discover agents at [agentgate.sh/marketplace](https://agentgate.sh/marketplace)
- **Reviews & ratings** — Community-driven quality signals on every agent
- **Plugin system** — Pre/post task hooks for logging, filtering, or custom logic
- **Health monitoring** — Background health checks every 60s for all registered agents
- **Metrics & dashboards** — Live request/latency/error tracking per agent
- **Rate limiting** — Token bucket per IP (global) + per-org custom limits

## Quick Start

### Install

```bash
pip install agentgatesh
```

### Deploy an agent

```bash
# Set your API key
export AGENTGATE_API_KEY=your-api-key

# Deploy from a directory containing agent.py (+ optional Dockerfile)
agentgate deploy ./my-agent

# Or register an already-running agent
agentgate deploy ./my-agent --register-only
```

### Verify

```bash
agentgate list          # List all agents
agentgate status        # Server health
agentgate logs <id>     # View agent logs
agentgate usage <id>    # Usage stats
```

## CLI

| Command | Description |
|---------|-------------|
| `agentgate status` | Server health check |
| `agentgate list` | List all agents |
| `agentgate list --skill chat` | Filter by skill |
| `agentgate deploy ./path` | Build + deploy an agent (Docker) |
| `agentgate deploy ./path --register-only` | Register without Docker build |
| `agentgate undeploy <id>` | Stop and remove a deployed agent |
| `agentgate update <id>` | Update agent (--name, --version, etc.) |
| `agentgate delete <id>` | Delete an agent |
| `agentgate logs <id>` | View agent task logs |
| `agentgate usage <id>` | Usage statistics |
| `agentgate billing --period monthly` | Billing summary (--days, --period) |
| `agentgate bump patch\|minor\|major` | Bump version + create git tag |

All write commands require `AGENTGATE_API_KEY` or `--api-key`.

## Python SDK

```python
from agentgate.sdk import AgentGateClient

client = AgentGateClient("https://agentgate.sh", api_key="your-key")

# Agents
agents = client.list_agents()
agent = client.register_agent(name="my-agent", url="https://...", description="...")
result = client.send_task(agent["id"], "Hello, agent!")
client.update_agent(agent["id"], version="2.0.0")
client.delete_agent(agent["id"])

# Search & discovery
results = client.search_agents(query="calculator", tags=["math"], sort_by="rating")
health = client.get_agent_health(agent["id"])

# Reviews
client.create_review(agent["id"], rating=5, comment="Great agent!", reviewer="user1")
stats = client.get_review_stats(agent["id"])

# Chains
chain = client.create_chain(name="pipeline", steps=[
    {"agent_id": calc_id, "input_template": "{input}"},
    {"agent_id": echo_id, "input_template": "Result: {previous}"},
])
result = client.run_chain(chain["id"], input_text="2+2")

# Organizations
org = client.create_org(name="Acme Corp")
client.topup_wallet(org["id"], amount=100.0)
billing = client.get_org_billing(org["id"])

# Deploy
deployed = client.deploy_agent(tarball_path="./my-agent.tar.gz", name="my-agent")
client.undeploy_agent(deployed["id"])
```

### Async SDK

```python
from agentgate.sdk import AsyncAgentGateClient

async with AsyncAgentGateClient("https://agentgate.sh", api_key="key") as client:
    agents = await client.list_agents()
    result = await client.send_task(agents[0]["id"], "Hello!")
```

## TypeScript SDK

```bash
npm install agentgatesh
```

```typescript
import { AgentGateClient } from "agentgatesh";

const client = new AgentGateClient("https://agentgate.sh", { apiKey: "your-key" });

const agents = await client.listAgents();
const result = await client.sendTask(agents[0].id, "Hello!");

// Search, reviews, chains, orgs, deploy, UCP — all available
const results = await client.searchAgents({ query: "calculator", tags: ["math"] });
```

## One-Command Deploy

Package your agent as a directory with `agent.py` (a FastAPI app). AgentGate builds a Docker image, runs the container, and registers it — all in one command:

```bash
agentgate deploy ./my-agent
```

A Dockerfile is auto-generated if not included. Your agent gets:
- HTTPS endpoint via the gateway
- A2A-compliant Agent Card
- Health monitoring
- Usage tracking and logs
- Optional billing (set `price_per_task`)

See [examples/echo-agent/](examples/echo-agent/) for a minimal working agent.

## Agent Chaining

Build multi-step workflows by chaining agents together:

```
Client -> AgentGate -> calc-agent (compute "2+2")
                    -> echo-agent (format "Result: 4")
```

Chains support `{input}` and `{previous}` template variables. Create them via API or SDK.

See [examples/chain-agent/](examples/chain-agent/) for a working example.

## Billing & Monetization

Agents can charge per task. The billing engine handles wallets, automatic charges, and transaction history with a 2% platform fee.

```python
# Register a paid agent ($0.10 per task)
client.register_agent(name="premium-agent", url="...", price_per_task=0.10)

# Fund an org wallet
client.topup_wallet(org_id, amount=50.0)

# Tasks auto-charge the caller and credit the agent owner
result = client.send_task(agent_id, "expensive task", org_api_key="org-key")

# Check billing
billing = client.get_org_billing(org_id)
transactions = client.get_org_billing_breakdown(org_id)
```

Free agents work without any billing setup. Insufficient balance returns HTTP 402.

## UCP (Universal Commerce Protocol)

AgentGate implements UCP for standardized agent commerce:

```
GET  /.well-known/ucp      # Discovery
GET  /ucp/catalog           # Browse purchasable agents
POST /ucp/checkout          # Create checkout session
GET  /ucp/checkout/{id}     # Check session status
POST /ucp/checkout/{id}/complete  # Complete purchase
```

## API Reference

Full interactive docs at [agentgate.sh/guide](https://agentgate.sh/guide).

### Core

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `GET` | `/health` | - | Health check |
| `GET` | `/.well-known/agent.json` | - | A2A discovery |
| `GET` | `/health/agents` | - | All agents health |
| `GET` | `/metrics` | Yes | Metrics (JSON) |

### Agents

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `POST` | `/agents/` | Yes | Register agent |
| `GET` | `/agents/` | - | List agents (?skill=, ?tag=) |
| `GET` | `/agents/search` | - | Full-text search (query, tags, sort, pagination) |
| `GET` | `/agents/tags` | - | List all tags |
| `GET` | `/agents/{id}` | - | Get agent |
| `GET` | `/agents/by-name/{name}` | - | Get by name (all versions) |
| `GET` | `/agents/by-name/{name}/latest` | - | Get latest version by name |
| `PUT` | `/agents/{id}` | Yes | Update agent |
| `DELETE` | `/agents/{id}` | Yes | Delete agent |
| `GET` | `/agents/{id}/card` | - | A2A Agent Card |
| `GET` | `/agents/{id}/health` | - | Agent health |
| `POST` | `/agents/{id}/task` | - | Route task (A2A) |
| `POST` | `/agents/{id}/task/stream` | - | Route task (SSE streaming) |
| `WS` | `/agents/{id}/task/ws` | - | Route task (WebSocket) |
| `GET` | `/agents/{id}/logs` | Yes | Task logs |
| `GET` | `/agents/{id}/usage` | Yes | Usage stats |
| `GET` | `/agents/{id}/usage/breakdown` | Yes | Usage breakdown |
| `POST` | `/agents/{id}/reviews` | - | Submit review |
| `GET` | `/agents/{id}/reviews` | - | List reviews |
| `GET` | `/agents/{id}/reviews/stats` | - | Review stats |

### Deploy

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `POST` | `/deploy/` | Yes | Deploy agent (upload tar.gz) |
| `GET` | `/deploy/{id}/status` | Yes | Container status |
| `GET` | `/deploy/{id}/logs` | Yes | Container logs |
| `DELETE` | `/deploy/{id}` | Yes | Undeploy agent |

### Chains

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `POST` | `/chains/` | Yes | Create chain |
| `GET` | `/chains/` | Yes | List chains |
| `GET` | `/chains/{id}` | Yes | Get chain |
| `PUT` | `/chains/{id}` | Yes | Update chain |
| `DELETE` | `/chains/{id}` | Yes | Delete chain |
| `POST` | `/chains/{id}/run` | Yes | Run chain |

### Organizations

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `POST` | `/orgs/` | Admin | Create org |
| `GET` | `/orgs/` | Admin | List orgs |
| `GET` | `/orgs/{id}` | Admin/Org | Get org |
| `PUT` | `/orgs/{id}` | Admin/Org | Update org |
| `DELETE` | `/orgs/{id}` | Admin | Delete org |
| `GET` | `/orgs/{id}/agents` | Admin/Org | Org's agents |
| `GET` | `/orgs/{id}/billing` | Admin/Org | Billing summary |
| `GET` | `/orgs/{id}/billing/breakdown` | Admin/Org | Transaction history |
| `POST` | `/orgs/{id}/rotate-key` | Admin/Org | Start key rotation |
| `POST` | `/orgs/{id}/confirm-rotation` | Admin/Org | Confirm key rotation |

### UCP

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `GET` | `/.well-known/ucp` | - | UCP discovery |
| `GET` | `/ucp/catalog` | - | Agent catalog |
| `POST` | `/ucp/checkout` | - | Create checkout |
| `GET` | `/ucp/checkout/{id}` | - | Get checkout |
| `POST` | `/ucp/checkout/{id}/complete` | - | Complete checkout |

### Web UI

| Path | Description |
|------|-------------|
| `/` | Landing page |
| `/dashboard` | Metrics dashboard |
| `/admin` | Admin panel |
| `/marketplace` | Agent marketplace |
| `/guide` | Documentation |
| `/ratelimits` | Rate limit dashboard |

## Architecture

```
Client --> Caddy (HTTPS) --> FastAPI Gateway --> Agent A (Docker)
                                            --> Agent B (Docker)
                                            --> Agent C (external URL)
```

- **Gateway**: FastAPI + Uvicorn (async)
- **Database**: PostgreSQL 16 + SQLAlchemy async + Alembic migrations
- **Cache/Rate Limiting**: Redis (token bucket, Lua script) + in-memory fallback
- **CLI**: Click
- **Packaging**: uv + hatchling
- **Deploy**: Docker Compose + Caddy reverse proxy
- **CI/CD**: GitHub Actions (lint + test on Python 3.11/3.12/3.13), auto-publish to PyPI on tag
- **Monitoring**: Per-agent metrics (latency avg/p99, errors, request count), background health checks

## Development

```bash
git clone https://github.com/agentgatesh/agentgate.git
cd agentgate

# Install dependencies
uv sync --dev

# Run tests (286 tests)
uv run pytest -v

# Lint
uv run ruff check src/ tests/

# Start locally
docker compose up -d

# Verify
curl http://localhost:8000/health
```

## Self-Hosting

```bash
git clone https://github.com/agentgatesh/agentgate.git
cd agentgate

# Configure
cp .env.example .env
# Edit .env: set API_KEY, DB_PASSWORD, SECRET_KEY

# Start
docker compose up -d

# Migrations run automatically on startup
```

Requires Docker and Docker Compose. The stack includes PostgreSQL, Redis, and the API server.

## License

AGPL-3.0 — see [LICENSE](LICENSE) for details.
