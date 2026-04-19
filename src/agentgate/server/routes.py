import asyncio
import json
import logging
import uuid

import httpx
from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    HTTPException,
    Query,
    Request,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy import select
from sse_starlette.sse import EventSourceResponse

# settings is re-imported for backwards-compat with tests that patch
# `agentgate.server.routes.settings`. The runtime code no longer reads
# settings directly from this module.
from agentgate.core.config import settings  # noqa: F401
from agentgate.db.engine import async_session
from agentgate.db.models import Agent, Organization
from agentgate.server.agent_logs import (  # noqa: F401 (re-export)
    agent_health,
    get_agent_logs,
    get_agent_usage,
    get_agent_usage_breakdown,
)
from agentgate.server.agent_logs import router as _logs_router
from agentgate.server.agent_reviews import (  # noqa: F401 (re-export)
    create_review,
    list_reviews,
    review_stats,
)
from agentgate.server.agent_reviews import router as _reviews_router
from agentgate.server.agent_search import (  # noqa: F401 (re-export)
    get_agent_latest,
    get_agent_versions,
    list_tags,
    search_agents,
)
from agentgate.server.agent_search import router as _search_router
from agentgate.server.auth import (
    bearer_scheme,  # noqa: F401 (re-export for tests/imports)
    bearer_scheme_optional,
    hash_api_key,
    is_admin_key,  # noqa: F401 (re-export)
)
from agentgate.server.billing import process_charge
from agentgate.server.deps import verify_api_key_or_org  # noqa: F401 (re-export)
from agentgate.server.metrics import Timer, record_request
from agentgate.server.plugins import plugin_manager
from agentgate.server.ratelimit import RateLimiter, task_limiter
from agentgate.server.schemas import (
    AgentCard,
    AgentCreate,
    AgentResponse,
    AgentUpdate,
)
from agentgate.server.task_runner import fire_webhook, save_task_log
from agentgate.server.url_validation import UnsafeURLError, validate_url

logger = logging.getLogger("agentgate.routing")

router = APIRouter(prefix="/agents", tags=["agents"])

# Sub-routers registered before dynamic /{agent_id} routes so path matching
# resolves "/tags", "/search", "/by-name/*" before the catch-all UUID route.
router.include_router(_search_router)
router.include_router(_reviews_router)
router.include_router(_logs_router)


@router.post("/", response_model=AgentResponse, status_code=201)
async def register_agent(
    data: AgentCreate,
    caller_org: Organization | None = Depends(verify_api_key_or_org),
):
    # SSRF guard: user-supplied url / webhook_url cannot point at private
    # IPs, loopback, or cloud metadata endpoints. Internal deployed-agent
    # URLs are populated by deploy_routes.py, not this handler.
    try:
        validate_url(data.url)
        if data.webhook_url:
            validate_url(data.webhook_url)
    except UnsafeURLError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid URL: {exc}")

    # If org key is used, force org_id to the caller's org
    org_id = caller_org.id if caller_org else data.org_id
    async with async_session() as session:
        agent = Agent(
            name=data.name,
            description=data.description,
            url=data.url,
            version=data.version,
            skills=data.skills,
            tags=data.tags,
            webhook_url=data.webhook_url,
            price_per_task=data.price_per_task,
            org_id=org_id,
            api_key_hash=hash_api_key(data.agent_api_key) if data.agent_api_key else None,
        )
        session.add(agent)
        await session.commit()
        await session.refresh(agent)
        return agent


@router.get("/", response_model=list[AgentResponse])
async def list_agents(
    skill: str | None = None,
    tag: str | None = Query(default=None),
):
    async with async_session() as session:
        query = select(Agent).order_by(Agent.created_at.desc())
        result = await session.execute(query)
        agents = result.scalars().all()
        if skill:
            skill_lower = skill.lower()
            agents = [
                a for a in agents
                if any(
                    skill_lower in s.get("id", "").lower()
                    or skill_lower in s.get("name", "").lower()
                    for s in (a.skills or [])
                )
            ]
        if tag:
            tag_lower = tag.lower()
            agents = [
                a for a in agents
                if any(tag_lower == t.lower() for t in (a.tags or []))
            ]
        return agents


