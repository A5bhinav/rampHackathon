"""The core service's FastAPI app: Ramp webhooks, the voice service's call reports, and (in
the next branch) the dashboard API. One always-on container also runs the worker."""
from __future__ import annotations

import asyncio
import contextlib

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from psycopg_pool import AsyncConnectionPool

from .api import router as api_router
from .auth import TokenVerifier, verifier_from_settings
from .calls import router as calls_router
from .db import open_pool
from .detection import Keys
from .email_door.resend import ResendClient
from .email_door.route import router as email_router
from .flags import EmailMode
from .ports import (
    CallPlacer,
    ChangeContext,
    LedgerResolver,
    NoVoiceService,
    NumberResolver,
    VoiceServiceClient,
)
from .ramp import RampConnector
from .settings import Settings
from .webhooks import router as webhooks_router
from .worker import Worker


def create_app(settings: Settings, pool: AsyncConnectionPool | None = None, connector: RampConnector | None = None,
               resolver: NumberResolver | None = None, caller: CallPlacer | None = None,
               change_context: ChangeContext | None = None, verifier: TokenVerifier | None = None) -> FastAPI:
    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI):
        http = httpx.AsyncClient(timeout=10.0)
        if pool is None:
            app.state.pool = await open_pool(settings.database_url)
        if connector is None:
            app.state.connector = RampConnector(settings, http)
        stop, task = asyncio.Event(), None
        if settings.run_worker:
            worker = Worker(app.state.pool, settings, app.state.connector,
                            resolver or LedgerResolver(settings.resolver_mode),
                            caller or (VoiceServiceClient(settings.voice_base_url, settings.internal_api_token, http)
                                       if settings.voice_base_url else NoVoiceService()),
                            change_context=change_context,
                            resend=ResendClient(settings.resend_api_key, http)
                            if settings.email_mode is EmailMode.RESEND and settings.resend_api_key else None)
            task = asyncio.create_task(worker.run_forever(stop))
        try:
            yield
        finally:
            stop.set()
            if task:
                await task
            await http.aclose()
            if pool is None:
                await app.state.pool.close()

    app = FastAPI(title="Dialback core", lifespan=lifespan)
    app.state.settings = settings
    app.state.keys = Keys(settings.fingerprint_key, settings.fingerprint_key_version)
    app.state.verifier = verifier or verifier_from_settings(settings)
    if pool is not None:
        app.state.pool = pool
    if connector is not None:
        app.state.connector = connector

    @app.exception_handler(RequestValidationError)
    async def invalid_request(_: Request, exc: RequestValidationError):
        # Never echo input values back: a call report carries spoken digits.
        problems = "; ".join(f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors())
        return JSONResponse({"code": "invalid_request", "message": problems}, status_code=422)

    @app.exception_handler(HTTPException)
    async def http_problem(_: Request, exc: HTTPException):
        # The dashboard contract's Error shape: {code, message}.
        if isinstance(exc.detail, dict) and {"code", "message"} <= exc.detail.keys():
            return JSONResponse(exc.detail, status_code=exc.status_code, headers=exc.headers)
        code = {401: "unauthenticated", 403: "forbidden", 404: "not_found", 409: "transition_refused"}.get(exc.status_code, "invalid_request")
        return JSONResponse({"code": code, "message": str(exc.detail)}, status_code=exc.status_code, headers=exc.headers)

    app.include_router(webhooks_router)
    app.include_router(email_router)
    app.include_router(calls_router)
    app.include_router(api_router)

    @app.get("/healthz")
    async def healthz():
        return {"ok": True}

    return app
