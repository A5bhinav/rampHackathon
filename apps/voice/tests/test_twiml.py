"""The TwiML endpoint (spec 0013). When Twilio fetches a call's instructions, the voice service
returns <Connect><ConversationRelay> carrying the fixed disclosure as the welcomeGreeting (played
first, R8) and the one-time call_token as a <Parameter>. The case id, the dialed number and the
number's lineage never appear in it, so Twilio and the model learn only what the call needs (R4).
The fetch is signature-verified, so a forged request mints no token (R9)."""
from __future__ import annotations

import base64
import hashlib
import hmac
import uuid

from dialback_policy.script import Script
from dialback_voice import tokens
from dialback_voice.app import CallDial, TwilioDeps, create_app
from fastapi.testclient import TestClient

KEY = b"call-token-key-for-tests-0123456789abcd"
AUTH = b"twilio-auth-token-for-tests-00000000000"
BASE = "https://voice.example"
WS_BASE = "wss://voice.example"
CALL = uuid.UUID("6b1e2f0a-4c3d-4e5f-8a9b-0c1d2e3f4a5b")
CASE = uuid.UUID("c0ffee00-1111-4222-8333-444455556666")
NUMBER = "+14155550123"
CUSTOMER = "Northwind Traders"
CALLBACK = "(415) 555-0100"


async def _no_report(body: dict) -> None: ...


def _deps() -> TwilioDeps:
    dial = CallDial(case_id=CASE, attempt=1, number_e164=NUMBER, customer_name=CUSTOMER,
                    callback_number=CALLBACK, vendor_names=("Acme Fasteners",))
    return TwilioDeps(call_token_key=KEY, twilio_auth_token=AUTH, ws_base_url=WS_BASE,
                      public_base_url=BASE, lookup_call=lambda cid: dial if cid == CALL else None,
                      report_ended=_no_report, agent_for=lambda: None, clock=lambda: None)


def _client() -> TestClient:
    app = create_app(store=None, internal_api_token=b"t", agent_version="agent-test-0", twilio=_deps())
    return TestClient(app)


def _sign(url: str, params: dict[str, str]) -> str:
    data = url + "".join(k + params[k] for k in sorted(params))
    return base64.b64encode(hmac.new(AUTH, data.encode(), hashlib.sha1).digest()).decode()


def test_connect_carries_disclosure_and_token():
    path = f"/twilio/twiml/{CALL}"
    r = _client().get(path, headers={"X-Twilio-Signature": _sign(BASE + path, {})})
    assert r.status_code == 200, r.text
    xml = r.text
    disclosure = Script(CUSTOMER, CALLBACK).disclosure()
    token = tokens.mint(KEY, case_id=CASE, attempt=1, number_e164=NUMBER)

    assert "<Connect>" in xml and "<ConversationRelay" in xml
    assert 'dtmfDetection="true"' in xml
    assert "welcomeGreeting=" in xml and disclosure in xml          # the disclosure plays first (R8)
    assert 'name="call_token"' in xml and token in xml             # the one-time token, bound to this call
    assert f"{WS_BASE}/twilio/relay/{CALL}" in xml                 # the ConversationRelay socket for this call
    # Leaks nothing a fraudster could use: not the case id, not the dialed number.
    assert str(CASE) not in xml
    assert NUMBER not in xml


def test_a_forged_twiml_fetch_is_rejected_and_mints_no_token():
    path = f"/twilio/twiml/{CALL}"
    r = _client().get(path, headers={"X-Twilio-Signature": "forged"})
    assert r.status_code == 401
    token = tokens.mint(KEY, case_id=CASE, attempt=1, number_e164=NUMBER)
    assert token not in r.text and "welcomeGreeting" not in r.text
