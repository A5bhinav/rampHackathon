"""POST /calls dials the Trust Ledger's pick over Twilio when live (spec 0014).

The endpoint is driven against an in-memory store and a fake Originator, so no live account is
touched; the TwilioOriginator REST client is unit-tested on its own against an httpx MockTransport.
In text mode (twilio=None) nothing dials, which the existing POST /calls tests already cover."""
from __future__ import annotations

import asyncio
import urllib.parse
import uuid

import httpx
from dialback_voice.app import TwilioDeps, create_app
from dialback_voice.guards import PreDialInputs
from dialback_voice.report import CoreUnavailable
from dialback_voice.store import PlacedCall, PreDial
from dialback_voice.twilio import origination
from dialback_voice.twilio.origination import TwilioOriginator
from fastapi.testclient import TestClient

TOKEN = b"internal-service-token-for-tests-0123456789"
CASE = uuid.UUID("c0ffee00-1111-4222-8333-444455556666")
PICK = uuid.UUID("a1a2a3a4-b1b2-4c3c-8d4d-e5e6e7e8e9ea")
NUMBER = "+14155550123"
FROM = "+14155550100"
BASE = "https://voice.example"


class FakeOriginator:
    """Records each dial and returns a fixed CallSid, or raises when `fail` is set (a Twilio
    rejection or a network error: the dial did not place a call)."""

    def __init__(self, sid: str = "CA00000000000000000000000000000001", *, fail: bool = False):
        self.sid = sid
        self.fail = fail
        self.calls: list[dict] = []

    async def originate(self, *, to, from_, twiml_url, status_url) -> str:
        self.calls.append({"to": to, "from_": from_, "twiml_url": twiml_url, "status_url": status_url})
        if self.fail:
            raise RuntimeError("twilio rejected the call")
        return self.sid


class FakeStore:
    """The in-memory store from test_calls_endpoint, plus the two writes the dial makes:
    mark_dialed on success, mark_failed when origination fails."""

    def __init__(self, pre_dial: PreDial, *, raise_on_dialed: bool = False, raise_on_failed: bool = False):
        self._pre_dial = pre_dial
        self.by_key: dict[str, PlacedCall] = {}
        self.dialed: dict[uuid.UUID, str] = {}
        self.failed: dict[uuid.UUID, str] = {}
        self._raise_on_dialed = raise_on_dialed   # simulate a voice.calls write blip on each path
        self._raise_on_failed = raise_on_failed

    async def find_call(self, idempotency_key):
        return self.by_key.get(idempotency_key)

    async def gather_pre_dial(self, *, case_id, attempt):
        return self._pre_dial

    async def record_call(self, *, case_id, attempt, idempotency_key, contact_point_id,
                          number_e164, agent_version):
        if idempotency_key in self.by_key:
            return self.by_key[idempotency_key], False
        placed = PlacedCall(call_id=uuid.uuid4(), case_id=case_id, attempt=attempt)
        self.by_key[idempotency_key] = placed
        return placed, True

    async def mark_dialed(self, call_id, *, provider_call_id):
        if self._raise_on_dialed:
            raise RuntimeError("voice.calls write failed")
        self.dialed[call_id] = provider_call_id

    async def mark_failed(self, call_id, reason):
        if self._raise_on_failed:
            raise RuntimeError("voice.calls write failed")
        self.failed[call_id] = reason


def _pre_dial(**over) -> PreDial:
    base = {"case_exists": True, "case_finished": False, "case_held": True, "has_number_pick": True,
            "number_opted_out": False, "within_calling_window": True, "attempts_used": 0,
            "number_calls_last_30d": 0, "demo_dataset": False, "number_on_allowlist": True}
    return PreDial(inputs=PreDialInputs(**(base | over)), contact_point_id=PICK, number_e164=NUMBER)


def _deps(originator, reports: list, *, report_ended=None) -> TwilioDeps:
    async def _append(body):
        reports.append(body)
    return TwilioDeps(call_token_key=b"k", twilio_auth_token=b"auth", ws_base_url="wss://voice.example",
                      public_base_url=BASE, from_number=FROM, originator=originator,
                      lookup_call=lambda cid: None, report_ended=report_ended or _append,
                      agent_for=lambda: None, clock=lambda: None)