@router.get("/{agent_id}", response_model=AgentResponse)
async def get_agent(agent_id: uuid.UUID):
    async with async_session() as session:
        agent = await session.get(Agent, agent_id)
        if not agent:
            raise HTTPException(status_code=404, detail="Agent not found")
        return agent


@router.put("/{agent_id}", response_model=AgentResponse)
async def update_agent(
    agent_id: uuid.UUID,
    data: AgentUpdate,
    caller_org: Organization | None = Depends(verify_api_key_or_org),
):
    # SSRF guard on any user-supplied URL change.
    try:
        if data.url is not None:
            validate_url(data.url)
        if data.webhook_url is not None and data.webhook_url != "":
            validate_url(data.webhook_url)
    except UnsafeURLError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid URL: {exc}")

    async with async_session() as session:
        agent = await session.get(Agent, agent_id)
        if not agent:
            raise HTTPException(status_code=404, detail="Agent not found")
        # Org can only update its own agents
        if caller_org and agent.org_id != caller_org.id:
            raise HTTPException(status_code=403, detail="Access denied to this agent")
        update_data = data.model_dump(exclude_none=True)
        if "agent_api_key" in update_data:
            key = update_data.pop("agent_api_key")
            if key:
                agent.api_key_hash = hash_api_key(key)
            else:
                agent.api_key_hash = None
        for field, value in update_data.items():
            setattr(agent, field, value)
        await session.commit()
        await session.refresh(agent)
        return agent


@router.delete("/{agent_id}", status_code=204)
async def delete_agent(
    agent_id: uuid.UUID,
    caller_org: Organization | None = Depends(verify_api_key_or_org),
):
    async with async_session() as session:
        agent = await session.get(Agent, agent_id)
        if not agent:
            raise HTTPException(status_code=404, detail="Agent not found")
        if caller_org and agent.org_id != caller_org.id:
            raise HTTPException(status_code=403, detail="Access denied to this agent")
        await session.delete(agent)
        await session.commit()


@router.get("/{agent_id}/card")
async def get_agent_card(agent_id: uuid.UUID):
    async with async_session() as session:
        agent = await session.get(Agent, agent_id)
        if not agent:
            raise HTTPException(status_code=404, detail="Agent not found")
        card = AgentCard(
            name=agent.name,
            description=agent.description,
            url=agent.url,
            version=agent.version,
            skills=agent.skills,
        ).model_dump()

        # Add UCP checkout capability for paid agents
        if agent.price_per_task > 0:
            from agentgate.server.ucp_routes import UCP_VERSION

            card["ucp"] = {
                "version": UCP_VERSION,
                "capabilities": ["dev.ucp.shopping.checkout"],
                "price_per_task": agent.price_per_task,
                "currency": "USD",
                "checkout_url": "https://agentgate.sh/ucp/checkout",
            }

        return card


# Billing logic lives in agentgate.server.billing (atomic debit +
# double-entry ledger). Kept here only as a thin wrapper to minimise churn
# at call sites.

from agentgate.server.billing import TIER_FEE_PCT  # noqa: E402,F401  (re-export)


async def _process_billing(
    agent: Agent,
    payer_org: Organization | None,
    task_id_str: str | None,
):
    return await process_charge(agent, payer_org, task_id_str)


# Thin wrappers so existing call sites keep the `_` prefix (and tests
# patching `agentgate.server.routes._save_task_log` / `_fire_webhook`
# still resolve).

_fire_webhook = fire_webhook
_save_task_log = save_task_log


