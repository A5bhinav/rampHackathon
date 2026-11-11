"""The TwiML endpoint (spec 0013). When Twilio fetches a call's instructions, the voice service
returns <Connect><ConversationRelay> carrying the fixed disclosure as the welcomeGreeting (played
first, R8) and the one-time call_token as a <Parameter>. The case id, the dialed number and the
number's lineage never appear in it, so Twilio and the model learn only what the call needs (R4).
The fetch is signature-verified, so a forged request mints no token (R9)."""
from __future__ import annotations

import base64
import dataclasses
import hashlib
import hmac
import uuid
import xml.etree.ElementTree as ET

import pytest
from dialback_policy.script import Script
from dialback_voice import tokens
from dialback_voice.app import CallDial, TwilioDeps, create_app
from dialback_voice.twilio.origination import build_connect_twiml
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
                      public_base_url=BASE, from_number="+14155550100", originator=None,
                      lookup_call=lambda cid: dial if cid == CALL else None,
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


def test_keys_pressed_while_the_agent_speaks_still_reach_the_relay():
    # Spec 0043: callers often start typing the last four before the question finishes. Since May
    # 2025 Twilio's default for reportInputDuringAgentSpeech is "none", which drops those keys, so
    # the call asks for DTMF during speech. Keys still never cut the agent off (interruptible stays
    # "false", R8), and speech during the agent's turn is still not reported.
    path = f"/twilio/twiml/{CALL}"
    r = _client().get(path, headers={"X-Twilio-Signature": _sign(BASE + path, {})})
    assert r.status_code == 200, r.text
    xml = r.text
    assert 'reportInputDuringAgentSpeech="dtmf"' in xml
    assert 'interruptible="false"' in xml
    assert 'dtmfDetection="true"' in xml


def test_a_forged_twiml_fetch_is_rejected_and_mints_no_token():
    path = f"/twilio/twiml/{CALL}"
    r = _client().get(path, headers={"X-Twilio-Signature": "forged"})
    assert r.status_code == 401
    token = tokens.mint(KEY, case_id=CASE, attempt=1, number_e164=NUMBER)
    assert token not in r.text and "welcomeGreeting" not in r.text


def test_machine_detection_rides_to_the_call_as_a_parameter():
    # Spec 0029: Twilio's synchronous machine detection answers on the instruction fetch. "human"
    # rides to the WebSocket as a parameter, so the call report can say a person answered (V03);
    # anything else is passed as "machine" or not at all, never as "human".
    path = f"/twilio/twiml/{CALL}"
    for answered, expected in (("human", "human"), ("machine_start", "machine"), ("fax", "machine"), ("unknown", None)):
        params = {"AnsweredBy": answered}
        r = _client().post(path, data=params, headers={"X-Twilio-Signature": _sign(BASE + path, params)})
        assert r.status_code == 200, r.text
        if expected is None:
            assert 'name="answered_by"' not in r.text, answered
        else:
            assert f'<Parameter name="answered_by" value="{expected}"/>' in r.text, answered


# Spec 0082, Part A: the TwiML carries the vendor's names as ConversationRelay `hints`, so the
# recognizer prefers "Acme" over "acne". The hints bias what is heard; nothing is ever spoken (R4).

def _relay(xml: str) -> ET.Element:
    relay = ET.fromstring(xml.encode("utf-8")).find("./Connect/ConversationRelay")
    assert relay is not None, xml
    return relay


def _twiml(**kwargs) -> str:
    return build_connect_twiml(ws_url=f"{WS_BASE}/twilio/relay/{CALL}", call_token="tok",
                               disclosure="This call is from an AI agent and is recorded.", **kwargs)