def _client(store, deps) -> TestClient:
    app = create_app(store=store, internal_api_token=TOKEN, agent_version="agent-test-0", twilio=deps)
    return TestClient(app)


def _post(client, *, key="k1"):
    return client.post("/calls", json={"case_id": str(CASE), "attempt": 1},
                       headers={"Idempotency-Key": key, "Authorization": f"Bearer {TOKEN.decode()}"})


def test_live_placement_dials_the_pick():
    store = FakeStore(_pre_dial())
    orig = FakeOriginator()
    r = _post(_client(store, _deps(orig, [])))
    assert r.status_code == 202
    call_id = r.json()["call_id"]
    assert len(orig.calls) == 1
    c = orig.calls[0]
    assert c["to"] == NUMBER and c["from_"] == FROM
    assert c["twiml_url"] == f"{BASE}/twilio/twiml/{call_id}"
    assert c["status_url"] == f"{BASE}/twilio/status/{call_id}"
    # the case id is never handed to Twilio: it appears in no dial argument
    assert str(CASE) not in " ".join(str(v) for v in c.values())
    assert store.dialed[uuid.UUID(call_id)] == orig.sid   # the CallSid was recorded
    assert store.failed == {}


def test_a_replayed_key_places_no_second_call():
    store = FakeStore(_pre_dial())
    orig = FakeOriginator()
    client = _client(store, _deps(orig, []))
    first = _post(client, key="same")
    second = _post(client, key="same")
    assert first.status_code == 202 and second.status_code == 200
    assert first.json()["call_id"] == second.json()["call_id"]
    assert len(orig.calls) == 1   # dialed once, not twice


def test_originate_failure_reports_failed():
    store = FakeStore(_pre_dial())
    orig = FakeOriginator(fail=True)
    reports: list[dict] = []
    r = _post(_client(store, _deps(orig, reports)))
    assert r.status_code == 202          # the request was accepted; the failure rides the report path
    assert len(reports) == 1
    body = reports[0]
    assert body["result"] == "failed"
    assert body["case_id"] == str(CASE)
    call_id = r.json()["call_id"]
    assert body["call_id"] == call_id
    assert body["attempt"] == 1
    assert body["contact_point_id"] == str(PICK)  # spec 0030: the pick, even on a failed dial
    assert store.failed[uuid.UUID(call_id)] == "originate_failed"
    assert store.dialed == {}            # never recorded as dialed


def test_placement_is_accepted_when_the_failed_report_cannot_land():
    """Q51/decision 3: in the synchronous-dial path the `failed` report cannot land (the core is still
    awaiting this 202 and has not appended `call_started`, so every retry 409s and `report_ended`
    raises `CoreUnavailable`). The placement must still return 202, not 500; the case stays `failed`
    and the core's `call_timeout` sweeps it to a person (R7)."""
    store = FakeStore(_pre_dial())
    orig = FakeOriginator(fail=True)

    async def report_raises(body):
        raise CoreUnavailable("the core did not accept the report")

    r = _post(_client(store, _deps(orig, [], report_ended=report_raises)))
    assert r.status_code == 202          # not 500: the dial outcome never fails the placement
    call_id = r.json()["call_id"]
    assert store.failed[uuid.UUID(call_id)] == "originate_failed"   # still recorded failed
    assert store.dialed == {}


def test_placement_is_accepted_when_marking_the_call_failed_raises():
    """A store blip on the failure path must not 500 the placement either; once the call is recorded
    the dial is best effort, with the core timeout as the backstop (R7, decision 3)."""
    store = FakeStore(_pre_dial(), raise_on_failed=True)
    orig = FakeOriginator(fail=True)
    reports: list[dict] = []
    r = _post(_client(store, _deps(orig, reports)))
    assert r.status_code == 202
    assert reports and reports[0]["result"] == "failed"   # the report is still attempted after the blip


def test_placement_is_accepted_when_recording_the_sid_raises():
    """The call was placed over Twilio; a store blip recording the CallSid must not 500 the placement.
    The call is live and reports its own end over the relay or the status callback (R7)."""
    store = FakeStore(_pre_dial(), raise_on_dialed=True)
    orig = FakeOriginator()
    r = _post(_client(store, _deps(orig, [])))
    assert r.status_code == 202
    assert len(orig.calls) == 1                       # the dial did happen
    call_id = r.json()["call_id"]
    assert uuid.UUID(call_id) not in store.dialed     # the SID just was not recorded
    assert store.failed == {}                         # a placed call is not a failure


