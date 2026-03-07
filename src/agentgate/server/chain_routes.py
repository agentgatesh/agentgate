"""Agent chaining: define and execute multi-step agent pipelines."""

import json
import logging
import uuid

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import desc, select

from agentgate.db.engine import async_session
from agentgate.db.models import Agent, Chain, Organization
from agentgate.server.metrics import Timer, record_request
from agentgate.server.routes import _save_task_log, verify_api_key_or_org
from agentgate.server.schemas import ChainCreate, ChainResponse, ChainUpdate

logger = logging.getLogger("agentgate.chaining")

router = APIRouter(prefix="/chains", tags=["chains"])


@router.post("/", response_model=ChainResponse, status_code=201)
async def create_chain(
    data: ChainCreate,
    caller_org: Organization | None = Depends(verify_api_key_or_org),
):
    """Create a named chain of agent steps. Requires API key."""
    org_id = caller_org.id if caller_org else data.org_id

    # Validate that all agent IDs exist
    async with async_session() as session:
        for step in data.steps:
            agent = await session.get(Agent, uuid.UUID(step.agent_id))
            if not agent:
                raise HTTPException(
                    status_code=400,
                    detail=f"Agent {step.agent_id} not found",
                )

        chain = Chain(
            name=data.name,
            description=data.description,
            steps=[s.model_dump() for s in data.steps],
            org_id=org_id,
        )
        session.add(chain)
        await session.commit()
        await session.refresh(chain)
        return chain


@router.get("/", response_model=list[ChainResponse])
async def list_chains(
    caller_org: Organization | None = Depends(verify_api_key_or_org),
):
    """List all chains. Org-scoped if using org key."""
    async with async_session() as session:
        query = select(Chain).order_by(desc(Chain.created_at))
        if caller_org:
            query = query.where(Chain.org_id == caller_org.id)
        result = await session.execute(query)
        return result.scalars().all()


@router.get("/{chain_id}", response_model=ChainResponse)
async def get_chain(
    chain_id: uuid.UUID,
    caller_org: Organization | None = Depends(verify_api_key_or_org),
):
    async with async_session() as session:
        chain = await session.get(Chain, chain_id)
        if not chain:
            raise HTTPException(status_code=404, detail="Chain not found")
        if caller_org and chain.org_id != caller_org.id:
            raise HTTPException(status_code=403, detail="Access denied")
        return chain


@router.put("/{chain_id}", response_model=ChainResponse)
async def update_chain(
    chain_id: uuid.UUID,
    data: ChainUpdate,
    caller_org: Organization | None = Depends(verify_api_key_or_org),
):
    async with async_session() as session:
        chain = await session.get(Chain, chain_id)
        if not chain:
            raise HTTPException(status_code=404, detail="Chain not found")
        if caller_org and chain.org_id != caller_org.id:
            raise HTTPException(status_code=403, detail="Access denied")
        update_data = data.model_dump(exclude_none=True)
        if "steps" in update_data:
            update_data["steps"] = [
                s.model_dump() if hasattr(s, "model_dump") else s
                for s in data.steps
            ]
        for field, value in update_data.items():
            setattr(chain, field, value)
        await session.commit()
        await session.refresh(chain)
        return chain


@router.delete("/{chain_id}", status_code=204)
async def delete_chain(
    chain_id: uuid.UUID,
    caller_org: Organization | None = Depends(verify_api_key_or_org),
):
    async with async_session() as session:
        chain = await session.get(Chain, chain_id)
        if not chain:
            raise HTTPException(status_code=404, detail="Chain not found")
        if caller_org and chain.org_id != caller_org.id:
            raise HTTPException(status_code=403, detail="Access denied")
        await session.delete(chain)
        await session.commit()