@router.post("/{agent_id}/task")
async def route_task(
    agent_id: uuid.UUID, task: dict, request: Request,
    background_tasks: BackgroundTasks,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme_optional),
):
    """Route an A2A task to the target agent (proxy).

    Looks up the agent's URL in the registry and forwards the task payload
    to {agent_url}/a2a. Returns the agent's response directly.
    If the agent has an api_key_hash, a Bearer token is required.
    If the agent has a webhook_url configured, a notification is sent in the background.
    """
    client_ip = request.client.host if request.client else "unknown"

    async with async_session() as session:
        agent = await session.get(Agent, agent_id)
        if not agent:
            raise HTTPException(status_code=404, detail="Agent not found")

        # Per-org rate limiting (if agent belongs to an org)
        if agent.org_id:
            org = await session.get(Organization, agent.org_id)
            if org:
                org_limiter = RateLimiter(rate=org.rate_limit, burst=org.rate_burst)
                org_key = f"org:{org.id}:{client_ip}"
                if not org_limiter.allow(org_key):
                    raise HTTPException(status_code=429, detail="Too many requests")
            else:
                if not task_limiter.allow(client_ip):
                    raise HTTPException(status_code=429, detail="Too many requests")
        else:
            if not task_limiter.allow(client_ip):
                raise HTTPException(status_code=429, detail="Too many requests")

    # Per-agent auth check
    if agent.api_key_hash:
        if not credentials or hash_api_key(credentials.credentials) != agent.api_key_hash:
            raise HTTPException(status_code=401, detail="Invalid or missing agent API key")

    # Resolve calling org for billing (if credentials provided and match an org)
    caller_org_for_billing = None
    if credentials:
        key_hash = hash_api_key(credentials.credentials)
        async with async_session() as session:
            result = await session.execute(
                select(Organization).where(
                    (Organization.api_key_hash == key_hash)
                    | (Organization.secondary_api_key_hash == key_hash)
                )
            )
            caller_org_for_billing = result.scalar_one_or_none()

    # Pre-check balance (fail fast with 402 before calling the agent)
    if agent.price_per_task > 0 and caller_org_for_billing:
        if caller_org_for_billing.balance < agent.price_per_task:
            raise HTTPException(
                status_code=402,
                detail=(
                    f"Insufficient balance: "
                    f"{caller_org_for_billing.balance:.4f} < "
                    f"{agent.price_per_task:.4f} "
                    f"(agent: {agent.name})"
                ),
            )

    agent_name = agent.name
    webhook_url = agent.webhook_url
    task_id_str = task.get("id")
    target_url = f"{agent.url.rstrip('/')}/a2a"
    logger.info("Routing task to %s (%s)", agent_name, target_url)

    # Run pre-task plugins
    pre_context = await plugin_manager.run_pre_hooks({
        "agent_id": str(agent_id),
        "agent_name": agent_name,
        "task": task,
        "client_ip": client_ip,
    })
    task = pre_context.get("task", task)

    with Timer() as t:
        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                resp = await client.post(target_url, json=task)
            except httpx.ConnectError:
                record_request(agent_name, t.elapsed_ms, error_type="connect_error")
                logger.error("Cannot reach %s at %s", agent_name, agent.url)
                background_tasks.add_task(
                    _save_task_log, agent_id, agent_name, client_ip,
                    task_id_str, "error", t.elapsed_ms, "connect_error",
                )
                raise HTTPException(
                    status_code=502,
                    detail=f"Cannot reach agent at {agent.url}",
                )
            except httpx.TimeoutException:
                record_request(agent_name, t.elapsed_ms, error_type="timeout")
                logger.error("Timeout reaching %s at %s", agent_name, agent.url)
                background_tasks.add_task(
                    _save_task_log, agent_id, agent_name, client_ip,
                    task_id_str, "error", t.elapsed_ms, "timeout",
                )
                raise HTTPException(
                    status_code=504,
                    detail=f"Agent at {agent.url} timed out",
                )

    if resp.status_code >= 400:
        record_request(agent_name, t.elapsed_ms, error_type=f"http_{resp.status_code}")
        logger.warning(
            "Agent %s returned %d in %.0fms", agent_name, resp.status_code, t.elapsed_ms,
        )
        background_tasks.add_task(
            _save_task_log, agent_id, agent_name, client_ip,
            task_id_str, "error", t.elapsed_ms, f"http_{resp.status_code}",
        )
        raise HTTPException(
            status_code=resp.status_code,
            detail=f"Agent returned error: {resp.text}",
        )

    record_request(agent_name, t.elapsed_ms)
    logger.info("Task routed to %s — %d in %.0fms", agent_name, resp.status_code, t.elapsed_ms)

    # Charge billing AFTER successful A2A response (no charge on failure)
    if agent.price_per_task > 0:
        charged, err = await _process_billing(
            agent, caller_org_for_billing, task_id_str,
        )
        if not charged:
            raise HTTPException(status_code=402, detail=err)

    # Save log in background
    background_tasks.add_task(
        _save_task_log, agent_id, agent_name, client_ip,
        task_id_str, "success", t.elapsed_ms,
    )

    if webhook_url:
        background_tasks.add_task(
            _fire_webhook,
            webhook_url,
            {
                "event": "task.completed",
                "agent_id": str(agent_id),
                "agent_name": agent_name,
                "task_id": task_id_str,
                "latency_ms": round(t.elapsed_ms, 1),
            },
        )

    # Run post-task plugins
    background_tasks.add_task(
        plugin_manager.run_post_hooks,
        {
            "agent_id": str(agent_id),
            "agent_name": agent_name,
            "task": task,
            "client_ip": client_ip,
            "status": "success",
            "latency_ms": round(t.elapsed_ms, 1),
            "response": resp.json(),
        },
    )

    result = resp.json()

    # Attach UCP metadata for paid agents
    if agent.price_per_task > 0:
        from agentgate.server.ucp_routes import UCP_VERSION

        result = {
            "result": result,
            "ucp": {
                "version": UCP_VERSION,
                "capability": "dev.ucp.shopping.checkout",
                "agent_id": str(agent_id),
                "price_per_task": agent.price_per_task,
                "currency": "USD",
            },
        }

    return result


