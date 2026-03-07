"""Chain Agent — Demonstrates agent-to-agent chaining via AgentGate.

Receives a math expression, routes it to calc-agent via AgentGate,
then routes the result to echo-agent. Returns a summary of the full chain.

Run with: uvicorn agent:app --port 9002
"""

import os

import httpx
from fastapi import FastAPI

app = FastAPI(title="Chain Agent")

AGENTGATE_URL = os.environ.get("AGENTGATE_URL", "http://api:8000")

# Cache agent IDs (discovered on first request)
_agent_ids: dict[str, str] = {}


async def discover_agents():
    """Discover agent IDs from AgentGate registry."""
    if _agent_ids:
        return
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(f"{AGENTGATE_URL}/agents/")
        agents = resp.json()
        for agent in agents:
            _agent_ids[agent["name"]] = agent["id"]


async def call_agent(agent_name: str, text: str, task_id: str) -> str:
    """Route a task to an agent via AgentGate and return the text result."""
    agent_id = _agent_ids.get(agent_name)
    if not agent_id:
        return f"Error: agent '{agent_name}' not found"

    payload = {
        "id": task_id,
        "message": {"parts": [{"type": "text", "text": text}]},
    }
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(f"{AGENTGATE_URL}/agents/{agent_id}/task", json=payload)
        if resp.status_code != 200:
            return f"Error: {resp.status_code} from {agent_name}"
        result = resp.json()
        parts = result.get("artifacts", [{}])[0].get("parts", [])
        return parts[0].get("text", "") if parts else ""


@app.post("/a2a")
async def handle_task(request: dict):
    """A2A task handler — chains calc-agent -> echo-agent via AgentGate."""
    message = request.get("message", {})
    parts = message.get("parts", [])
    text = parts[0].get("text", "") if parts else ""
    task_id = request.get("id", "chain-1")

    # Discover agents on first call
    await discover_agents()

    # Step 1: Send expression to calc-agent
    calc_result = await call_agent("calc-agent", text, f"{task_id}-calc")

    # Step 2: Send calc result to echo-agent
    echo_result = await call_agent("echo-agent", calc_result, f"{task_id}-echo")

    summary = f"Chain complete: '{text}' -> calc-agent: {calc_result} -> echo-agent: {echo_result}"

    return {
        "id": task_id,
        "status": {"state": "completed"},
        "artifacts": [
            {
                "parts": [{"type": "text", "text": summary}],
            }
        ],
    }


@app.get("/health")
async def health():
    return {"status": "ok", "agent": "chain-agent", "version": "1.0.0"}