def test_twilio_originator_posts_the_call_and_returns_the_sid():
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization", "")
        seen["body"] = request.content.decode()
        return httpx.Response(201, json={"sid": "CA99999999999999999999999999999999", "status": "queued"})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            orig = TwilioOriginator(http, account_sid="ACtest00000000000000000000000000", auth_token=b"secret")
            return await orig.originate(to=NUMBER, from_=FROM,
                                        twiml_url=f"{BASE}/twilio/twiml/x", status_url=f"{BASE}/twilio/status/x")

    sid = asyncio.run(run())
    assert sid == "CA99999999999999999999999999999999"
    assert seen["url"].endswith("/2010-04-01/Accounts/ACtest00000000000000000000000000/Calls.json")
    assert seen["auth"].startswith("Basic ")
    assert "To=%2B14155550123" in seen["body"]            # the pick, url-encoded
    assert "From=%2B14155550100" in seen["body"]
    assert "Url=" in seen["body"] and "StatusCallback=" in seen["body"]


def test_dial_request_carries_the_detection_settings():
    """Spec 0080, A1 and A2: synchronous machine detection with a 4.5 s speech threshold (a greeting
    up to about 4 s is a person) and the end-of-speech and silence waits sent explicitly at their
    defaults. No asynchronous detection and no overall detection timeout are sent."""
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = request.content.decode()
        return httpx.Response(201, json={"sid": "CA99999999999999999999999999999999", "status": "queued"})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            orig = TwilioOriginator(http, account_sid="ACtest00000000000000000000000000", auth_token=b"secret")
            return await orig.originate(to=NUMBER, from_=FROM,
                                        twiml_url=f"{BASE}/twilio/twiml/x", status_url=f"{BASE}/twilio/status/x")

    asyncio.run(run())
    form = urllib.parse.parse_qs(seen["body"], keep_blank_values=True)
    detection = {k: v for k, v in form.items() if k.startswith("MachineDetection")}
    assert detection == {
        "MachineDetection": ["Enable"],
        "MachineDetectionSpeechThreshold": ["4500"],
        "MachineDetectionSpeechEndThreshold": ["1200"],
        "MachineDetectionSilenceTimeout": ["2500"],           # spec 0080, Part C (C1): was 5000
    }                                                       # so no MachineDetectionTimeout either
    assert not [k for k in form if k.startswith("AsyncAmd")]  # detection stays synchronous (D1)
    # A2: the module's named constants hold these values; the next test shows the request is built
    # from them, not from literals.
    assert origination.AMD_SPEECH_THRESHOLD_MS == 4500
    assert origination.AMD_SPEECH_END_THRESHOLD_MS == 1200
    assert origination.AMD_SILENCE_TIMEOUT_MS == 2500


def test_dial_request_uses_the_named_detection_constants(monkeypatch):
    """Spec 0080, A2: changing a constant changes what is sent, so the request reads the constants
    (one place to tune) rather than literals that could drift from them."""
    monkeypatch.setattr(origination, "AMD_SPEECH_THRESHOLD_MS", 4400)
    monkeypatch.setattr(origination, "AMD_SPEECH_END_THRESHOLD_MS", 1300)
    monkeypatch.setattr(origination, "AMD_SILENCE_TIMEOUT_MS", 4900)
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = request.content.decode()
        return httpx.Response(201, json={"sid": "CA99999999999999999999999999999999", "status": "queued"})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            orig = TwilioOriginator(http, account_sid="ACtest00000000000000000000000000", auth_token=b"secret")
            return await orig.originate(to=NUMBER, from_=FROM,
                                        twiml_url=f"{BASE}/twilio/twiml/x", status_url=f"{BASE}/twilio/status/x")

    asyncio.run(run())
    form = urllib.parse.parse_qs(seen["body"], keep_blank_values=True)
    assert form["MachineDetectionSpeechThreshold"] == ["4400"]
    assert form["MachineDetectionSpeechEndThreshold"] == ["1300"]
    assert form["MachineDetectionSilenceTimeout"] == ["4900"]