@router.post("/{agent_id}/task/stream")
async def route_task_stream(
    agent_id: uuid.UUID, task: dict, request: Request,
    background_tasks: BackgroundTasks,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme_optional),
):
    """Route an A2A task and stream the response via Server-Sent Events.

    Returns an SSE stream with events:
      - status: task routing status updates
      - chunk: streamed response data from the agent
      - result: final complete result
      - error: error details if something fails
    """
    client_ip = request.client.host if request.client else "unknown"

    async with async_session() as session:
        agent = await session.get(Agent, agent_id)
        if not agent:
            raise HTTPException(status_code=404, detail="Agent not found")

        if agent.org_id:
            org = await session.get(Organization, agent.org_id)
            if org:
                org_limiter = RateLimiter(rate=org.rate_limit, burst=org.rate_burst)
                org_key = f"org:{org.id}:{client_ip}"
                if not org_limiter.allow(org_key):
                    raise HTTPException(status_code=429, detail="Too many requests")
            else:
                if not task_limiter.allow(client_ip):
                    raise HTTPException(status_code=429, detail="Too many requests")
        else:
            if not task_limiter.allow(client_ip):
                raise HTTPException(status_code=429, detail="Too many requests")

    if agent.api_key_hash:
        if not credentials or hash_api_key(credentials.credentials) != agent.api_key_hash:
            raise HTTPException(status_code=401, detail="Invalid or missing agent API key")

    # Resolve calling org for billing
    caller_org_for_billing = None
    if credentials:
        key_hash = hash_api_key(credentials.credentials)
        async with async_session() as session:
            result = await session.execute(
                select(Organization).where(
                    (Organization.api_key_hash == key_hash)
                    | (Organization.secondary_api_key_hash == key_hash)
                )
            )
            caller_org_for_billing = result.scalar_one_or_none()

    # Pre-check balance (fail fast with 402)
    if agent.price_per_task > 0 and caller_org_for_billing:
        if caller_org_for_billing.balance < agent.price_per_task:
            raise HTTPException(
                status_code=402,
                detail=(
                    f"Insufficient balance: "
                    f"{caller_org_for_billing.balance:.4f} < "
                    f"{agent.price_per_task:.4f} "
                    f"(agent: {agent.name})"
                ),
            )

    agent_name = agent.name
    webhook_url = agent.webhook_url
    task_id_str = task.get("id")
    target_url = f"{agent.url.rstrip('/')}/a2a"

    async def event_generator():
        yield {"event": "status", "data": f"Routing task to {agent_name}"}

        with Timer() as t:
            try:
                async with httpx.AsyncClient(timeout=30.0) as http_client:
                    async with http_client.stream(
                        "POST", target_url, json=task,
                    ) as resp:
                        if resp.status_code >= 400:
                            record_request(
                                agent_name, t.elapsed_ms,
                                error_type=f"http_{resp.status_code}",
                            )
                            yield {
                                "event": "error",
                                "data": f"Agent returned {resp.status_code}",
                            }
                            return

                        chunks = []
                        async for chunk in resp.aiter_text():
                            chunks.append(chunk)
                            yield {"event": "chunk", "data": chunk}

            except httpx.ConnectError:
                record_request(agent_name, t.elapsed_ms, error_type="connect_error")
                yield {"event": "error", "data": f"Cannot reach agent at {agent.url}"}
                return
            except httpx.TimeoutException:
                record_request(agent_name, t.elapsed_ms, error_type="timeout")
                yield {"event": "error", "data": f"Agent at {agent.url} timed out"}
                return

        record_request(agent_name, t.elapsed_ms)
        logger.info(
            "Task streamed to %s — %.0fms", agent_name, t.elapsed_ms,
        )

        # Charge billing AFTER successful stream
        if agent.price_per_task > 0:
            charged, err = await _process_billing(
                agent, caller_org_for_billing, task_id_str,
            )
            if not charged:
                yield {"event": "error", "data": err}
                return

        yield {
            "event": "result",
            "data": "".join(chunks),
        }

        # Save log (inline since we're in a generator)
        await _save_task_log(
            agent_id, agent_name, client_ip,
            task_id_str, "success", t.elapsed_ms,
        )

        if webhook_url:
            await _fire_webhook(
                webhook_url,
                {
                    "event": "task.completed",
                    "agent_id": str(agent_id),
                    "agent_name": agent_name,
                    "task_id": task_id_str,
                    "latency_ms": round(t.elapsed_ms, 1),
                },
            )

    return EventSourceResponse(event_generator())


