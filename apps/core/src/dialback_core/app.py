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
from .email_door.classifier import AnthropicClassifier, ChangeClassifier, anthropic_client
from .email_door.resend import ResendClient
from .email_door.route import router as email_router
from .flags import ClassifierMode, EmailMode
from .ports import (
    CallPlacer,
    ChangeContext,
    LedgerResolver,
    NoVoiceService,
    NumberResolver,
    VoiceServiceClient,
)
from .ramp import RampConnector
from .settings import ConfigurationError, Settings
from .webhooks import router as webhooks_router
from .worker import Worker


def email_classifier(settings: Settings) -> ChangeClassifier | None:
    """The email door's model (spec 0086, Task 6b), only with DIALBACK_EMAIL_CLASSIFIER=model. Otherwise
    None: the rule alone flags, and no email text leaves Dialback."""
    if settings.email_classifier is not ClassifierMode.MODEL:
        return None
    if not settings.anthropic_api_key:   # Settings.from_env refuses this too
        raise ConfigurationError("ANTHROPIC_API_KEY is required when DIALBACK_EMAIL_CLASSIFIER=model")
    return AnthropicClassifier(anthropic_client(settings.anthropic_api_key))


def create_app(settings: Settings, pool: AsyncConnectionPool | None = None, connector: RampConnector | None = None,
               resolver: NumberResolver | None = None, caller: CallPlacer | None = None,
               change_context: ChangeContext | None = None, verifier: TokenVerifier | None = None) -> FastAPI:
    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI):
        http = httpx.AsyncClient(timeout=10.0)
        stop, task, classifier, opened_pool = asyncio.Event(), None, None, False
        try:   # whatever fails on the way up or down, everything opened here is closed
            if pool is None:
                app.state.pool = await open_pool(settings.database_url)
                opened_pool = True
            if connector is None:
                app.state.connector = RampConnector(settings, http)
            if settings.run_worker:
                classifier = email_classifier(settings)
                worker = Worker(app.state.pool, settings, app.state.connector,
                                resolver or LedgerResolver(settings.resolver_mode),
                                caller or (VoiceServiceClient(settings.voice_base_url, settings.internal_api_token, http)
                                           if settings.voice_base_url else NoVoiceService()),
                                change_context=change_context,
                                resend=ResendClient(settings.resend_api_key, http)
                                if settings.email_mode is EmailMode.RESEND and settings.resend_api_key else None,
                                classifier=classifier)
                task = asyncio.create_task(worker.run_forever(stop))
            yield
        finally:
            stop.set()
            try:
                if task:
                    await task
            finally:
                if isinstance(classifier, AnthropicClassifier):
                    classifier.client.close()
                await http.aclose()
                if opened_pool:
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