@pytest.mark.parametrize(("names", "hints"), [
    # A1: the four examples in D2.
    (("Acme Fasteners LLC",), "Acme Fasteners LLC,Acme"),
    (("The Acme Group",), "The Acme Group,Acme"),
    (("First Solar Inc",), "First Solar Inc"),               # "first" is a common first word
    (("Yes Energy LLC",), "Yes Energy LLC"),                 # "yes" is under 4 characters
    # A2: commas become spaces; spaces are trimmed and collapsed.
    (("Acme Fasteners, LLC",), "Acme Fasteners LLC,Acme"),
    (("  Acme \u00a0  Fasteners   LLC  ",), "Acme Fasteners LLC,Acme"),
    (("Acme, Inc.",), "Acme Inc."),                          # one word once the suffix is gone
    # A2: the first word is as written, with the punctuation around it stripped.
    (('"Acme" Fasteners',), '"Acme" Fasteners,Acme'),
    # A written word the guard reads as two ("Acme-Fasteners") is not a first word on its own.
    (("Acme-Fasteners LLC",), "Acme-Fasteners LLC"),
    # A2: a whitespace-only or comma-only name gives no hint.
    (("   ", "Acme Fasteners LLC"), "Acme Fasteners LLC,Acme"),
    ((" , ,",), None),
    # A2: duplicates are dropped ignoring case, and the first spelling wins.
    (("ACME Fasteners LLC", "Acme Fasteners LLC", "acme bolts"), "ACME Fasteners LLC,ACME,acme bolts"),
    # A2: a hint over 100 characters is skipped; exactly 100 is kept. The first word is its own hint.
    (("Acme " + "B" * 95,), "Acme " + "B" * 95 + ",Acme"),
    (("Acme " + "B" * 96,), "Acme"),
    # A2: at most 10 hints.
    (tuple(f"Vendor{i}" for i in range(12)), ",".join(f"Vendor{i}" for i in range(10))),
    (tuple(f"Vendor{i} Supply" for i in range(6)),
     ",".join(f"Vendor{i} Supply,Vendor{i}" for i in range(5))),
])
def test_hints_carry_the_vendor_names(names: tuple[str, ...], hints: str | None):
    assert _relay(_twiml(vendor_names=names)).get("hints") == hints


def test_hints_are_escaped_and_read_back():
    # A3: quotes, ampersands and angle brackets are escaped, and control characters (Unicode
    # category C) are dropped before anything else, so the TwiML stays well-formed XML.
    names = ("Acme \"Bolts\" & <Nuts> O'Neil LLC", "Smith\x0bJones\u200b Supply\x1f")
    xml = _twiml(vendor_names=names)
    assert "&amp;" in xml and "&lt;Nuts&gt;" in xml and "&quot;Bolts&quot;" in xml
    assert "<Nuts>" not in xml and "\x0b" not in xml and "\x1f" not in xml and "\u200b" not in xml
    assert _relay(xml).get("hints") == "Acme \"Bolts\" & <Nuts> O'Neil LLC,Acme,SmithJones Supply,SmithJones"


@pytest.mark.parametrize("answered_by", [None, "human"])
@pytest.mark.parametrize("names", [(), ("",), ("   ", " , ,"), ("\x0b\u200b",)])
def test_no_vendor_names_means_no_hints(names: tuple[str, ...], answered_by: str | None):
    # A4: with no hints the TwiML is exactly what it is without names (compared with the function,
    # not a frozen string, since other work edits the same attribute line).
    without = _twiml(answered_by=answered_by)
    assert _twiml(vendor_names=names, answered_by=answered_by) == without
    assert _relay(without).get("hints") is None


def test_the_route_hints_the_calls_vendor_names_and_never_speaks_them():
    # A5: the route passes the looked-up call's vendor_names. The names reach the recognizer as
    # hints and never the welcomeGreeting, so the callee hears nothing of who we are checking (R4).
    dial = CallDial(case_id=CASE, attempt=1, number_e164=NUMBER, customer_name=CUSTOMER,
                    callback_number=CALLBACK, vendor_names=("Brightline Office Supply Inc", "Acme Fasteners LLC"))
    deps = dataclasses.replace(_deps(), lookup_call=lambda cid: dial if cid == CALL else None)
    client = TestClient(create_app(store=None, internal_api_token=b"t", agent_version="agent-test-0", twilio=deps))
    path = f"/twilio/twiml/{CALL}"
    r = client.get(path, headers={"X-Twilio-Signature": _sign(BASE + path, {})})
    assert r.status_code == 200, r.text
    relay = _relay(r.text)
    assert relay.get("hints") == "Brightline Office Supply Inc,Brightline,Acme Fasteners LLC,Acme"
    greeting = relay.get("welcomeGreeting").lower()
    assert greeting == Script(CUSTOMER, CALLBACK).disclosure().lower()
    assert all(word not in greeting for word in ("brightline", "acme", "fasteners", "office"))
