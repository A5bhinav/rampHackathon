"""Spec 0096: each dataset picks the Ramp mock or Ramp's sandbox, and the core logs in to Ramp.

A fake Ramp stands in for both the token endpoint and the API, and records every request, so each
test can say exactly what was sent where. Expected headers and URLs are written out by hand.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import logging
import uuid
from urllib.parse import parse_qs

import httpx
import pytest
from conftest import MOCK_SECRET, core_settings
from dialback_core.datasets import create_dataset
from dialback_core.db import open_pool, system_tx
from dialback_core.flags import RampMode
from dialback_core.main import _new_dataset
from dialback_core.ramp.connector import MockRefused, RampConnector
from dialback_core.ramp.http import RampHttpAdapter, TokenBucket
from dialback_core.ramp.login import RampLogin
from dialback_core.ramp.port import RampError
from dialback_core.settings import ConfigurationError, RampSandbox, Settings

SECRET = "sandbox-secret-for-tests-only"
SANDBOX = RampSandbox(client_id="client-for-tests", client_secret=SECRET.encode(), base_url="https://sandbox.ramp.test",
                      scopes=("vendors:read", "bills:read"))
FIVE_SCOPES = ("vendors:read", "vendors:write", "bills:read", "bills:write", "audit_logs:read")


class FakeRamp:
    """Ramp's token endpoint and API in one: records every request; the statuses can be set."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.token_status = 200
        self.api_status = 200
        self.issued = 0
        self.delay = 0.0

    async def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.path == "/developer/v1/token":
            if self.token_status != 200:  # a body that echoes the secret, to prove it never reaches an error
                return httpx.Response(self.token_status, json={"error_v2": {"message": f"bad client {SECRET}"}})
            self.issued += 1
            await asyncio.sleep(self.delay)
            return httpx.Response(200, json={"access_token": f"test-token-{self.issued}", "token_type": "Bearer",
                                             "expires_in": 864000, "scope": "vendors:read bills:read"})
        if self.api_status != 200:
            return httpx.Response(self.api_status, json={})
        return httpx.Response(200, json={"id": "v1", "name": "Acme Fasteners LLC"})

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self.handle))

    def api_requests(self) -> list[httpx.Request]:
        return [r for r in self.requests if r.url.path != "/developer/v1/token"]


async def _no_sleep(_: float) -> None:
    pass


def connector(settings: Settings, http: httpx.AsyncClient, pool) -> RampConnector:
    return RampConnector(settings, http, limiter=TokenBucket(100_000), sleep=_no_sleep, pool=pool)


async def sandbox_dataset(pool, kind: str = "dev"):
    """One current sandbox dataset per database (D1), so any earlier one is set aside first."""
    async with system_tx(pool) as conn:
        await conn.execute("update app.datasets set retired_at = now() where ramp = 'sandbox' and retired_at is null")
    return await create_dataset(pool, kind, f"test-{uuid.uuid4().hex[:8]}", ramp="sandbox")


# Settings (D2) ------------------------------------------------------------------------------------

BASE_ENV = {"DATABASE_URL": "postgresql://x", "RAMP_BASE_URL": "http://mock", "MOCK_RAMP_SECRET": "m" * 16,
            "RAMP_WEBHOOK_SECRET": "w" * 16, "FINGERPRINT_KEY": "f" * 32, "DIGITS_KEY": "d" * 32,
            "INTERNAL_API_TOKEN": "i" * 24, "DIALBACK_JWT_SECRET": "s" * 32}
CREDS = {"RAMP_SANDBOX_CLIENT_ID": "client-for-tests", "RAMP_SANDBOX_CLIENT_SECRET": "x" * 24}


