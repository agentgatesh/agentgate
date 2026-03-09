from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.security import HTTPAuthorizationCredentials
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select
from starlette.exceptions import HTTPException as StarletteHTTPException

from agentgate import __version__
from agentgate.core.config import settings
from agentgate.db.engine import async_session
from agentgate.db.models import Agent
from agentgate.server.account_routes import router as account_router
from agentgate.server.admin_routes import router as admin_router
from agentgate.server.auth import bearer_scheme_optional as bearer_scheme
from agentgate.server.auth_routes import router as auth_router
from agentgate.server.chain_routes import router as chains_router
from agentgate.server.deploy_routes import router as deploy_router
from agentgate.server.healthcheck import get_all_health, health_check_loop
from agentgate.server.log_retention import log_retention_loop
from agentgate.server.metrics import get_metrics
from agentgate.server.org_routes import router as orgs_router
from agentgate.server.routes import router as agents_router
from agentgate.server.stripe_routes import router as stripe_router
from agentgate.server.ucp_routes import router as ucp_router

STATIC_DIR = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    import asyncio

    from agentgate.server.plugins import plugin_manager

    # Load plugins from config file if configured
    if settings.plugin_config:
        loaded = plugin_manager.load_from_config(settings.plugin_config)
        if loaded:
            import logging

            logging.getLogger("agentgate").info(
                "Loaded %d plugins from %s", loaded, settings.plugin_config,
            )

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
app.include_router(chains_router)
app.include_router(agents_router, prefix="/v1")
app.include_router(orgs_router, prefix="/v1")
app.include_router(chains_router, prefix="/v1")
app.include_router(ucp_router)
app.include_router(ucp_router, prefix="/v1")
app.include_router(deploy_router)
app.include_router(deploy_router, prefix="/v1")
app.include_router(admin_router)
app.include_router(auth_router)
app.include_router(account_router)
app.include_router(stripe_router)

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.exception_handler(StarletteHTTPException)
async def custom_http_exception_handler(request: Request, exc: StarletteHTTPException):
    if exc.status_code == 404:
        accept = request.headers.get("accept", "")
        if "text/html" in accept:
            html = (STATIC_DIR / "404.html").read_text()
            return HTMLResponse(content=html, status_code=404)
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail},
    )


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


@app.get("/signup", response_class=HTMLResponse)
async def signup_page():
    return (STATIC_DIR / "signup.html").read_text()


@app.get("/billing", response_class=HTMLResponse)
async def billing_page():
    return (STATIC_DIR / "billing.html").read_text()


@app.get("/login", response_class=HTMLResponse)
async def login_page():
    return (STATIC_DIR / "login.html").read_text()


@app.get("/account", response_class=HTMLResponse)
async def account_page(request: Request):
    from agentgate.server.auth_routes import get_current_user

    user = await get_current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=302)
    return (STATIC_DIR / "account.html").read_text()


@app.get("/pricing", response_class=HTMLResponse)
async def pricing_page():
    return (STATIC_DIR / "pricing.html").read_text()


@app.get("/ratelimits", response_class=HTMLResponse)
async def ratelimits_page():
    return (STATIC_DIR / "ratelimits.html").read_text()


@app.get("/terms", response_class=HTMLResponse)
async def terms_page():
    return (STATIC_DIR / "terms.html").read_text()


@app.get("/privacy", response_class=HTMLResponse)
async def privacy_page():
    return (STATIC_DIR / "privacy.html").read_text()


@app.get("/refund", response_class=HTMLResponse)
async def refund_page():
    return (STATIC_DIR / "refund.html").read_text()


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


@app.get("/ratelimits/data")
@app.get("/v1/ratelimits/data")
async def ratelimits_data(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
):
    """Get rate limit config and current state for all orgs."""
    if settings.api_key:
        if not credentials or credentials.credentials != settings.api_key:
            raise HTTPException(status_code=401, detail="Invalid or missing API key")

    from agentgate.db.models import Organization

    async with async_session() as session:
        result = await session.execute(select(Organization).order_by(Organization.name))
        orgs = result.scalars().all()

    from agentgate.server.ratelimit import task_limiter

    return {
        "global": {
            "rate": task_limiter.rate,
            "burst": task_limiter.burst,
        },
        "organizations": [
            {
                "id": str(o.id),
                "name": o.name,
                "rate_limit": o.rate_limit,
                "rate_burst": o.rate_burst,
                "cost_per_invocation": o.cost_per_invocation,
            }
            for o in orgs
        ],
    }


@app.get("/plugins/info")
@app.get("/v1/plugins/info")
async def plugins_info(credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme)):
    """Get info about loaded plugins."""
    if settings.api_key:
        if not credentials or credentials.credentials != settings.api_key:
            raise HTTPException(status_code=401, detail="Invalid or missing API key")

    from agentgate.server.plugins import plugin_manager

    return {
        "plugins": plugin_manager.plugin_info,
        "total": len(plugin_manager.plugin_info),
    }


@app.get("/.well-known/ucp")
async def well_known_ucp():
    from agentgate.server.ucp_routes import get_ucp_profile

    return get_ucp_profile()


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
