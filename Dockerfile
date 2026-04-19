FROM python:3.12-slim

WORKDIR /app

# Install curl for the disposable-domains fetch below, then trim.
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

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

# Fetch the disposable-email-domain lists from four public sources at
# build time, de-dup + lowercase, and bake the result into the image.
# Missing sources are tolerated (|| true); if the final file ends up
# shorter than a sanity threshold we fall back to the small hardcoded
# list in disposable.py.
COPY scripts/build-disposable-list.sh /usr/local/bin/build-disposable-list.sh
RUN chmod +x /usr/local/bin/build-disposable-list.sh \
    && mkdir -p /app/data \
    && /usr/local/bin/build-disposable-list.sh /app/data/disposable-domains.txt || true

RUN useradd -m -u 1000 agentgate && chown -R agentgate:agentgate /app
USER agentgate

EXPOSE 8000

CMD ["./entrypoint.sh"]