def test_settings_read_the_sandbox_credentials():
    s = Settings.from_env({**BASE_ENV, **CREDS})
    assert s.ramp_sandbox == RampSandbox("client-for-tests", b"x" * 24, "https://demo-api.ramp.com", FIVE_SCOPES)
    assert Settings.from_env(BASE_ENV).ramp_sandbox is None, "no credentials, no sandbox"
    s = Settings.from_env({**BASE_ENV, **CREDS, "RAMP_SANDBOX_BASE_URL": "https://demo-api.ramp.com/",
                           "RAMP_SANDBOX_SCOPES": " vendors:read  bills:read "})
    assert s.ramp_sandbox.base_url == "https://demo-api.ramp.com" and s.ramp_sandbox.scopes == ("vendors:read", "bills:read")


@pytest.mark.parametrize("missing", ["RAMP_SANDBOX_CLIENT_ID", "RAMP_SANDBOX_CLIENT_SECRET"])
def test_settings_refuse_one_credential_without_the_other(missing):
    env = {k: v for k, v in {**BASE_ENV, **CREDS}.items() if k != missing}
    with pytest.raises(ConfigurationError, match="RAMP_SANDBOX_CLIENT"):
        Settings.from_env(env)


@pytest.mark.parametrize("url", ["https://api.ramp.com", "http://demo-api.ramp.com", "https://demo-api.ramp.com.evil.example",
                                 "https://demo-api.ramp.com/developer/v1", "https://user@demo-api.ramp.com"],
                         ids=["production", "http", "lookalike-host", "with-path", "userinfo"])
def test_settings_refuse_any_address_but_ramps_sandbox(url):
    with pytest.raises(ConfigurationError, match="RAMP_SANDBOX_BASE_URL"):
        Settings.from_env({**BASE_ENV, **CREDS, "RAMP_SANDBOX_BASE_URL": url})


def test_settings_refuse_dialback_ramp_sandbox_without_credentials():
    with pytest.raises(ConfigurationError, match="RAMP_SANDBOX_CLIENT"):
        Settings.from_env({**BASE_ENV, "DIALBACK_RAMP": "sandbox"})
    no_mock = {k: v for k, v in BASE_ENV.items() if k not in ("MOCK_RAMP_SECRET", "RAMP_BASE_URL")}
    s = Settings.from_env({**no_mock, **CREDS, "DIALBACK_RAMP": "sandbox"})
    assert s.ramp_mode is RampMode.SANDBOX and s.mock_ramp_secret is None and s.ramp_sandbox is not None


# The login (D4) ------------------------------------------------------------------------------------

@pytest.mark.anyio
async def test_login_sends_basic_auth_and_the_form():
    ramp = FakeRamp()
    async with ramp.client() as http:
        assert await RampLogin(SANDBOX, http).token() == "test-token-1"
    [r] = ramp.requests
    assert r.method == "POST" and str(r.url) == "https://sandbox.ramp.test/developer/v1/token"
    assert r.headers["Authorization"] == "Basic " + base64.b64encode(b"client-for-tests:" + SECRET.encode()).decode()
    assert r.headers["Content-Type"] == "application/x-www-form-urlencoded"
    assert parse_qs(r.content.decode()) == {"grant_type": ["client_credentials"], "scope": ["vendors:read bills:read"]}


@pytest.mark.anyio
async def test_login_keeps_the_token_and_renews_it_five_minutes_before_it_expires():
    ramp, now = FakeRamp(), [1000.0]
    async with ramp.client() as http:
        login = RampLogin(SANDBOX, http, clock=lambda: now[0])
        assert await login.token() == "test-token-1"
        now[0] = 1000.0 + 864000 - 301  # just over five minutes left
        assert await login.token() == "test-token-1"
        now[0] += 2  # just under
        assert await login.token() == "test-token-2"
    assert ramp.issued == 2


@pytest.mark.anyio
async def test_login_renews_once_under_concurrent_calls():
    ramp = FakeRamp()
    ramp.delay = 0.05
    async with ramp.client() as http:
        login = RampLogin(SANDBOX, http)
        assert await asyncio.gather(*(login.token() for _ in range(5))) == ["test-token-1"] * 5
    assert ramp.issued == 1


