from fastapi import FastAPI

from agentgate import __version__
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
