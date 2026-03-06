from fastapi import FastAPI

from agentgate import __version__

app = FastAPI(
    title="AgentGate",
    description="The unified gateway to deploy, connect, and monetize AI agents.",
    version=__version__,
)


@app.get("/health")
async def health():
    return {"status": "ok", "version": __version__}
