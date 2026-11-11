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
import logging
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


# Spec 0080, D3: one INFO line per signed TwiML fetch, so the detection settings can be tuned from
# real calls. It carries our path call id and the two detection fields, each reduced to a known
# value, "absent" or "other", and nothing else Twilio sent.
APP_LOGGER = "dialback_voice.app"
DETECTION_LINE = "twiml fetch for call %s: AnsweredBy=%s MachineDetectionDuration=%s"
CALL_SID = "CA0123456789abcdef0123456789abcdef"
DOCUMENTED_ANSWERED_BY = ("human", "machine_start", "machine_end_beep", "machine_end_silence",
                          "machine_end_other", "fax", "unknown")


def _twilio_form(**detection: str | None) -> dict[str, str]:
    """A fetch body shaped like Twilio's: the CallSid, the dialed number and our caller id ride
    along with the detection fields, so the tests can check that none of them reaches the log line.
    A detection field given as None is left out of the form."""
    form = {"CallSid": CALL_SID, "AccountSid": "ACtest00000000000000000000000000", "To": NUMBER,
            "Called": NUMBER, "From": "+14155550100", "Direction": "outbound-api",
            "CallStatus": "in-progress"}
    form.update({k: v for k, v in detection.items() if v is not None})
    return form


def _signed_post(client: TestClient, path: str, params: dict[str, str]):
    return client.post(path, data=params, headers={"X-Twilio-Signature": _sign(BASE + path, params)})


def _app_records(caplog) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.name == APP_LOGGER]


def _assert_one_detection_line(caplog, expected: str) -> str:
    records = _app_records(caplog)
    assert len(records) == 1, [r.getMessage() for r in records]
    assert records[0].levelno == logging.INFO
    assert records[0].msg == DETECTION_LINE
    line = records[0].getMessage()
    assert line == expected
    return line


def test_twiml_fetch_logs_the_detection_result(caplog):
    caplog.set_level(logging.INFO, logger=APP_LOGGER)
    client = _client()
    path = f"/twilio/twiml/{CALL}"
    token = tokens.mint(KEY, case_id=CASE, attempt=1, number_e164=NUMBER)
    disclosure = Script(CUSTOMER, CALLBACK).disclosure()
    for answered in DOCUMENTED_ANSWERED_BY:
        caplog.clear()
        r = _signed_post(client, path, _twilio_form(AnsweredBy=answered, MachineDetectionDuration="2100"))
        assert r.status_code == 200, r.text
        _assert_one_detection_line(
            caplog, f"twiml fetch for call {CALL}: AnsweredBy={answered} MachineDetectionDuration=2100")
        # The TwiML is unchanged by the log line: exactly what the builder makes for this call, with
        # the answered_by mapping of spec 0029 (human, machine, or nothing) and the call's vendor names
        # as hints (spec 0082, Part A, which the route passes since PR #118).
        carried = {"human": "human", "unknown": None}.get(answered, "machine")
        assert r.text == build_connect_twiml(ws_url=f"{WS_BASE}/twilio/relay/{CALL}", call_token=token,
                                             disclosure=disclosure, answered_by=carried,
                                             vendor_names=("Acme Fasteners",)), answered


def test_twiml_fetch_for_an_unknown_call_still_logs(caplog):
    caplog.set_level(logging.INFO, logger=APP_LOGGER)
    other = uuid.UUID("0d0d0d0d-1e1e-4f2f-8a3a-4b4b4b4b4b4b")
    path = f"/twilio/twiml/{other}"
    r = _signed_post(_client(), path, _twilio_form(AnsweredBy="machine_start", MachineDetectionDuration="4630"))
    assert r.status_code == 404
    assert r.json()["code"] == "unknown_call"
    _assert_one_detection_line(
        caplog, f"twiml fetch for call {other}: AnsweredBy=machine_start MachineDetectionDuration=4630")


@pytest.mark.parametrize(
    ("answered_by", "duration", "logged_answered", "logged_duration"),
    [
        ("Human", "123\n", "other", "other"),
        ("human\n", "-5", "other", "other"),
        (" ", " ", "other", "other"),                        # blank means empty; spaces are other (D3)
        ("machine", "1234567", "other", "other"),            # the report's word, not one of Twilio's
        (CALL_SID, "\u0661\u0662\u0663", "other", "other"),  # Arabic-Indic digits are not [0-9]
        (NUMBER, NUMBER, "other", "other"),
        ("human", "12.5", "human", "other"),
        ("fax", "", "fax", "absent"),
        ("", "2100", "absent", "2100"),
        ("", "", "absent", "absent"),
        (None, None, "absent", "absent"),
        ("unknown", "0", "unknown", "0"),
        ("human", "123456", "human", "123456"),              # six digits is the longest kept
    ],
    ids=["Human", "human-newline", "whitespace", "machine", "callsid-arabic-digits", "number-number", "decimal",
         "blank-duration", "blank-answered", "both-blank", "both-missing", "zero", "six-digits"],
)
def test_unexpected_detection_values_are_logged_as_other_or_absent(
        caplog, answered_by, duration, logged_answered, logged_duration):
    caplog.set_level(logging.INFO, logger=APP_LOGGER)
    path = f"/twilio/twiml/{CALL}"
    r = _signed_post(_client(), path, _twilio_form(AnsweredBy=answered_by, MachineDetectionDuration=duration))
    assert r.status_code == 200, r.text
    line = _assert_one_detection_line(
        caplog,
        f"twiml fetch for call {CALL}: AnsweredBy={logged_answered} MachineDetectionDuration={logged_duration}")
    # Nothing else Twilio sent, and nothing we minted, reaches the line (R9).
    assert NUMBER not in line
    assert CALL_SID not in line
    assert tokens.mint(KEY, case_id=CASE, attempt=1, number_e164=NUMBER) not in line


def test_a_forged_twiml_fetch_logs_no_detection_line(caplog):
    caplog.set_level(logging.INFO, logger=APP_LOGGER)
    client = _client()
    path = f"/twilio/twiml/{CALL}"
    params = _twilio_form(AnsweredBy="human", MachineDetectionDuration="2100")
    # Control: the same fetch, signed, logs its line, so the capture below could see one.
    assert _signed_post(client, path, params).status_code == 200
    assert len(_app_records(caplog)) == 1
    tampered = _sign(BASE + path, params | {"AnsweredBy": "machine_start"})
    for headers in ({}, {"X-Twilio-Signature": "forged"}, {"X-Twilio-Signature": tampered}):
        caplog.clear()
        r = client.post(path, data=params, headers=headers)
        assert r.status_code == 401, headers
        assert _app_records(caplog) == [], headers


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