@router.websocket("/{agent_id}/task/ws")
async def route_task_ws(websocket: WebSocket, agent_id: uuid.UUID):
    """WebSocket endpoint for bidirectional task routing.

    Client sends JSON messages with A2A task payloads.
    Server responds with JSON messages containing:
      {"type": "status", "data": "..."}
      {"type": "chunk", "data": "..."}
      {"type": "result", "data": {...}}
      {"type": "error", "data": "..."}

    Auth: send {"type": "auth", "token": "..."} as first message if agent requires auth.
    """
    await websocket.accept()
    client_ip = websocket.client.host if websocket.client else "unknown"

    # Resolve agent
    async with async_session() as session:
        agent = await session.get(Agent, agent_id)
        if not agent:
            await websocket.send_json({"type": "error", "data": "Agent not found"})
            await websocket.close(code=4004)
            return

    agent_name = agent.name
    webhook_url = agent.webhook_url
    target_url = f"{agent.url.rstrip('/')}/a2a"
    auth_token: str | None = None
    caller_org_for_billing: Organization | None = None

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                await websocket.send_json({"type": "error", "data": "Invalid JSON"})
                continue

            msg_type = msg.get("type", "task")

            # Handle auth message
            if msg_type == "auth":
                auth_token = msg.get("token")
                # Resolve calling org for billing
                if auth_token:
                    key_hash = hash_api_key(auth_token)
                    async with async_session() as session:
                        result = await session.execute(
                            select(Organization).where(
                    (Organization.api_key_hash == key_hash)
                    | (Organization.secondary_api_key_hash == key_hash)
                )
                        )
                        caller_org_for_billing = result.scalar_one_or_none()
                await websocket.send_json({"type": "status", "data": "Authenticated"})
                continue

            # For task messages, validate auth if required
            if agent.api_key_hash:
                if not auth_token or hash_api_key(auth_token) != agent.api_key_hash:
                    await websocket.send_json({
                        "type": "error", "data": "Invalid or missing agent API key",
                    })
                    continue

            # Rate limiting
            if not task_limiter.allow(client_ip):
                await websocket.send_json({"type": "error", "data": "Too many requests"})
                continue

            # Extract task payload
            task = msg.get("task", msg)
            task_id_str = task.get("id")

            # Pre-check balance (fail fast with 402)
            if agent.price_per_task > 0 and caller_org_for_billing:
                if caller_org_for_billing.balance < agent.price_per_task:
                    await websocket.send_json({
                        "type": "error",
                        "data": (
                            f"Insufficient balance: "
                            f"{caller_org_for_billing.balance:.4f} < "
                            f"{agent.price_per_task:.4f} "
                            f"(agent: {agent.name})"
                        ),
                    })
                    continue

            await websocket.send_json({
                "type": "status", "data": f"Routing task to {agent_name}",
            })

            # Run pre-task plugins
            try:
                pre_context = await plugin_manager.run_pre_hooks({
                    "agent_id": str(agent_id),
                    "agent_name": agent_name,
                    "task": task,
                    "client_ip": client_ip,
                })
                task = pre_context.get("task", task)
            except Exception as e:
                await websocket.send_json({"type": "error", "data": str(e)})
                continue

            # Route the task
            with Timer() as t:
                try:
                    async with httpx.AsyncClient(timeout=30.0) as client:
                        resp = await client.post(target_url, json=task)
                except httpx.ConnectError:
                    record_request(agent_name, t.elapsed_ms, error_type="connect_error")
                    await _save_task_log(
                        agent_id, agent_name, client_ip,
                        task_id_str, "error", t.elapsed_ms, "connect_error",
                    )
                    await websocket.send_json({
                        "type": "error", "data": f"Cannot reach agent at {agent.url}",
                    })
                    continue
                except httpx.TimeoutException:
                    record_request(agent_name, t.elapsed_ms, error_type="timeout")
                    await _save_task_log(
                        agent_id, agent_name, client_ip,
                        task_id_str, "error", t.elapsed_ms, "timeout",
                    )
                    await websocket.send_json({
                        "type": "error", "data": f"Agent at {agent.url} timed out",
                    })
                    continue

            if resp.status_code >= 400:
                record_request(agent_name, t.elapsed_ms, error_type=f"http_{resp.status_code}")
                await _save_task_log(
                    agent_id, agent_name, client_ip,
                    task_id_str, "error", t.elapsed_ms, f"http_{resp.status_code}",
                )
                await websocket.send_json({
                    "type": "error", "data": f"Agent returned {resp.status_code}",
                })
                continue

            record_request(agent_name, t.elapsed_ms)
            result_data = resp.json()

            # Charge billing AFTER successful response
            if agent.price_per_task > 0:
                charged, err = await _process_billing(
                    agent, caller_org_for_billing, task_id_str,
                )
                if not charged:
                    await websocket.send_json({"type": "error", "data": err})
                    continue
                # Refresh balance for next task in same WS session
                if caller_org_for_billing:
                    async with async_session() as session:
                        refreshed = await session.get(Organization, caller_org_for_billing.id)
                        if refreshed:
                            caller_org_for_billing = refreshed

            await websocket.send_json({"type": "result", "data": result_data})

            # Background: save log, fire webhook, run post-hooks
            await _save_task_log(
                agent_id, agent_name, client_ip,
                task_id_str, "success", t.elapsed_ms,
            )
            if webhook_url:
                asyncio.create_task(_fire_webhook(
                    webhook_url,
                    {
                        "event": "task.completed",
                        "agent_id": str(agent_id),
                        "agent_name": agent_name,
                        "task_id": task_id_str,
                        "latency_ms": round(t.elapsed_ms, 1),
                    },
                ))
            asyncio.create_task(plugin_manager.run_post_hooks({
                "agent_id": str(agent_id),
                "agent_name": agent_name,
                "task": task,
                "client_ip": client_ip,
                "status": "success",
                "latency_ms": round(t.elapsed_ms, 1),
                "response": result_data,
            }))

    except WebSocketDisconnect:
        logger.info("WebSocket client disconnected from agent %s", agent_name)
