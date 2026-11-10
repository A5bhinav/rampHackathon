"""Spec 0048, A1: the report helpers are public, so the attack lab chains production's own report code
instead of a copy. These pin what the lab relies on: answers ride along only on answers_recorded, and a
verified setup vouches that the dialed number is the pick."""
from __future__ import annotations

import datetime as dt
import uuid

from dialback_policy.step import End
from dialback_voice.app import report_body, with_connect_facts
from dialback_voice.store import CallDial
from dialback_voice.transport import CallResult

CALL, CASE, PICK = uuid.UUID(int=11), uuid.UUID(int=12), uuid.UUID(int=13)
ANSWERS = {"schema_version": "call-answers/v0", "call_id": str(CALL), "answers": []}
CHECKS = {"disclosure_first": True, "answers_grounded": True, "agent_spoke_account_digits": False,
          "new_number_accepted": False, "policy_violations": 0, "speech_guard_trips": 0}


def _dial() -> CallDial:
    return CallDial(case_id=CASE, attempt=2, number_e164="+14155550100", customer_name="Northwind Traders",
                    callback_number="(415) 555-0100", vendor_names=("Acme Fasteners",), contact_point_id=PICK)


def _result(result: str, retry_at: dt.datetime | None = None) -> CallResult:
    return CallResult(End(result, "test", retry_at), ANSWERS, CHECKS, ())


def test_answers_ride_along_only_when_recorded():
    recorded = report_body(_result("answers_recorded"), CALL, _dial())
    assert recorded["answers"] == ANSWERS
    assert recorded | {"answers": None} == {"call_id": str(CALL), "case_id": str(CASE), "result": "answers_recorded",
                                            "attempt": 2, "contact_point_id": str(PICK), "answers": None}
    for result in ("no_answer", "voicemail", "failed", "declined", "callback_requested"):
        assert "answers" not in report_body(_result(result), CALL, _dial()), result


def test_a_callback_carries_its_time():
    at = dt.datetime(2026, 11, 2, 16, 30, tzinfo=dt.UTC)
    assert report_body(_result("callback_requested", at), CALL, _dial())["retry_at"] == "2026-11-02T16:30:00Z"


def test_a_verified_setup_vouches_for_the_pick_and_reports_direction_answerer_and_checks():
    body = report_body(_result("answers_recorded"), CALL, _dial())
    with_connect_facts(body, {"direction": "outbound-api", "customParameters": {"answered_by": "human"}},
                       _result("answers_recorded"))
    assert (body["dialed_number_is_pick"], body["direction"], body["answered_by"], body["checks"]) == (
        True, "outbound", "human", CHECKS)
    inbound = report_body(_result("answers_recorded"), CALL, _dial())
    with_connect_facts(inbound, {"direction": "inbound", "customParameters": {"answered_by": "unknown"}},
                       _result("answers_recorded"))
    assert inbound["direction"] == "inbound" and "answered_by" not in inbound
    assert inbound["dialed_number_is_pick"] is True          # set on every verified setup, never False