@pytest.mark.anyio
@pytest.mark.parametrize(("status", "retryable"), [(400, False), (401, False), (429, True), (500, True), (503, True)])
async def test_login_failures_are_retryable_only_when_ramp_may_answer_later(status, retryable):
    ramp = FakeRamp()
    ramp.token_status = status
    async with ramp.client() as http:
        with pytest.raises(RampError) as err:
            await RampLogin(SANDBOX, http).token()
    assert err.value.retryable is retryable


@pytest.mark.anyio
async def test_a_login_that_cannot_reach_ramp_is_retryable():
    def unreachable(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(unreachable)) as http:
        with pytest.raises(RampError) as err:
            await RampLogin(SANDBOX, http).token()
    assert err.value.retryable


@pytest.mark.anyio
async def test_a_401_from_ramp_drops_the_token_so_the_next_try_logs_in_again(caplog):
    ramp = FakeRamp()
    async with ramp.client() as http:
        adapter = RampHttpAdapter(SANDBOX.base_url, RampLogin(SANDBOX, http), http, TokenBucket(100_000), sleep=_no_sleep)
        assert (await adapter.get_vendor("v1")).name == "Acme Fasteners LLC"
        ramp.api_status = 401
        with caplog.at_level(logging.DEBUG), pytest.raises(RampError) as err:
            await adapter.get_vendor("v1")
        assert err.value.retryable
        ramp.api_status = 200
        await adapter.get_vendor("v1")
    assert ramp.issued == 2
    first, refused, after = ramp.api_requests()
    assert first.headers["Authorization"] == "Bearer test-token-1" == refused.headers["Authorization"]
    assert after.headers["Authorization"] == "Bearer test-token-2"
    assert "test-token" not in str(err.value) + caplog.text


@pytest.mark.anyio
async def test_the_secret_never_reaches_an_error_or_a_log(caplog):
    ramp = FakeRamp()
    ramp.token_status = 400  # its body echoes the secret
    async with ramp.client() as http:
        with caplog.at_level(logging.DEBUG), pytest.raises(RampError) as err:
            await RampLogin(SANDBOX, http).token()
    text = str(err.value) + repr(err.value) + caplog.text
    assert SECRET not in text and base64.b64encode(SECRET.encode()).decode() not in text


# Making a dataset (D1, D5) ------------------------------------------------------------------------

@pytest.mark.anyio
async def test_create_dataset_stores_its_ramp(pool):
    d = await sandbox_dataset(pool)
    m = await create_dataset(pool, "dev", f"test-{uuid.uuid4().hex[:8]}")
    async with system_tx(pool) as conn:
        rows = await (await conn.execute("select id, ramp::text as ramp from app.datasets where id in (%s, %s)",
                                         (d.id, m.id))).fetchall()
    assert {r["id"]: r["ramp"] for r in rows} == {d.id: "sandbox", m.id: "mock"}
    assert (d.ramp, m.ramp) == ("sandbox", "mock")


def test_new_dataset_refuses_a_demo_on_the_sandbox():
    with pytest.raises(SystemExit, match="demo"):
        asyncio.run(_new_dataset(core_settings(ramp_sandbox=SANDBOX), "demo", "Demo", None, "sandbox"))


def test_new_dataset_refuses_the_sandbox_without_credentials():
    with pytest.raises(SystemExit, match="RAMP_SANDBOX_CLIENT"):
        asyncio.run(_new_dataset(core_settings(), "dev", "Dev", None, "sandbox"))


# Routing (D3) --------------------------------------------------------------------------------------

