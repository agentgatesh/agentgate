"""Echo Agent — A minimal A2A-compatible agent for demonstration.

Run with: uvicorn agent:app --port 9000
"""

from fastapi import FastAPI

app = FastAPI(title="Echo Agent")


@app.post("/a2a")
async def handle_task(request: dict):
    """A2A task handler — echoes back the user's message."""
    message = request.get("message", {})
    parts = message.get("parts", [])
    text = parts[0].get("text", "") if parts else ""

    return {
        "id": request.get("id", "task-1"),
        "status": {"state": "completed"},
        "artifacts": [
            {
                "parts": [{"type": "text", "text": f"Echo: {text}"}],
            }
        ],
    }


@app.get("/health")
async def health():
    return {"status": "ok", "agent": "echo-agent", "version": "1.0.0"}
