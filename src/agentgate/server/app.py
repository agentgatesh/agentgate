from fastapi import FastAPI
from sqlalchemy import select

from agentgate import __version__
from agentgate.db.engine import async_session
from agentgate.db.models import Agent
from agentgate.server.routes import router as agents_router

app = FastAPI(
    title="AgentGate",
    description="The unified gateway to deploy, connect, and monetize AI agents.",
    version=__version__,
)

app.include_router(agents_router)


@app.get("/health")
async def health():
    return {"status": "ok", "version": __version__}


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
