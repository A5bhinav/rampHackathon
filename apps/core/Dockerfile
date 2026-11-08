# The core service: the API and the worker in one always-on container (design ADR-2).
# Build from the repo root:  docker build -f apps/core/Dockerfile .
# Railway runs `python scripts/db/migrate.py` from this image before each deploy.
FROM python:3.13-slim-bookworm AS build
COPY --from=ghcr.io/astral-sh/uv:0.11.32 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never UV_PROJECT_ENVIRONMENT=/app/.venv
WORKDIR /src
COPY . .
# Only the core and the packages it imports (the decision engine, the ledger), no dev tools.
RUN uv sync --frozen --no-dev --no-editable --package dialback-core

FROM python:3.13-slim-bookworm
RUN useradd --create-home --uid 10001 dialback
WORKDIR /app
COPY --from=build /app/.venv /app/.venv
COPY scripts/db/migrate.py scripts/db/migrate.py
COPY supabase/migrations supabase/migrations
# The demo seed, so `dialback-core demo reset` works from the Railway shell (spec 0011).
COPY scripts/demo scripts/demo
# An empty HOST binds every address family the host has: IPv4, and IPv6 where it exists (Railway's
# private network). "::" alone would be IPv6-only and fail to start on a host without IPv6.
ENV PATH=/app/.venv/bin:$PATH PYTHONUNBUFFERED=1 HOST="" PORT=8001
USER dialback
EXPOSE 8001
CMD ["dialback-core", "serve"]
