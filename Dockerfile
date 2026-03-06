FROM python:3.12-slim

WORKDIR /app

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Copy dependency files first (better caching)
COPY pyproject.toml uv.lock ./

# Install dependencies
RUN uv sync --frozen --no-dev --no-install-project

# Copy source code and README (required by hatchling build)
COPY README.md ./
COPY src/ src/
COPY alembic.ini entrypoint.sh ./

# Install the project itself
RUN uv sync --frozen --no-dev

EXPOSE 8000

CMD ["./entrypoint.sh"]
