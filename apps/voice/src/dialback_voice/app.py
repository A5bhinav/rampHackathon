"""The voice service's FastAPI app. For spec 0012 it serves POST /calls (internal.yaml,
operation placeCall): the core worker asks the service to call a case's chosen number.

The request carries only {case_id, attempt}. The service reads the number itself from the
database, so nothing in the request can change who gets called (R2). Before dialing, code
confirms the call is allowed (`guards.pre_dial_refusal`); a failing check is a 409 PreDialRefusal
and no call is placed, so the core sends the case to a person (R7).

Spec 0013 adds the Twilio ConversationRelay routes, mounted only when `twilio` deps are given: the
TwiML endpoint Twilio fetches, the WebSocket the call runs over (run_relay), and the status callback
for calls that never reach a live conversation. They are off by default, so the text line is
unaffected (DIALBACK_VOICE=text)."""
from __future__ import annotations

import datetime as dt
import hmac
import logging
import urllib.parse
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Annotated, Any
from uuid import UUID

from dialback_policy.script import Script
from dialback_policy.step import CallContext
from fastapi import FastAPI, Header, Request, Response, WebSocket
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from .agent import Agent
from .guards import pre_dial_refusal
from .report import CoreRejectedReport, CoreUnavailable
from .store import CallDial, CallStore, PlacedCall
from .tokens import mint
from .transport import CallResult, Clock
from .twilio.origination import Originator, build_connect_twiml, status_result, valid_twilio_signature
from .twilio.relay import run_relay, verify_setup

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


@dataclass(frozen=True)
class TwilioDeps:
    """The Twilio-facing seams, injected so the routes run without a live account (spec 0013). The
    real wiring builds `report_ended` from CoreClient, `agent_for` from the live model, and
    `lookup_call` from the call store; the tests pass fakes."""
    call_token_key: bytes
    twilio_auth_token: bytes
    ws_base_url: str
    public_base_url: str
    from_number: str
    originator: Originator
    lookup_call: Callable[[UUID], CallDial | None]
    report_ended: Callable[[dict[str, Any]], Awaitable[None]]
    agent_for: Callable[[], Agent]
    clock: Clock


async def _verified_twilio_params(twilio: TwilioDeps, request: Request) -> dict[str, str] | None:
    """The request's parameters if its X-Twilio-Signature checks out, else None. The signed URL is
    this service's public URL (what Twilio fetched), rebuilt from the configured base so a proxy in
    front of us does not change what is signed."""
    url = f"{twilio.public_base_url}{request.url.path}"
    if request.method == "POST":
        raw = await request.body()
        params = {k: v[-1] for k, v in urllib.parse.parse_qs(raw.decode()).items()}
    else:
        params = dict(request.query_params)
    signature = request.headers.get("X-Twilio-Signature", "")
    return params if valid_twilio_signature(twilio.twilio_auth_token, signature, url, params) else None


