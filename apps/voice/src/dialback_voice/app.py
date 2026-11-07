"""The voice service's FastAPI app. For spec 0012 it serves POST /calls (internal.yaml,
operation placeCall): the core worker asks the service to call a case's chosen number.

The request carries only {case_id, attempt}. The service reads the number itself from the
database, so nothing in the request can change who gets called (R2). Before dialing, code
confirms the call is allowed (`guards.pre_dial_refusal`); a failing check is a 409 PreDialRefusal
and no call is placed, so the core sends the case to a person (R7). The WebSocket transport and
the Twilio adapter are later specs; this app only accepts the call and records it."""
from __future__ import annotations

import hmac
import logging
from typing import Annotated
from uuid import UUID

from fastapi import FastAPI, Header, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from .guards import pre_dial_refusal
from .store import CallStore

log = logging.getLogger(__name__)

# A short, human-readable reason per PreDialRefusal code. It names the check, never case data.
REFUSAL_MESSAGE = {
    "case_not_held": "The case is not held, so no call is due.",
    "case_finished": "The case is already finished.",
    "number_not_selected": "The Trust Ledger has not selected a number to call.",
    "number_opted_out": "The chosen number has opted out of calls.",
    "outside_calling_window": "It is outside the vendor's calling window.",
    "attempt_cap_reached": "The case has used all of its call attempts.",
    "number_cap_reached": "The number has reached its call cap for the last 30 days.",
    "not_on_dial_allowlist": "The number is not on the demo dial allowlist.",
}


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PlaceCallRequest(Strict):
    case_id: UUID
    attempt: int = Field(ge=1, le=3)  # the case allows three attempts (internal.yaml)


class PlaceCallAccepted(Strict):
    call_id: UUID
    case_id: UUID
    attempt: int


def _error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse({"code": code, "message": message}, status_code=status)


def create_app(*, store: CallStore, internal_api_token: bytes, agent_version: str) -> FastAPI:
    app = FastAPI(title="Dialback voice")
    app.state.store = store
    app.state.internal_api_token = internal_api_token
    app.state.agent_version = agent_version

    @app.exception_handler(RequestValidationError)
    async def invalid_request(_: Request, exc: RequestValidationError):
        # The Error shape is {code, message}; input values are not echoed.
        problems = "; ".join(f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors())
        return JSONResponse({"code": "invalid_request", "message": problems}, status_code=422)

    @app.post("/calls")
    async def place_call(body: PlaceCallRequest, request: Request,
                         idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=200)]):
        token = request.headers.get("authorization", "").removeprefix("Bearer ").strip().encode()
        expected: bytes = request.app.state.internal_api_token
        if not expected or not hmac.compare_digest(token, expected):
            return _error(401, "unauthorized", "A valid service token is required.")

        store: CallStore = request.app.state.store
        existing = await store.find_call(idempotency_key)
        if existing is not None:  # a replay returns the first call, unchanged (internal.yaml)
            return JSONResponse(PlaceCallAccepted(**existing.__dict__).model_dump(mode="json"), status_code=200)

        pre = await store.gather_pre_dial(case_id=body.case_id, attempt=body.attempt)
        code = pre_dial_refusal(pre.inputs)
        if code is not None:
            return _error(409, code, REFUSAL_MESSAGE[code])

        placed, created = await store.record_call(
            case_id=body.case_id, attempt=body.attempt, idempotency_key=idempotency_key,
            contact_point_id=pre.contact_point_id, number_e164=pre.number_e164,
            agent_version=request.app.state.agent_version)
        log.info("call %s accepted for case %s attempt %s", placed.call_id, placed.case_id, placed.attempt)
        return JSONResponse(PlaceCallAccepted(**placed.__dict__).model_dump(mode="json"),
                            status_code=202 if created else 200)

    @app.get("/healthz")
    async def healthz():
        return {"ok": True}

    return app
