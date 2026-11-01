"""The core service's FastAPI app. Routes are added per branch of the walking skeleton:
webhooks here; the call report and the dashboard API in later branches."""
from __future__ import annotations

import contextlib

import httpx
from fastapi import FastAPI
from psycopg_pool import AsyncConnectionPool

from .db import open_pool
from .detection import Keys
from .ramp import RampConnector
from .settings import Settings
from .webhooks import router as webhooks_router


def create_app(settings: Settings, pool: AsyncConnectionPool | None = None,
               connector: RampConnector | None = None) -> FastAPI:
    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI):
        owned_http: httpx.AsyncClient | None = None
        if pool is None:
            app.state.pool = await open_pool(settings.database_url)
        if connector is None:
            owned_http = httpx.AsyncClient(timeout=10.0)
            app.state.connector = RampConnector(settings, owned_http)
        try:
            yield
        finally:
            if owned_http is not None:
                await owned_http.aclose()
            if pool is None:
                await app.state.pool.close()

    app = FastAPI(title="Dialback core", lifespan=lifespan)
    app.state.settings = settings
    app.state.keys = Keys(settings.fingerprint_key, settings.fingerprint_key_version)
    if pool is not None:
        app.state.pool = pool
    if connector is not None:
        app.state.connector = connector
    app.include_router(webhooks_router)

    @app.get("/healthz")
    async def healthz():
        return {"ok": True}

    return app
