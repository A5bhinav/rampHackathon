"""POST /internal/calls/{call_id}/ended: the voice service's call report (Sami's
openapi/internal.yaml, operation reportCallEnded).

On arrival the core compares the spoken digits with the case's accounts, replaces them with
a keyed hash, and appends call_ended with the stored answers (spec 0002, D7). Only the
case's current call counts (D6): a report for any other call gets 409. The request body is
never logged, and validation errors never echo it back.
"""
from __future__ import annotations

import hmac
import logging
import re
from typing import Annotated, Any, Literal
from uuid import UUID

from dialback_decision import compare_last_four
from dialback_decision.rules import CHANGE, CONSENT, FLAGS
from fastapi import APIRouter, Header, Request
from fastapi.responses import JSONResponse, Response
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StrictBool, field_validator, model_validator

from .answers import NO_DIGITS, protect, spoken_last_four
from .db import system_tx
from .events import TransitionRefused, apply_event

log = logging.getLogger(__name__)
router = APIRouter()
_DIGITS = re.compile(r"^[0-9]{1,4}$")
ALLOWED = {"recording_consent": CONSENT, "change_requested": CHANGE, "flag": FLAGS}


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TransitAnswer(Strict):
    slot: Literal["recording_consent", "company_named", "department", "speaker_name", "speaker_role",
                  "change_requested", "account_last4", "offered_number", "flag"]
    value: Any
    turn_id: int = Field(ge=0)
    tool_call_id: str = Field(min_length=1, max_length=128)
    captured_at: str
    capture_method: Literal["speech", "keypad"]

    @field_validator("value")
    @classmethod
    def _typed(cls, value: Any, info) -> Any:
        slot = info.data.get("slot")
        if slot == "account_last4":
            if not isinstance(value, str) or not (value in NO_DIGITS or _DIGITS.match(value)):
                raise ValueError("account_last4 must be 1 to 4 digits, dont_know or refused")
        elif slot in ALLOWED:
            if value not in ALLOWED[slot]:  # the decision engine reads only these values (spec 0006)
                raise ValueError(f"{slot} must be one of {', '.join(sorted(ALLOWED[slot]))}")
        elif not isinstance(value, str) or not value or len(value) > 200:
            raise ValueError("must be a non-empty string of at most 200 characters")
        return value


class TransitAnswers(Strict):
    schema_version: Literal["call-answers/v0"]
    call_id: UUID
    answers: list[TransitAnswer] = Field(max_length=50)


class Checks(Strict):
    disclosure_first: bool
    answers_grounded: bool
    agent_spoke_account_digits: bool
    new_number_accepted: bool
    policy_violations: int = Field(ge=0)
    speech_guard_trips: int = Field(ge=0)


class CallEndedRequest(Strict):
    call_id: UUID
    case_id: UUID
    result: Literal["answers_recorded", "no_answer", "voicemail", "busy", "failed", "machine", "declined",
                    "callback_requested"]
    attempt: int = Field(ge=1)
    retry_at: AwareDatetime | None = None  # a real moment with its offset: never 'infinity' or a local time
    recording_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    transcript_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    agent_version: str | None = None
    answers: TransitAnswers | None = None
    contact_point_id: UUID | None = None
    checks: Checks | None = None
    # Twilio's facts about the call (CallEndedBody; spec 0005, D6). Absent means unknown.
    direction: Literal["outbound", "inbound"] | None = None
    dialed_number_is_pick: StrictBool | None = None  # a real boolean only: never "yes" or 1
    answered_by: Literal["human", "machine"] | None = None

    @model_validator(mode="after")
    def _callback_has_a_time(self) -> CallEndedRequest:
        if self.result == "callback_requested" and not self.retry_at:
            raise ValueError("retry_at is required with callback_requested: the time the vendor asked to be called")
        return self


def _error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse({"code": code, "message": message}, status_code=status)


@router.post("/internal/calls/{call_id}/ended")
async def report_call_ended(call_id: UUID, body: CallEndedRequest, request: Request,
                            idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=200)]):
    settings = request.app.state.settings
    token = request.headers.get("authorization", "").removeprefix("Bearer ").strip().encode()
    if not settings.internal_api_token or not hmac.compare_digest(token, settings.internal_api_token):
        return _error(401, "unauthorized", "A valid service token is required.")
    if body.call_id != call_id or (body.answers and body.answers.call_id != call_id):
        return _error(422, "invalid_request", "call_id in the path, the body and the answers must match.")
    if body.result == "answers_recorded" and body.answers is None:
        return _error(422, "invalid_request", "answers are required when result is answers_recorded.")

    async with system_tx(request.app.state.pool) as conn:
        case = await (await conn.execute(
            """select c.id, n.account_last4 as new_last4, o.account_last4 as old_last4
                 from app.cases c join app.bank_change_events ce on ce.id = c.change_event_id
                 join app.bank_accounts n on n.id = ce.new_account_id
                 left join app.bank_accounts o on o.id = ce.old_account_id
                where c.id = %s""", (body.case_id,))).fetchone()
        if case is None:
            return _error(409, "unknown_case", "No such case.")

        event: dict[str, Any] = body.model_dump(mode="json", exclude_none=True, exclude={"case_id", "answers"})
        if body.answers is not None:
            transit = body.answers.model_dump(mode="json")
            event["last_four_result"] = compare_last_four(spoken_last_four(transit), case["new_last4"], case["old_last4"])
            event["answers"] = protect(transit, settings.digits_key, settings.digits_key_version)
            del transit  # the digits go no further than this function
        try:
            result = await apply_event(conn, body.case_id, "call_ended", f"{body.case_id}:call:{call_id}:ended", event)
        except TransitionRefused:
            return _error(409, "not_current_call", "The case is not waiting on this call.")
    log.info("call %s for case %s reported: %s", call_id, body.case_id, body.result)
    if result.status == "already_applied":
        return JSONResponse({"status": "already_applied"}, status_code=200)
    return Response(status_code=204)
