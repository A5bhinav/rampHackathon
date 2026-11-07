"""The report to the core (internal.yaml, operation reportCallEnded). When a call ends, whether or
not anyone answered, the voice service POSTs the result to the core at
`/internal/calls/{call_id}/ended`. The core compares any spoken digits with the case in memory,
appends `call_ended` to the evidence chain, and moves the case on; this service never learns the
comparison and never stores the digits (spec 0002, D7).

Delivery fails closed (R7). The core being unreachable, a 5xx, or a 409 right after the call was
placed (the core has not appended `call_started` yet, a race) are all retried with backoff. If the
report still will not land, the client raises `CoreUnavailable`: the caller leaves the case
`calling`, and the core's own `call_timeout` later sweeps it to a person, so a dropped report never
silently loses a call. A 401 or 422 is this service's own bug (a bad token or a malformed body) and
is not retried; it raises `CoreRejectedReport` so the fault is seen, not buried under retries."""
from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

log = logging.getLogger(__name__)


class CoreUnavailable(Exception):
    """The report did not land after every retry. The case stays `calling` and the core's
    call_timeout sweeps it to a person (R7)."""


class CoreRejectedReport(Exception):
    """The core refused the report for a reason retrying cannot fix (401 or 422): our token or our
    body is wrong. Surfaced so the bug is fixed, never retried into silence."""


async def _asyncio_sleep(delay: float) -> None:
    import asyncio
    await asyncio.sleep(delay)


class CoreClient:
    """POST {core}/internal/calls/{call_id}/ended. Takes the full CallEndedRequest body; the path
    and the Idempotency-Key are built from its required call_id and case_id (internal.yaml)."""

    def __init__(self, base_url: str, token: bytes, http: httpx.AsyncClient, *,
                 max_attempts: int = 5, base_delay: float = 0.5, max_delay: float = 8.0,
                 sleep: Callable[[float], Awaitable[None]] = _asyncio_sleep) -> None:
        self.base_url = base_url.rstrip("/")
        self._token = token.decode()
        self._http = http
        self._max_attempts = max_attempts
        self._base_delay, self._max_delay = base_delay, max_delay
        self._sleep = sleep

    def _backoff(self, attempt: int) -> float:
        return min(self._base_delay * (2 ** (attempt - 1)), self._max_delay)

    async def report_call_ended(self, body: dict[str, Any]) -> None:
        call_id, case_id = body["call_id"], body["case_id"]
        url = f"{self.base_url}/internal/calls/{call_id}/ended"
        headers = {"Authorization": f"Bearer {self._token}",
                   "Idempotency-Key": f"{case_id}:call:{call_id}:ended"}  # internal.yaml
        reason = "no attempt was made"
        for attempt in range(1, self._max_attempts + 1):
            try:
                r = await self._http.post(url, json=body, headers=headers)
            except httpx.TransportError as exc:
                reason = f"unreachable ({type(exc).__name__})"
            else:
                if r.status_code in (200, 204):
                    return  # delivered (204) or already applied (200)
                if r.status_code in (401, 422):
                    raise CoreRejectedReport(f"the core refused the report: HTTP {r.status_code}")
                if r.status_code == 409 or r.status_code >= 500:
                    reason = f"HTTP {r.status_code}"  # a race or a server fault: worth another try
                else:
                    raise CoreRejectedReport(f"the core answered an unexpected HTTP {r.status_code}")
            if attempt < self._max_attempts:
                log.info("report for call %s not yet accepted (%s); retrying", call_id, reason)
                await self._sleep(self._backoff(attempt))
        raise CoreUnavailable(f"the core did not accept the report for call {call_id} "
                              f"after {self._max_attempts} tries ({reason})")
