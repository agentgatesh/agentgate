# AgentGate

The unified gateway to deploy, connect, and monetize AI agents via MCP + A2A + UCP.

## What is AgentGate?

AgentGate is an open-source gateway that makes it easy to deploy, discover, and connect AI agents using standard protocols (MCP, A2A, UCP). Think of it as **Vercel + npm + Stripe, but for AI agents**.

- **Deploy in 5 minutes** — One command, your agent is live with an A2A-compliant Agent Card
- **Registry & Discovery** — Find agents by capability, compose them into workflows
- **Built-in Monetization** — Your agent can charge for its services

## Quick Start

```bash
pip install agentgate
agentgate deploy ./my-agent
```

## Development

```bash
# Clone the repo
git clone https://github.com/agentgatesh/agentgate.git
cd agentgate

# Start services
docker compose up -d

# Or run locally with uv
uv sync
uv run agentgate status
```

## License

AGPL-3.0 — see [LICENSE](LICENSE) for details.