@pytest.mark.anyio
async def test_routing_sends_a_mock_dataset_to_the_mock_with_its_own_token(pool):
    ramp = FakeRamp()
    d = await create_dataset(pool, "dev", f"test-{uuid.uuid4().hex[:8]}")
    async with ramp.client() as http:
        await connector(core_settings(ramp_sandbox=SANDBOX), http, pool).for_dataset(d.id).get_vendor("v1")
    [r] = ramp.requests
    assert str(r.url) == "http://ramp-mock/developer/v1/vendors/v1"
    mac = hmac.new(MOCK_SECRET, str(d.id).encode(), hashlib.sha256).hexdigest()
    assert r.headers["Authorization"] == f"Bearer mock.{d.id}.{mac}"


@pytest.mark.anyio
async def test_routing_sends_a_sandbox_dataset_to_the_sandbox_with_the_login(pool):
    ramp = FakeRamp()
    d = await sandbox_dataset(pool)
    async with ramp.client() as http:
        await connector(core_settings(ramp_sandbox=SANDBOX), http, pool).for_dataset(d.id).get_vendor("v1")
    # Spec 0097 adds a third request after these two: the vendor's hold, from Ramp's audit log.
    token, api = ramp.requests[:2]
    assert str(token.url) == "https://sandbox.ramp.test/developer/v1/token"
    assert str(api.url) == "https://sandbox.ramp.test/developer/v1/vendors/v1"
    assert all(r.headers["Authorization"] == "Bearer test-token-1" for r in ramp.requests[1:])


@pytest.mark.anyio
async def test_routing_refuses_a_dataset_this_server_cannot_reach(pool):
    ramp = FakeRamp()
    sandbox = await sandbox_dataset(pool)
    mock = await create_dataset(pool, "dev", f"test-{uuid.uuid4().hex[:8]}")
    no_mock = core_settings(ramp_mode=RampMode.SANDBOX, mock_ramp_secret=None, ramp_sandbox=SANDBOX)
    async with ramp.client() as http:
        cases = [(core_settings(), sandbox.id),  # no sandbox credentials
                 (no_mock, mock.id),  # DIALBACK_RAMP=sandbox: no mock
                 (core_settings(ramp_sandbox=SANDBOX), uuid.uuid4())]  # no such dataset
        for settings, dataset_id in cases:
            with pytest.raises(RampError) as err:
                await connector(settings, http, pool).for_dataset(dataset_id).get_vendor("v1")
            assert not err.value.retryable
    assert ramp.requests == [], "nothing was sent"


@pytest.mark.anyio
async def test_routing_hands_out_one_port_per_dataset(pool):
    # The worker and the tests both ask for a dataset's port; a method swapped on it (a fault, a spy)
    # must be the one the worker then calls, so each dataset has exactly one port.
    a, b = await create_dataset(pool, "dev", f"test-{uuid.uuid4().hex[:8]}"), await create_dataset(pool, "dev", f"test-{uuid.uuid4().hex[:8]}")
    async with FakeRamp().client() as http:
        c = connector(core_settings(), http, pool)
        assert c.for_dataset(a.id) is c.for_dataset(a.id)
        assert c.for_dataset(a.id) is not c.for_dataset(b.id)


@pytest.mark.anyio
async def test_routing_reads_a_datasets_ramp_once(database_url):
    ramp = FakeRamp()
    own = await open_pool(database_url, max_size=1)
    d = await create_dataset(own, "dev", f"test-{uuid.uuid4().hex[:8]}")
    async with ramp.client() as http:
        c = connector(core_settings(), http, own)
        await c.for_dataset(d.id).get_vendor("v1")
        await own.close()  # from here on there is no database to ask
        await c.for_dataset(d.id).get_vendor("v1")
    assert len(ramp.requests) == 2


@pytest.mark.anyio
async def test_the_demo_bank_change_refuses_a_sandbox_dataset(pool):
    ramp = FakeRamp()
    d = await sandbox_dataset(pool)
    async with ramp.client() as http:
        with pytest.raises(MockRefused) as err:
            await connector(core_settings(ramp_sandbox=SANDBOX), http, pool).mock_bank_change(
                d.id, "v1", "026009593", "000055556666", "u1")
    assert err.value.sent is False and ramp.requests == []
