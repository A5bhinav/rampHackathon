"""Twilio webhook signatures (spec 0034, B3).

Twilio signs the full URL it requested, query string included, followed by every form field (blank
values included) as key and value, sorted by key then value, duplicates removed; for a GET only the
URL. The signer below is checked against Twilio's own published vectors (twilio-python,
tests/unit/test_request_validator.py) before it signs any request here, so a divergence from
Twilio's scheme fails in this file, not on a live call. A webhook with one blank field, such as
`CalledCity=`, is ordinary: Twilio sends them whenever a lookup has no value."""
from __future__ import annotations

import base64
import hashlib
import hmac
import uuid
from urllib.parse import urlencode

from dialback_voice.app import CallDial, TwilioDeps, create_app
from dialback_voice.twilio.origination import valid_twilio_signature
from fastapi.testclient import TestClient

# Twilio's published vectors: a POST to a URL with a query string, and a POST with duplicate keys.
VECTOR_TOKEN = b"12345"
VECTOR_URL = "https://mycompany.com/myapp.php?foo=1&bar=2"
VECTOR_PARAMS = {"CallSid": "CA1234567890ABCDE", "Digits": "1234", "From": "+14158675309",
                 "To": "+18005551212", "Caller": "+14158675309"}
VECTOR_SIGNATURE = "RSOYDt4T1cUTdK1PDd93/VVr8B8="
DUPLICATE_PAIRS = [("Sid", "CA123"), ("SidAccount", "AC123"), ("Digits", "5678"), ("Digits", "1234"),
                   ("Digits", "1234")]
DUPLICATE_SIGNATURE = "IK+Dwps556ElfBT0I3Rgjkr1wJU="

AUTH = b"twilio-auth-token-for-tests-00000000000"
BASE = "https://voice.example"
CALL = uuid.UUID("6b1e2f0a-4c3d-4e5f-8a9b-0c1d2e3f4a5b")
FORM = {"content-type": "application/x-www-form-urlencoded"}


def _twilio_sign(token: bytes, url: str, pairs) -> str:
    data = url + "".join(k + v for k, v in sorted(set(pairs)))
    return base64.b64encode(hmac.new(token, data.encode(), hashlib.sha1).digest()).decode()


class Reporter:
    def __init__(self) -> None:
        self.bodies: list[dict] = []

    async def __call__(self, body: dict) -> None:
        self.bodies.append(body)


def _client(reporter: Reporter) -> TestClient:
    dial = CallDial(case_id=uuid.uuid4(), attempt=1, number_e164="+14155550123", customer_name="Northwind Traders",
                    callback_number="(415) 555-0100", vendor_names=("Acme Fasteners",), contact_point_id=None)
    deps = TwilioDeps(call_token_key=b"k", twilio_auth_token=AUTH, ws_base_url="wss://voice.example",
                      public_base_url=BASE, from_number="+14155550100", originator=None,
                      lookup_call=lambda cid: dial if cid == CALL else None,
                      report_ended=reporter, agent_for=lambda: None, clock=lambda: None)
    return TestClient(create_app(store=None, internal_api_token=b"t", agent_version="a", twilio=deps))


def _post(client: TestClient, path: str, pairs, *, query: str = "", signature: str | None = None):
    target = path + (f"?{query}" if query else "")
    sig = signature if signature is not None else _twilio_sign(AUTH, BASE + target, pairs)
    return client.post(target, content=urlencode(pairs), headers={**FORM, "X-Twilio-Signature": sig})


def test_the_signer_reproduces_twilios_published_vectors():
    assert _twilio_sign(VECTOR_TOKEN, VECTOR_URL, VECTOR_PARAMS.items()) == VECTOR_SIGNATURE
    assert _twilio_sign(VECTOR_TOKEN, VECTOR_URL, DUPLICATE_PAIRS) == DUPLICATE_SIGNATURE


def test_valid_twilio_signature_accepts_twilios_vectors():
    assert valid_twilio_signature(VECTOR_TOKEN, VECTOR_SIGNATURE, VECTOR_URL, VECTOR_PARAMS.items())
    assert valid_twilio_signature(VECTOR_TOKEN, DUPLICATE_SIGNATURE, VECTOR_URL, DUPLICATE_PAIRS)


def test_valid_twilio_signature_rejects_a_wrong_or_missing_signature():
    assert not valid_twilio_signature(VECTOR_TOKEN, "forged", VECTOR_URL, VECTOR_PARAMS.items())
    assert not valid_twilio_signature(VECTOR_TOKEN, "", VECTOR_URL, VECTOR_PARAMS.items())
    assert not valid_twilio_signature(b"another-token", VECTOR_SIGNATURE, VECTOR_URL, VECTOR_PARAMS.items())


def test_a_status_callback_with_a_blank_field_is_accepted():
    reporter = Reporter()
    pairs = [("CallSid", "CA1"), ("CallStatus", "no-answer"), ("CalledCity", ""), ("CalledState", "CA")]
    r = _post(_client(reporter), f"/twilio/status/{CALL}", pairs)
    assert r.status_code == 204, r.text
    assert [b["result"] for b in reporter.bodies] == ["no_answer"]


def test_a_twiml_fetch_with_a_blank_field_is_accepted():
    r = _post(_client(Reporter()), f"/twilio/twiml/{CALL}", [("CallSid", "CA1"), ("CalledCity", "")])
    assert r.status_code == 200, r.text
    assert "<ConversationRelay" in r.text


def test_the_signed_url_includes_the_query_string():
    reporter = Reporter()
    r = _post(_client(reporter), f"/twilio/status/{CALL}", [("CallSid", "CA1"), ("CallStatus", "busy")],
              query="attempt=2&x=1")
    assert r.status_code == 204, r.text
    assert [b["result"] for b in reporter.bodies] == ["busy"]


def test_a_get_fetch_signs_the_url_with_its_query_and_no_fields():
    path = f"/twilio/twiml/{CALL}?CallSid=CA1&CalledCity="
    r = _client(Reporter()).get(path, headers={"X-Twilio-Signature": _twilio_sign(AUTH, BASE + path, [])})
    assert r.status_code == 200, r.text


def test_a_signature_that_skips_the_blank_field_is_rejected():
    # The old parser dropped blank fields before checking, so a signature computed without them was
    # accepted for a request that carries them. Every field Twilio sent is part of what is signed.
    reporter = Reporter()
    pairs = [("CallSid", "CA1"), ("CallStatus", "no-answer"), ("CalledCity", "")]
    skipped = _twilio_sign(AUTH, f"{BASE}/twilio/status/{CALL}", [p for p in pairs if p[1]])
    r = _post(_client(reporter), f"/twilio/status/{CALL}", pairs, signature=skipped)
    assert r.status_code == 401
    assert reporter.bodies == []


def test_a_forged_signature_is_still_rejected():
    reporter = Reporter()
    r = _post(_client(reporter), f"/twilio/status/{CALL}", [("CallSid", "CA1"), ("CallStatus", "busy")],
              signature="forged")
    assert r.status_code == 401
    assert reporter.bodies == []
