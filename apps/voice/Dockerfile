# The voice service (spec 0029): POST /calls for the core, and the Twilio routes in live mode.
# Build from the repo root:  docker build -f apps/voice/Dockerfile .
# It needs a public domain (Twilio's webhooks and the ConversationRelay WebSocket) and reaches the
# database the core migrates; it runs no migrations itself.
FROM python:3.13-slim-bookworm AS build
COPY --from=ghcr.io/astral-sh/uv:0.11.32 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never UV_PROJECT_ENVIRONMENT=/app/.venv
WORKDIR /src
COPY . .
# Only the voice service and the packages it imports (the call policy), no dev tools.
RUN uv sync --frozen --no-dev --no-editable --package dialback-voice

FROM python:3.13-slim-bookworm
RUN useradd --create-home --uid 10001 dialback
WORKDIR /app
COPY --from=build /app/.venv /app/.venv
# An empty HOST binds every address family the host has, as the core does: IPv4, and IPv6 where it
# exists (Railway's private network, which the core calls it over).
ENV PATH=/app/.venv/bin:$PATH PYTHONUNBUFFERED=1 HOST="" PORT=8080
USER dialback
EXPOSE 8080
CMD ["dialback-voice"]
