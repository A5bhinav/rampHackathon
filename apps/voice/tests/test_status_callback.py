"""The Twilio status-callback endpoint (spec 0013). A call that never reaches a live conversation
(no answer, busy, failed) or that an answering machine picks up is reported to the core from here,
not from the WebSocket. Every callback is signature-verified; a forged one is rejected and reports
nothing (R7, R9: Twilio's POST is data, trusted only once its X-Twilio-Signature checks out)."""
from __future__ import annotations

import base64
import hashlib
import hmac
import uuid

from dialback_voice.app import CallDial, TwilioDeps, create_app
from dialback_voice.twilio.origination import status_result
from fastapi.testclient import TestClient

AUTH = b"twilio-auth-token-for-tests-00000000000"
BASE = "https://voice.example"
CALL = uuid.UUID("6b1e2f0a-4c3d-4e5f-8a9b-0c1d2e3f4a5b")
PICK = uuid.UUID("7d0c1f3e-2a4b-4c5d-8e6f-9a0b1c2d3e4f")
CASE = uuid.UUID("c0ffee00-1111-4222-8333-444455556666")
NUMBER = "+14155550123"


class Reporter:
    def __init__(self) -> None:
        self.bodies: list[dict] = []

    async def __call__(self, body: dict) -> None:
        self.bodies.append(body)


def _client(reporter) -> TestClient:
    dial = CallDial(case_id=CASE, attempt=2, number_e164=NUMBER, customer_name="Northwind Traders",
                    callback_number="(415) 555-0100", vendor_names=("Acme Fasteners",), contact_point_id=PICK)
    deps = TwilioDeps(call_token_key=b"k", twilio_auth_token=AUTH, ws_base_url="wss://voice.example",
                      public_base_url=BASE, from_number="+14155550100", originator=None,
                      lookup_call=lambda cid: dial if cid == CALL else None,
                      report_ended=reporter, agent_for=lambda: None, clock=lambda: None)
    app = create_app(store=None, internal_api_token=b"t", agent_version="a", twilio=deps)
    return TestClient(app)


def _sign(url: str, params: dict[str, str]) -> str:
    data = url + "".join(k + params[k] for k in sorted(params))
    return base64.b64encode(hmac.new(AUTH, data.encode(), hashlib.sha1).digest()).decode()


def test_non_connect_results_are_reported_and_signed():
    url = f"{BASE}/twilio/status/{CALL}"
    path = f"/twilio/status/{CALL}"
    cases = [({"CallSid": "CA1", "CallStatus": "no-answer"}, "no_answer", None),
             ({"CallSid": "CA1", "CallStatus": "busy"}, "busy", None),
             ({"CallSid": "CA1", "CallStatus": "failed"}, "failed", None),
             ({"CallSid": "CA1", "CallStatus": "completed", "AnsweredBy": "machine_start"}, "machine", "machine")]
    for params, result, answered_by in cases:
        reporter = Reporter()
        r = _client(reporter).post(path, data=params, headers={"X-Twilio-Signature": _sign(url, params)})
        assert r.status_code == 204, (params, r.status_code, r.text)
        assert len(reporter.bodies) == 1, params
        body = reporter.bodies[0]
        assert body["result"] == result
        assert body["call_id"] == str(CALL) and body["case_id"] == str(CASE) and body["attempt"] == 2
        assert body["contact_point_id"] == str(PICK)  # the pick it dialed, so decide() can count it (spec 0030)
        if answered_by is None:
            assert "answered_by" not in body
        else:
            assert body["answered_by"] == answered_by


def test_a_forged_status_callback_is_rejected_and_reports_nothing():
    reporter = Reporter()
    r = _client(reporter).post(f"/twilio/status/{CALL}", data={"CallSid": "CA1", "CallStatus": "no-answer"},
                               headers={"X-Twilio-Signature": "forged"})
    assert r.status_code == 401
    assert reporter.bodies == []        # an unverified callback moves nothing


def test_status_result_maps_twilio_statuses():
    assert status_result("no-answer", None) == ("no_answer", None)
    assert status_result("busy", None) == ("busy", None)
    assert status_result("failed", None) == ("failed", None)
    assert status_result("canceled", None) == ("failed", None)
    assert status_result("completed", "machine_end_beep") == ("machine", "machine")
    assert status_result("completed", "fax") == ("machine", "machine")
    assert status_result("completed", "human") is None      # a human conversation: the WebSocket reports it
    assert status_result("in-progress", None) is None
