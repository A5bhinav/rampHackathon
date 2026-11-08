"""POST /calls dials the Trust Ledger's pick over Twilio when live (spec 0014).

The endpoint is driven against an in-memory store and a fake Originator, so no live account is
touched; the TwilioOriginator REST client is unit-tested on its own against an httpx MockTransport.
In text mode (twilio=None) nothing dials, which the existing POST /calls tests already cover."""
from __future__ import annotations

import asyncio
import uuid

import httpx
from dialback_voice.app import TwilioDeps, create_app
from dialback_voice.guards import PreDialInputs
from dialback_voice.store import PlacedCall, PreDial
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

    def __init__(self, pre_dial: PreDial):
        self._pre_dial = pre_dial
        self.by_key: dict[str, PlacedCall] = {}
        self.dialed: dict[uuid.UUID, str] = {}
        self.failed: dict[uuid.UUID, str] = {}

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
        self.dialed[call_id] = provider_call_id

    async def mark_failed(self, call_id, reason):
        self.failed[call_id] = reason


def _pre_dial(**over) -> PreDial:
    base = {"case_exists": True, "case_finished": False, "case_held": True, "has_number_pick": True,
            "number_opted_out": False, "within_calling_window": True, "attempts_used": 0,
            "number_calls_last_30d": 0, "demo_dataset": False, "number_on_allowlist": True}
    return PreDial(inputs=PreDialInputs(**(base | over)), contact_point_id=PICK, number_e164=NUMBER)


def _deps(originator, reports: list) -> TwilioDeps:
    async def report_ended(body):
        reports.append(body)
    return TwilioDeps(call_token_key=b"k", twilio_auth_token=b"auth", ws_base_url="wss://voice.example",
                      public_base_url=BASE, from_number=FROM, originator=originator,
                      lookup_call=lambda cid: None, report_ended=report_ended,
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
    assert store.failed[uuid.UUID(call_id)] == "originate_failed"
    assert store.dialed == {}            # never recorded as dialed


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
