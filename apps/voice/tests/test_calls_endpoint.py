"""POST /calls (internal.yaml, operation placeCall): the core asks the voice service to call a
case's chosen number. The request carries only {case_id, attempt}; the voice service reads the
number itself, so nothing in the request can change who gets called (spec 0012, decision 8).

These tests drive the endpoint against an in-memory store, so they need no database: the pre-dial
facts the store would read from Postgres are set directly. The real psycopg store is the thin
adapter exercised by the psql-backed CI test (spec 0012, test_calls.py)."""
from __future__ import annotations

import uuid

from dialback_voice.app import create_app
from dialback_voice.guards import PreDialInputs
from dialback_voice.store import PlacedCall, PreDial
from fastapi.testclient import TestClient

TOKEN = b"internal-service-token-for-tests-0123456789"
CASE = uuid.UUID("c0ffee00-1111-4222-8333-444455556666")
PICK = uuid.UUID("a1a2a3a4-b1b2-4c3c-8d4d-e5e6e7e8e9ea")
NUMBER = "+14155550123"


class FakeStore:
    """A stand-in for the Postgres-backed store. `pre_dial` is what it would read for the case;
    recorded calls are keyed by their Idempotency-Key, so a replay returns the first call."""

    def __init__(self, pre_dial: PreDial) -> None:
        self._pre_dial = pre_dial
        self.by_key: dict[str, PlacedCall] = {}
        self.gather_count = 0

    async def find_call(self, idempotency_key: str) -> PlacedCall | None:
        return self.by_key.get(idempotency_key)

    async def gather_pre_dial(self, *, case_id: uuid.UUID, attempt: int) -> PreDial:
        self.gather_count += 1
        return self._pre_dial

    async def record_call(self, *, case_id, attempt, idempotency_key, contact_point_id,
                          number_e164, agent_version) -> tuple[PlacedCall, bool]:
        if idempotency_key in self.by_key:
            return self.by_key[idempotency_key], False
        placed = PlacedCall(call_id=uuid.uuid4(), case_id=case_id, attempt=attempt)
        self.by_key[idempotency_key] = placed
        return placed, True


def _pre_dial(**over) -> PreDial:
    base = {"case_exists": True, "case_finished": False, "case_held": True, "has_number_pick": True,
            "number_opted_out": False, "within_calling_window": True, "attempts_used": 0,
            "number_calls_last_30d": 0, "demo_dataset": False, "number_on_allowlist": True}
    return PreDial(inputs=PreDialInputs(**(base | over)), contact_point_id=PICK, number_e164=NUMBER)


def _client(store: FakeStore) -> TestClient:
    app = create_app(store=store, internal_api_token=TOKEN, agent_version="agent-test-0")
    return TestClient(app)


def _post(client: TestClient, *, case_id=CASE, attempt=1, key="k1", token=TOKEN):
    headers = {"Idempotency-Key": key}
    if token is not None:
        headers["Authorization"] = f"Bearer {token.decode()}"
    return client.post("/calls", json={"case_id": str(case_id), "attempt": attempt}, headers=headers)


def test_a_dialable_case_is_accepted_with_202_and_a_call_id():
    store = FakeStore(_pre_dial())
    r = _post(_client(store), attempt=2)
    assert r.status_code == 202
    body = r.json()
    assert body["case_id"] == str(CASE)
    assert body["attempt"] == 2
    uuid.UUID(body["call_id"])  # a real call id was minted
    assert len(store.by_key) == 1  # and a call was recorded


def test_each_pre_dial_refusal_returns_409_with_its_code():
    for over, code in [({"case_held": False}, "case_not_held"),
                       ({"has_number_pick": False}, "number_not_selected"),
                       ({"number_opted_out": True}, "number_opted_out"),
                       ({"within_calling_window": False}, "outside_calling_window"),
                       ({"attempts_used": 3}, "attempt_cap_reached"),
                       ({"number_calls_last_30d": 3}, "number_cap_reached"),
                       ({"demo_dataset": True, "number_on_allowlist": False}, "not_on_dial_allowlist")]:
        store = FakeStore(_pre_dial(**over))
        r = _post(_client(store), key=code)
        assert r.status_code == 409, (over, r.status_code, r.text)
        assert r.json()["code"] == code
        assert r.json()["message"]  # a human-readable reason, never empty
        assert store.by_key == {}  # a refused call is never recorded


def test_a_refused_call_records_nothing():
    store = FakeStore(_pre_dial(has_number_pick=False))
    _post(_client(store))
    assert store.by_key == {}


def test_a_replayed_idempotency_key_returns_200_with_the_same_call_id():
    store = FakeStore(_pre_dial())
    client = _client(store)
    first = _post(client, key="same")
    second = _post(client, key="same")
    assert first.status_code == 202
    assert second.status_code == 200
    assert first.json()["call_id"] == second.json()["call_id"]
    assert len(store.by_key) == 1  # the call was placed once, not twice
    assert store.gather_count == 1  # a replay does not re-check pre-dial


def test_a_missing_service_token_is_401_and_touches_nothing():
    store = FakeStore(_pre_dial())
    r = _post(_client(store), token=None)
    assert r.status_code == 401
    assert r.json()["code"] == "unauthorized"
    assert store.gather_count == 0 and store.by_key == {}  # unauthenticated callers learn nothing


def test_a_wrong_service_token_is_401():
    store = FakeStore(_pre_dial())
    r = _post(_client(store), token=b"not-the-service-token-at-all-000000")
    assert r.status_code == 401


def test_a_malformed_request_is_422():
    store = FakeStore(_pre_dial())
    client = _client(store)
    headers = {"Idempotency-Key": "k", "Authorization": f"Bearer {TOKEN.decode()}"}
    too_high = client.post("/calls", json={"case_id": str(CASE), "attempt": 4}, headers=headers)
    assert too_high.status_code == 422
    extra = client.post("/calls", json={"case_id": str(CASE), "attempt": 1, "number": "+1"}, headers=headers)
    assert extra.status_code == 422
    missing_key = client.post("/calls", json={"case_id": str(CASE), "attempt": 1},
                              headers={"Authorization": f"Bearer {TOKEN.decode()}"})
    assert missing_key.status_code == 422
    assert store.by_key == {}
