# The Ramp-shaped mock (design, "Ramp adapter"): same paths and field names as Ramp's API,
# with signed webhooks to the core. Build from the repo root:
#   docker build -f apps/ramp-mock/Dockerfile .
FROM python:3.13-slim-bookworm AS build
COPY --from=ghcr.io/astral-sh/uv:0.11.32 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never UV_PROJECT_ENVIRONMENT=/app/.venv
WORKDIR /src
COPY . .
RUN uv sync --frozen --no-dev --no-editable --package dialback-ramp-mock

FROM python:3.13-slim-bookworm
RUN useradd --create-home --uid 10001 dialback
WORKDIR /app
COPY --from=build /app/.venv /app/.venv
# An empty HOST binds every address family the host has: IPv4, and IPv6 where it exists (Railway's
# private network). "::" alone would be IPv6-only and fail to start on a host without IPv6.
ENV PATH=/app/.venv/bin:$PATH PYTHONUNBUFFERED=1 HOST="" PORT=8003
USER dialback
EXPOSE 8003
CMD ["dialback-ramp-mock"]
