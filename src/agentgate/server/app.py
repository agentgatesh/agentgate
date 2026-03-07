from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select

from agentgate import __version__
from agentgate.core.config import settings
from agentgate.db.engine import async_session
from agentgate.db.models import Agent
from agentgate.server.healthcheck import get_all_health, health_check_loop
from agentgate.server.log_retention import log_retention_loop
from agentgate.server.metrics import get_metrics
from agentgate.server.org_routes import router as orgs_router
from agentgate.server.routes import router as agents_router

bearer_scheme = HTTPBearer(auto_error=False)

STATIC_DIR = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    import asyncio

    health_task = asyncio.create_task(health_check_loop())
    retention_task = asyncio.create_task(log_retention_loop())
    yield
    health_task.cancel()
    retention_task.cancel()


app = FastAPI(
    title="AgentGate",
    description="The unified gateway to deploy, connect, and monetize AI agents.",
    version=__version__,
    lifespan=lifespan,
)

# Mount routers at both / (backward compat) and /v1/ (versioned API)
app.include_router(agents_router)
app.include_router(orgs_router)
app.include_router(agents_router, prefix="/v1")
app.include_router(orgs_router, prefix="/v1")


@app.get("/", response_class=HTMLResponse)
async def landing_page():
    return (STATIC_DIR / "index.html").read_text()


@app.get("/health")
@app.get("/v1/health")
async def health():
    return {"status": "ok", "version": __version__}


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard():
    return (STATIC_DIR / "dashboard.html").read_text()


@app.get("/admin", response_class=HTMLResponse)
async def admin_page():
    return (STATIC_DIR / "admin.html").read_text()


@app.get("/guide", response_class=HTMLResponse)
async def guide_page():
    return (STATIC_DIR / "guide.html").read_text()


@app.get("/marketplace", response_class=HTMLResponse)
async def marketplace_page():
    return (STATIC_DIR / "marketplace.html").read_text()


@app.get("/health/agents")
@app.get("/v1/health/agents")
async def agents_health():
    return get_all_health()


@app.get("/metrics")
@app.get("/v1/metrics")
async def metrics(credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme)):
    if settings.api_key:
        if not credentials or credentials.credentials != settings.api_key:
            raise HTTPException(status_code=401, detail="Invalid or missing API key")
    return get_metrics()


@app.get("/.well-known/agent.json")
async def well_known_agent():
    async with async_session() as session:
        result = await session.execute(select(Agent).order_by(Agent.created_at.desc()))
        agents = result.scalars().all()
    return {
        "name": "AgentGate",
        "description": "The unified gateway to deploy, connect, and monetize AI agents.",
        "url": "https://agentgate.sh",
        "version": __version__,
        "capabilities": {},
        "authentication": {"schemes": []},
        "provider": {"organization": "AgentGate", "url": "https://agentgate.sh"},
        "agents": [
            {
                "name": a.name,
                "description": a.description,
                "url": a.url,
                "version": a.version,
                "skills": a.skills,
            }
            for a in agents
        ],
    }
