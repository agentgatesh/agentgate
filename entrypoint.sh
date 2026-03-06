#!/bin/sh
set -e
uv run alembic upgrade head
exec uv run uvicorn agentgate.server.app:app --host 0.0.0.0 --port 8000
