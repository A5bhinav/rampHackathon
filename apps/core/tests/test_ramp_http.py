"""The Ramp adapter's failure handling, without a network."""
from __future__ import annotations

import httpx
import pytest
from dialback_core.flags import RampMode, ResolverMode, read_flag
from dialback_core.ramp import RampError, RampNotFound
from dialback_core.ramp.http import RampHttpAdapter, TokenBucket

pytestmark = pytest.mark.anyio


def adapter(handler, sleeps: list[float]) -> RampHttpAdapter:
    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return RampHttpAdapter("https://ramp.test", "tok", http, TokenBucket(1000, sleep=fake_sleep), max_retries=2, sleep=fake_sleep)


VENDOR = {"id": "v1", "name": "Acme", "status": "ACTIVE", "updated_at": "2026-11-01T00:00:00Z", "hold": None}


async def test_retries_server_errors_then_gives_up_as_retryable():
    calls, sleeps = [], []
    a = adapter(lambda r: calls.append(r) or httpx.Response(503), sleeps)
    with pytest.raises(RampError) as err:
        await a.get_vendor("v1")
    assert err.value.retryable and err.value.status == 503 and len(calls) == 3 and len(sleeps) == 2


async def test_waits_for_retry_after_on_429_then_succeeds():
    responses = [httpx.Response(429, headers={"Retry-After": "3"}), httpx.Response(200, json=VENDOR)]
    sleeps: list[float] = []
    a = adapter(lambda r: responses.pop(0), sleeps)
    assert (await a.get_vendor("v1")).name == "Acme" and sleeps == [3.0]


async def test_client_errors_are_final_and_404_is_not_found():
    calls, sleeps = [], []
    a = adapter(lambda r: calls.append(r) or httpx.Response(403), sleeps)
    with pytest.raises(RampError) as err:
        await a.get_vendor("v1")
    assert not err.value.retryable and len(calls) == 1
    with pytest.raises(RampNotFound):
        await adapter(lambda r: httpx.Response(404), []).get_vendor("v1")


async def test_never_follows_a_page_link_to_another_host():
    page = {"data": [VENDOR], "page": {"next": "https://evil.test/developer/v1/vendors?start=v1"}}
    a = adapter(lambda r: httpx.Response(200, json=page), [])
    with pytest.raises(RampError, match="another host"):
        await a.list_vendors_updated_since(None)


async def test_sends_the_bearer_token():
    seen = []
    a = adapter(lambda r: seen.append(r.headers["Authorization"]) or httpx.Response(200, json=VENDOR), [])
    await a.get_vendor("v1")
    assert seen == ["Bearer tok"]


def test_unknown_flag_values_fall_back_to_the_safe_path():
    assert read_flag("DIALBACK_RAMP", RampMode, RampMode.MOCK, {"DIALBACK_RAMP": "production!"}) is RampMode.MOCK
    assert read_flag("DIALBACK_RAMP", RampMode, RampMode.MOCK, {"DIALBACK_RAMP": "Sandbox"}) is RampMode.SANDBOX
    assert read_flag("DIALBACK_RESOLVER", ResolverMode, ResolverMode.STUB, {}) is ResolverMode.STUB