@router.post("/{chain_id}/run")
async def run_chain(
    chain_id: uuid.UUID,
    body: dict,
    request: Request,
    caller_org: Organization | None = Depends(verify_api_key_or_org),
):
    """Execute a chain: run each step sequentially, passing output to next step.

    Body:
        {"input": "initial text input for the first agent"}

    Each step calls POST /agents/{agent_id}/task internally.
    If a step has input_template, it formats the template with {input} and {previous}.
    Otherwise, the previous step's output text is used as input.

    Returns the full execution trace with each step's result.
    """
    client_ip = request.client.host if request.client else "unknown"

    async with async_session() as session:
        chain = await session.get(Chain, chain_id)
        if not chain:
            raise HTTPException(status_code=404, detail="Chain not found")
        if caller_org and chain.org_id != caller_org.id:
            raise HTTPException(status_code=403, detail="Access denied")

    initial_input = body.get("input", "")
    steps = chain.steps
    current_input = initial_input
    trace = []

    for i, step in enumerate(steps):
        agent_id = step["agent_id"]
        input_template = step.get("input_template")
        agent_api_key = step.get("agent_api_key")

        # Resolve agent
        async with async_session() as session:
            agent = await session.get(Agent, uuid.UUID(agent_id))
            if not agent:
                trace.append({
                    "step": i + 1, "agent_id": agent_id,
                    "status": "error", "error": f"Agent {agent_id} not found",
                })
                return {"chain_id": str(chain_id), "status": "error", "trace": trace}

        # Build task text
        if input_template:
            task_text = input_template.format(
                input=initial_input, previous=current_input,
            )
        else:
            task_text = current_input

        # Build A2A task payload
        task_payload = {
            "id": f"chain-{chain_id}-step-{i + 1}",
            "message": {"parts": [{"type": "text", "text": task_text}]},
        }

        target_url = f"{agent.url.rstrip('/')}/a2a"
        headers = {}
        if agent_api_key:
            headers["Authorization"] = f"Bearer {agent_api_key}"

        with Timer() as t:
            try:
                async with httpx.AsyncClient(timeout=30.0) as client:
                    resp = await client.post(target_url, json=task_payload, headers=headers)
            except httpx.ConnectError:
                record_request(agent.name, t.elapsed_ms, error_type="connect_error")
                await _save_task_log(
                    agent.id, agent.name, client_ip,
                    task_payload["id"], "error", t.elapsed_ms, "connect_error",
                )
                trace.append({
                    "step": i + 1, "agent_id": agent_id, "agent_name": agent.name,
                    "status": "error", "error": f"Cannot reach agent at {agent.url}",
                    "latency_ms": round(t.elapsed_ms, 1),
                })
                return {"chain_id": str(chain_id), "status": "error", "trace": trace}
            except httpx.TimeoutException:
                record_request(agent.name, t.elapsed_ms, error_type="timeout")
                await _save_task_log(
                    agent.id, agent.name, client_ip,
                    task_payload["id"], "error", t.elapsed_ms, "timeout",
                )
                trace.append({
                    "step": i + 1, "agent_id": agent_id, "agent_name": agent.name,
                    "status": "error", "error": f"Agent at {agent.url} timed out",
                    "latency_ms": round(t.elapsed_ms, 1),
                })
                return {"chain_id": str(chain_id), "status": "error", "trace": trace}

        if resp.status_code >= 400:
            record_request(agent.name, t.elapsed_ms, error_type=f"http_{resp.status_code}")
            await _save_task_log(
                agent.id, agent.name, client_ip,
                task_payload["id"], "error", t.elapsed_ms, f"http_{resp.status_code}",
            )
            trace.append({
                "step": i + 1, "agent_id": agent_id, "agent_name": agent.name,
                "status": "error", "error": f"Agent returned {resp.status_code}: {resp.text}",
                "latency_ms": round(t.elapsed_ms, 1),
            })
            return {"chain_id": str(chain_id), "status": "error", "trace": trace}

        record_request(agent.name, t.elapsed_ms)
        await _save_task_log(
            agent.id, agent.name, client_ip,
            task_payload["id"], "success", t.elapsed_ms,
        )

        result_data = resp.json()

        # Extract text from A2A response for next step
        result_text = _extract_text(result_data)
        current_input = result_text

        trace.append({
            "step": i + 1,
            "agent_id": agent_id,
            "agent_name": agent.name,
            "status": "success",
            "input": task_text,
            "output": result_text,
            "raw_response": result_data,
            "latency_ms": round(t.elapsed_ms, 1),
        })

        logger.info(
            "Chain %s step %d/%d: %s -> %.0fms",
            chain.name, i + 1, len(steps), agent.name, t.elapsed_ms,
        )

    return {
        "chain_id": str(chain_id),
        "chain_name": chain.name,
        "status": "success",
        "final_output": current_input,
        "trace": trace,
    }


def _extract_text(response: dict) -> str:
    """Extract text from an A2A response payload."""
    # Try top-level artifacts[].parts[].text (standard A2A task response)
    for artifact in response.get("artifacts", []):
        for part in artifact.get("parts", []):
            if part.get("type") == "text":
                return part["text"]

    # Try result.artifacts or result.message (wrapped format)
    if "result" in response:
        result = response["result"]
        if isinstance(result, dict):
            for artifact in result.get("artifacts", []):
                for part in artifact.get("parts", []):
                    if part.get("type") == "text":
                        return part["text"]
            msg = result.get("message", {})
            for part in msg.get("parts", []):
                if part.get("type") == "text":
                    return part["text"]
        if isinstance(result, str):
            return result

    # Try message.parts[].text
    msg = response.get("message", {})
    if isinstance(msg, dict):
        for part in msg.get("parts", []):
            if part.get("type") == "text":
                return part["text"]

    # Try direct text field
    if "text" in response:
        return response["text"]

    # Fallback: stringify
    return json.dumps(response)
