"""The voice service entrypoint (spec 0016). `build_app` wires `create_app` from `Settings` and the
injected store, Twilio deps and model: text mode serves the call endpoints with no Twilio routes and
authenticates `POST /calls`; live mode mounts the Twilio routes. `create_asgi_app` assembles the app
from injectable store and lookup factories. All driven with the in-memory store double and a fake
lookup, no Postgres, no live account, no key. The real store and pool are the ledger seam."""
from __future__ import annotations

import uuid

import httpx
import pytest
from dialback_voice.entrypoint import _ledger_lookup, _ledger_store, build_app, create_asgi_app
from dialback_voice.guards import PreDialInputs
from dialback_voice.settings import Settings, TwilioConfig
from dialback_voice.store import PreDial
from fastapi.testclient import TestClient

TOKEN = b"internal-service-token-for-tests"
TOKEN_STR = TOKEN.decode()
CFG = TwilioConfig(account_sid="ACtest00000000000000000000000000", auth_token=b"auth",
                   from_number="+14155550100", call_token_key=b"key", core_url="https://core.internal",
                   internal_token=TOKEN, public_base_url="https://voice.example",
                   ws_base_url="wss://voice.example")


class _Store:
    """The minimum POST /calls touches before it refuses: no prior call, and a pre-dial with no number
    pick, so a request that gets past auth returns 409 number_not_selected (fail closed)."""

    async def find_call(self, key):
        return None

    async def gather_pre_dial(self, *, case_id, attempt):
        inputs = PreDialInputs(case_exists=True, case_finished=False, case_held=True, has_number_pick=False,
                               number_opted_out=False, within_calling_window=True, attempts_used=0,
                               number_calls_last_30d=0, demo_dataset=False, number_on_allowlist=True)
        return PreDial(inputs=inputs, contact_point_id=None, number_e164=None)

    async def record_call(self, **kwargs):
        raise AssertionError("record_call should not be reached when there is no number pick")

    async def mark_dialed(self, *a, **k):
        raise AssertionError

    async def mark_failed(self, *a, **k):
        raise AssertionError


def _text_app() -> TestClient:
    settings = Settings(voice_mode="text", internal_api_token=TOKEN)
    app = build_app(settings, store=_Store(), http=httpx.AsyncClient(), clock=lambda: 0.0,
                    lookup_call=lambda c: None)
    return TestClient(app)


def _post(client, *, token=TOKEN_STR):
    return client.post("/calls", json={"case_id": str(uuid.uuid4()), "attempt": 1},
                       headers={"Idempotency-Key": "k1", "Authorization": f"Bearer {token}"})


def test_text_mode_serves_calls_and_healthz_no_twilio_routes():
    client = _text_app()
    assert client.get("/healthz").json() == {"ok": True}
    assert client.get(f"/twilio/twiml/{uuid.uuid4()}").status_code == 404   # no Twilio routes in text mode


def test_post_calls_needs_the_internal_token():
    client = _text_app()
    assert _post(client, token="wrong").status_code == 401            # the token is wired from settings
    r = _post(client)                                                 # right token gets past auth
    assert r.status_code == 409 and r.json()["code"] == "number_not_selected"


def test_live_mode_mounts_the_twilio_routes():
    settings = Settings(voice_mode="live", twilio=CFG, internal_api_token=TOKEN)   # no agent key: ScriptedAgent
    app = build_app(settings, store=_Store(), http=httpx.AsyncClient(), clock=lambda: 0.0,
                    lookup_call=lambda c: None)
    client = TestClient(app)
    # the route is mounted: a bad (missing) Twilio signature is 401, not a 404 for an absent route
    assert client.get(f"/twilio/twiml/{uuid.uuid4()}").status_code == 401


def test_create_asgi_app_wires_injected_factories():
    store = _Store()
    app = create_asgi_app(Settings(voice_mode="text", internal_api_token=TOKEN),
                          store_for=lambda: store, lookup_for=lambda: (lambda c: None),
                          http=httpx.AsyncClient(), clock=lambda: 0.0)
    assert TestClient(app).get("/healthz").json() == {"ok": True}


def test_the_ledger_seam_factories_raise_fail_closed():
    # The default store and lookup factories are the ledger seam (Q40/Q50): they must raise, not hand
    # back a broken default, so a deploy without the real store cannot boot (R7).
    with pytest.raises(NotImplementedError):
        _ledger_store()
    with pytest.raises(NotImplementedError):
        _ledger_lookup()


def test_create_asgi_app_with_default_factories_raises_on_boot():
    # With no factories injected, create_asgi_app falls to the ledger seam and raises rather than
    # serving a half-wired app (R7). A regression to a silent default would let it start.
    with pytest.raises(NotImplementedError):
        create_asgi_app(Settings(voice_mode="text", internal_api_token=TOKEN))
