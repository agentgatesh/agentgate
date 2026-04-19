from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.security import HTTPAuthorizationCredentials
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.base import BaseHTTPMiddleware

from agentgate import __version__
from agentgate.core.config import settings
from agentgate.db.engine import async_session
from agentgate.db.models import Agent
from agentgate.server.account_routes import router as account_router
from agentgate.server.admin_routes import router as admin_router
from agentgate.server.auth import bearer_scheme_optional as bearer_scheme
from agentgate.server.auth import is_admin_key
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
TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


# ---------------------------------------------------------------------------
# Security headers middleware
# ---------------------------------------------------------------------------


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        if not settings.debug:
            response.headers["Strict-Transport-Security"] = (
                "max-age=31536000; includeSubDomains"
            )
        return response


class DeprecationHeaderMiddleware(BaseHTTPMiddleware):
    """Mark legacy non-versioned API paths as deprecated.

    Adds RFC-8594-style headers on responses for paths that have a /v1/
    twin so SDK / CLI clients can surface the upgrade path without us
    having to break existing integrations with a 308.
    """

    LEGACY_PREFIXES = ("/agents", "/orgs", "/chains", "/ucp", "/deploy")
    SUNSET = "Sat, 31 Jan 2026 23:59:59 GMT"

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        path = request.url.path
        if path.startswith(self.LEGACY_PREFIXES) and not path.startswith("/v1/"):
            response.headers["Deprecation"] = "true"
            response.headers["Sunset"] = self.SUNSET
            response.headers["Link"] = f'</v1{path}>; rel="successor-version"'
        return response


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

# Security headers on every response
app.add_middleware(SecurityHeadersMiddleware)

# Mark legacy non-/v1 API responses as deprecated (no redirect, header-only)
app.add_middleware(DeprecationHeaderMiddleware)

# CORS — allow SDK clients and third-party integrations
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.base_url],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
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
            return templates.TemplateResponse(
                request, "404.html", status_code=404,
            )
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail},
    )


@app.get("/robots.txt", response_class=PlainTextResponse)
async def robots_txt():
    return (
        "User-agent: *\n"
        "Allow: /\n"
        "Disallow: /admin\n"
        "Disallow: /account\n"
        "Disallow: /auth/\n"
        "Disallow: /ratelimits\n"
        "Disallow: /v1/\n"
        "\n"
        "Sitemap: https://agentgate.sh/sitemap.xml\n"
    )


@app.get("/sitemap.xml", response_class=PlainTextResponse)
async def sitemap_xml():
    urls = [
        ("https://agentgate.sh/", "1.0", "weekly"),
        ("https://agentgate.sh/marketplace", "0.9", "daily"),
        ("https://agentgate.sh/pricing", "0.8", "monthly"),
        ("https://agentgate.sh/guide", "0.8", "weekly"),
        ("https://agentgate.sh/signup", "0.7", "monthly"),
        ("https://agentgate.sh/login", "0.5", "monthly"),
        ("https://agentgate.sh/terms", "0.3", "yearly"),
        ("https://agentgate.sh/privacy", "0.3", "yearly"),
        ("https://agentgate.sh/refund", "0.3", "yearly"),
    ]
    entries = "\n".join(
        f"  <url><loc>{u}</loc><priority>{p}</priority><changefreq>{f}</changefreq></url>"
        for u, p, f in urls
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        f"{entries}\n"
        "</urlset>\n"
    )


_PAGES = [
    "index", "marketplace", "signup", "login", "pricing",
    "ratelimits", "terms", "privacy", "refund", "guide", "admin",
]


def _page_route(template_name: str, path: str):
    @app.get(path, response_class=HTMLResponse)
    async def _page(request: Request):
        return templates.TemplateResponse(request, template_name)
    _page.__name__ = f"page_{template_name}"
    return _page


# Landing
_page_route("index.html", "/")
# Top-level pages
for _p in _PAGES[1:]:  # skip "index" already mounted at "/"
    _page_route(f"{_p}.html", f"/{_p}")


@app.get("/health")
@app.get("/v1/health")
async def health():
    return {"status": "ok", "version": __version__}


@app.get("/dashboard")
async def dashboard():
    return RedirectResponse("/account", status_code=302)


@app.get("/billing")
async def billing_page():
    return RedirectResponse("/account", status_code=302)


@app.get("/account", response_class=HTMLResponse)
async def account_page(request: Request):
    from agentgate.server.auth_routes import get_current_user

    user = await get_current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=302)
    return templates.TemplateResponse(request, "account.html")


@app.get("/health/agents")
@app.get("/v1/health/agents")
async def agents_health():
    return get_all_health()


@app.get("/metrics")
@app.get("/v1/metrics")
async def metrics(credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme)):
    if settings.api_key:
        if not credentials or not is_admin_key(credentials.credentials, settings.api_key):
            raise HTTPException(status_code=401, detail="Invalid or missing API key")
    return get_metrics()


@app.get("/ratelimits/data")
@app.get("/v1/ratelimits/data")
async def ratelimits_data(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
):
    """Get rate limit config and current state for all orgs."""
    if settings.api_key:
        if not credentials or not is_admin_key(credentials.credentials, settings.api_key):
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
        if not credentials or not is_admin_key(credentials.credentials, settings.api_key):
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
