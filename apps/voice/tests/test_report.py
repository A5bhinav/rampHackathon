"""The report to the core (internal.yaml, operation reportCallEnded): when a call ends, the voice
service POSTs the result to the core. These tests drive the client against an httpx MockTransport,
so they need no running core.

Fail-closed is the point (R7): if the core cannot be reached, the client gives up after retrying
and raises, so the caller leaves the case `calling` and the core's own call_timeout sweeps it to a
person. A 409 right after placing the call (the core has not appended call_started yet) is a race,
so it is retried (spec 0012). A 401 or 422 is our own bug and is not retried."""
from __future__ import annotations

import asyncio
import uuid

import httpx
import pytest
from dialback_voice.report import CoreClient, CoreRejectedReport, CoreUnavailable

TOKEN = b"internal-service-token-for-tests-0123456789"
CASE = uuid.UUID("c0ffee00-1111-4222-8333-444455556666")
CALL = uuid.UUID("6b1e2f0a-4c3d-4e5f-8a9b-0c1d2e3f4a5b")
BODY = {"call_id": str(CALL), "case_id": str(CASE), "result": "no_answer", "attempt": 1}


class Recorder:
    """Captures each request and replies with a scripted sequence of statuses; a status given as an
    exception type is raised instead, to stand in for the core being unreachable."""

    def __init__(self, script):
        self.script = list(script)
        self.requests: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        step = self.script[min(len(self.requests) - 1, len(self.script) - 1)]
        if isinstance(step, type) and issubclass(step, Exception):
            raise step("core unreachable")
        status, body = step if isinstance(step, tuple) else (step, None)
        return httpx.Response(status, json=body)


def _run(script, *, max_attempts=5):
    rec = Recorder(script)
    slept: list[float] = []

    async def go():
        async def sleep(delay):
            slept.append(delay)
        transport = httpx.MockTransport(rec.handler)
        async with httpx.AsyncClient(transport=transport) as http:
            client = CoreClient("http://core", TOKEN, http, max_attempts=max_attempts, sleep=sleep)
            return await client.report_call_ended(BODY)

    return rec, slept, go


def test_a_204_is_a_delivered_report():
    rec, slept, go = _run([204])
    asyncio.run(go())  # no raise: delivered
    assert len(rec.requests) == 1
    assert slept == []


def test_a_200_already_applied_is_delivered():
    rec, _slept, go = _run([(200, {"status": "already_applied"})])
    asyncio.run(go())
    assert len(rec.requests) == 1


def test_the_request_matches_the_contract():
    rec, _, go = _run([204])
    asyncio.run(go())
    req = rec.requests[0]
    assert req.method == "POST"
    assert req.url.path == f"/internal/calls/{CALL}/ended"
    assert req.headers["Authorization"] == f"Bearer {TOKEN.decode()}"
    assert req.headers["Idempotency-Key"] == f"{CASE}:call:{CALL}:ended"
    import json
    assert json.loads(req.content) == BODY


def test_a_409_before_call_started_is_retried_then_succeeds():
    rec, slept, go = _run([409, 204])
    asyncio.run(go())  # the race clears on retry
    assert len(rec.requests) == 2
    assert len(slept) == 1


def test_a_5xx_is_retried_then_succeeds():
    rec, _slept, go = _run([503, 503, 204])
    asyncio.run(go())
    assert len(rec.requests) == 3


def test_the_core_unreachable_raises_core_unavailable_after_retries():
    rec, slept, go = _run([httpx.ConnectError], max_attempts=4)
    with pytest.raises(CoreUnavailable):
        asyncio.run(go())
    assert len(rec.requests) == 4  # it tried max_attempts times
    assert len(slept) == 3         # and backed off between tries


def test_persistent_409_gives_up_as_core_unavailable():
    rec, _, go = _run([409], max_attempts=3)
    with pytest.raises(CoreUnavailable):
        asyncio.run(go())
    assert len(rec.requests) == 3


def test_a_401_is_not_retried():
    rec, slept, go = _run([(401, {"code": "unauthorized", "message": "no"})])
    with pytest.raises(CoreRejectedReport):
        asyncio.run(go())
    assert len(rec.requests) == 1  # our token is wrong; retrying cannot help
    assert slept == []


def test_a_422_is_not_retried():
    rec, _, go = _run([(422, {"code": "invalid_request", "message": "bad"})])
    with pytest.raises(CoreRejectedReport):
        asyncio.run(go())
    assert len(rec.requests) == 1