def _report_body(result: CallResult, call_id: UUID, dial: CallDial) -> dict[str, Any]:
    """The CallEndedRequest body for a call that ran over the WebSocket (internal.yaml). Answers ride
    along only when the call recorded them; retry_at only when the result carries one (a requested
    callback, or a dropped call to redial)."""
    body: dict[str, Any] = {"call_id": str(call_id), "case_id": str(dial.case_id),
                            "result": result.result, "attempt": dial.attempt}
    if result.result == "answers_recorded":
        body["answers"] = result.answers
    if result.end.retry_at is not None:
        body["retry_at"] = result.end.retry_at.astimezone(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    return body


async def _dial(twilio: TwilioDeps, store: CallStore, placed: PlacedCall, number_e164: str | None) -> None:
    """Originate the Twilio call for a freshly placed call, then record the outcome (spec 0014).
    Fails closed (R7): a missing number or a Twilio rejection records the call `failed` and reports
    `failed` to the core, and POST /calls still returns the call_id with 202 even when that report or
    the bookkeeping write cannot land, so a dial outcome never 500s the placement; the failure rides
    the normal report path, with the core's `call_timeout` as the backstop (Q49/Q51). Only the pick,
    the caller id and our own URLs reach Twilio; never the case id."""
    call_id = placed.call_id
    if not number_e164:  # past pre_dial this should hold, but never dial a number we do not have (R2)
        await _dial_failed(twilio, store, placed, "no_number")
        return
    try:
        sid = await twilio.originator.originate(
            to=number_e164, from_=twilio.from_number,
            twiml_url=f"{twilio.public_base_url}/twilio/twiml/{call_id}",
            status_url=f"{twilio.public_base_url}/twilio/status/{call_id}")
    except Exception:  # noqa: BLE001  any REST error means no live call was placed: fail closed
        log.warning("origination failed for call %s", call_id)
        await _dial_failed(twilio, store, placed, "originate_failed")
        return
    try:
        await store.mark_dialed(call_id, provider_call_id=sid)
    except Exception:  # noqa: BLE001  the call is placed; a store blip recording the SID must not 500 it
        log.warning("could not record the dialed SID for call %s", call_id)


async def _dial_failed(twilio: TwilioDeps, store: CallStore, placed: PlacedCall, reason: str) -> None:
    """Record and report a call that never reached a live conversation, best effort. Neither the store
    write nor the core report may 500 the placement (R7, decision 3): on any failure the case stays
    `calling` and the core's `call_timeout` sweeps it to a person. In the synchronous-dial path the
    core is still awaiting this 202 and has not appended `call_started`, so the `failed` report 409s
    through all its retries and raises rather than landing; the timeout is the real backstop (Q49/Q51)."""
    try:
        await store.mark_failed(placed.call_id, reason)
    except Exception:  # noqa: BLE001  a store blip must not 500 the placement; the core timeout backstops it
        log.warning("could not mark call %s failed (%s)", placed.call_id, reason)
    try:
        await twilio.report_ended({"call_id": str(placed.call_id), "case_id": str(placed.case_id),
                                   "result": "failed", "attempt": placed.attempt})
    except (CoreUnavailable, CoreRejectedReport):
        log.warning("the failed report for call %s did not land; the core timeout will sweep it",
                    placed.call_id)


async def run_relay_call(twilio: TwilioDeps, call_id: UUID, ws: Any) -> None:
    """Run one ConversationRelay call on an already-accepted socket, then report its end. This is the
    whole body of the WebSocket route, apart from accepting the socket, so it is tested directly."""
    dial = twilio.lookup_call(call_id)
    if dial is None:
        await ws.close()                                     # an unknown call_id: nothing to run
        return
    ctx = CallContext(call_id=str(call_id), script=Script(dial.customer_name, dial.callback_number),
                      vendor_names=dial.vendor_names)

    def verify(setup: dict[str, Any]) -> bool:
        return verify_setup(setup, key=twilio.call_token_key, case_id=dial.case_id,
                            attempt=dial.attempt, number_e164=dial.number_e164)

    result = await run_relay(ctx, twilio.agent_for(), ws, verify=verify, clock=twilio.clock,
                             greeting=ctx.script.disclosure())  # welcomeGreeting spoke it; don't repeat (R8)
    await twilio.report_ended(_report_body(result, call_id, dial))


def create_app(*, store: CallStore, internal_api_token: bytes, agent_version: str,
               twilio: TwilioDeps | None = None) -> FastAPI:
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
        if twilio is not None and created:  # live mode dials the pick; a replay (created=False) never redials
            await _dial(twilio, store, placed, pre.number_e164)
        return JSONResponse(PlaceCallAccepted(**placed.__dict__).model_dump(mode="json"),
                            status_code=202 if created else 200)

    @app.get("/healthz")
    async def healthz():
        return {"ok": True}

    if twilio is not None:
        @app.api_route("/twilio/twiml/{call_id}", methods=["GET", "POST"])
        async def twiml(call_id: UUID, request: Request):
            # Twilio fetches a call's instructions here; the fetch is signature-verified so a forged
            # request mints no token (R9, and Q46 on whether the WS handshake is signed too).
            if await _verified_twilio_params(twilio, request) is None:
                return _error(401, "unauthorized", "A valid Twilio signature is required.")
            dial = twilio.lookup_call(call_id)
            if dial is None:
                return _error(404, "unknown_call", "No such call.")
            token = mint(twilio.call_token_key, case_id=dial.case_id, attempt=dial.attempt,
                         number_e164=dial.number_e164)
            disclosure = Script(dial.customer_name, dial.callback_number).disclosure()
            xml = build_connect_twiml(ws_url=f"{twilio.ws_base_url}/twilio/relay/{call_id}",
                                      call_token=token, disclosure=disclosure)
            return Response(content=xml, media_type="application/xml")

        @app.post("/twilio/status/{call_id}")
        async def call_status(call_id: UUID, request: Request):
            params = await _verified_twilio_params(twilio, request)
            if params is None:
                return _error(401, "unauthorized", "A valid Twilio signature is required.")
            mapped = status_result(params.get("CallStatus", ""), params.get("AnsweredBy"))
            if mapped is None:
                return Response(status_code=204)  # a human answered: the WebSocket owns that report
            result, answered_by = mapped
            dial = twilio.lookup_call(call_id)
            if dial is None:
                return _error(409, "not_current_call", "No such call.")
            body: dict[str, Any] = {"call_id": str(call_id), "case_id": str(dial.case_id),
                                    "result": result, "attempt": dial.attempt}
            if answered_by is not None:
                body["answered_by"] = answered_by
            await twilio.report_ended(body)
            return Response(status_code=204)

        @app.websocket("/twilio/relay/{call_id}")
        async def relay(ws: WebSocket, call_id: UUID):
            await ws.accept()
            await run_relay_call(twilio, call_id, ws)

    return app
