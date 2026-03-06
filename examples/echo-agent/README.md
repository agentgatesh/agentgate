# Echo Agent

A minimal A2A-compatible agent for testing AgentGate.

## Run

```bash
pip install fastapi uvicorn
uvicorn agent:app --port 9000
```

## Test

```bash
curl -X POST http://localhost:9000/a2a \
  -H "Content-Type: application/json" \
  -d '{"id": "test-1", "message": {"parts": [{"type": "text", "text": "Hello!"}]}}'
```

## Register on AgentGate

```bash
agentgate deploy ./examples/echo-agent --api-key YOUR_KEY
```
