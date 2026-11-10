"""RampHttpAdapter: Ramp's REST API, and the Ramp-shaped mock that copies it.

- A token bucket keeps Dialback under Ramp's limit (200 requests per 10 s per IP).
- 429 waits for Retry-After; 5xx and network errors back off and retry a few times; any
  other 4xx is final. Errors surface as RampError with `retryable` set, so the worker can
  decide whether to try the command again (R7: a failure only ever delays a payment).
- Pagination follows `page.next`, but never to a different host than the base URL.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import time
from collections.abc import Awaitable, Callable
from typing import Any, Protocol
from urllib.parse import urlsplit

import httpx

from .port import RampAccount, RampAuditEvent, RampBill, RampError, RampHold, RampNotFound, RampVendor

API = "/developer/v1"
Sleep = Callable[[float], Awaitable[None]]


class TokenBucket:
    """`capacity` requests per `period` seconds, refilled continuously. Shared by all datasets,
    because Ramp limits by IP."""

    def __init__(self, capacity: int, period: float = 10.0, clock: Callable[[], float] = time.monotonic,
                 sleep: Sleep = asyncio.sleep) -> None:
        self.capacity, self.rate = float(capacity), capacity / period
        self.tokens, self._clock, self._sleep = float(capacity), clock, sleep
        self._last = clock()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            while True:
                now = self._clock()
                self.tokens = min(self.capacity, self.tokens + (now - self._last) * self.rate)
                self._last = now
                if self.tokens >= 1:
                    self.tokens -= 1
                    return
                await self._sleep((1 - self.tokens) / self.rate)


def _ts(value: str | None) -> dt.datetime | None:
    return dt.datetime.fromisoformat(value) if value else None


def _date(value: str | None) -> dt.date | None:
    return dt.date.fromisoformat(value) if value else None


def vendor_from(j: dict[str, Any]) -> RampVendor:
    hold = j.get("hold")
    return RampVendor(id=j["id"], name=j["name"], status=j.get("status"), updated_at=_ts(j.get("updated_at")),
                      hold_id=hold.get("id") if isinstance(hold, dict) else None)


def account_from(j: dict[str, Any]) -> RampAccount:
    return RampAccount(id=j["id"], vendor_id=j["vendor_id"], routing_number=j["routing_number"],
                       last4=j["account_number_last4"], account_type=j.get("account_type"), bank_name=j.get("bank_name"),
                       is_active=bool(j.get("is_active", True)), created_at=_ts(j.get("created_at")))


def bill_from(j: dict[str, Any]) -> RampBill:
    return RampBill(id=j["id"], vendor_id=j["vendor_id"], payment_date=_date(j.get("payment_date")), status=j.get("status"),
                    is_on_hold=bool(j.get("is_on_hold")), paid=j.get("paid_at") is not None or j.get("status") == "PAID")


def audit_from(j: dict[str, Any]) -> RampAuditEvent:
    return RampAuditEvent(id=j["id"], event_type=j["event_type"], actor_id=j.get("actor_id"), target_id=j.get("target_id"),
                          occurred_at=_ts(j.get("occurred_at")), payload=j.get("payload") or {})


class TokenSource(Protocol):
    """A login that hands out a bearer token and forgets it when Ramp refuses it (spec 0096)."""

    async def token(self) -> str: ...
    def drop(self) -> None: ...


class RampHttpAdapter:
    def __init__(self, base_url: str, token: str | TokenSource, http: httpx.AsyncClient, limiter: TokenBucket,
                 max_retries: int = 3, sleep: Sleep = asyncio.sleep) -> None:
        self.base_url = base_url.rstrip("/")
        self._host = urlsplit(self.base_url).netloc
        self._token = token  # the mock's fixed per-dataset token, or Ramp's login
        self._http, self._limiter, self._max_retries, self._sleep = http, limiter, max_retries, sleep

    async def _headers(self) -> dict[str, str]:
        bearer = self._token if isinstance(self._token, str) else await self._token.token()
        return {"Authorization": f"Bearer {bearer}", "Accept": "application/json"}

    async def _request(self, method: str, url: str, *, empty_ok: bool = False, **kwargs: Any) -> dict[str, Any]:
        """`empty_ok`: a 202 or 204 with no body is an answer (Ramp's hold and release, spec 0097)."""
        if url.startswith("/"):
            url = self.base_url + url
        elif urlsplit(url).netloc != self._host:
            raise RampError(f"refusing to follow a link to another host: {urlsplit(url).netloc}")
        for attempt in range(self._max_retries + 1):
            await self._limiter.acquire()
            last = attempt == self._max_retries
            headers = await self._headers()
            try:
                response = await self._http.request(method, url, headers=headers, **kwargs)
            except httpx.TransportError as exc:
                if last:
                    raise RampError(f"{method} {url}: {type(exc).__name__}", retryable=True) from exc
                await self._sleep(min(8.0, 0.5 * 2 ** attempt))
                continue
            code = response.status_code
            if code == 429 and not last:
                await self._sleep(min(10.0, float(response.headers.get("Retry-After", "1"))))
                continue
            if code >= 500 and not last:
                await self._sleep(min(8.0, 0.5 * 2 ** attempt))
                continue
            if code == 401 and not isinstance(self._token, str):  # Ramp refused the token: log in again next try
                self._token.drop()
                raise RampError(f"{method} {url}: HTTP 401", status=401, retryable=True)
            if code == 404:
                raise RampNotFound(f"{method} {url}: not found")
            if code >= 400:
                raise RampError(f"{method} {url}: HTTP {code}", status=code, retryable=code == 429 or code >= 500)
            if empty_ok and not response.content:
                return {}
            return response.json()
        raise AssertionError("unreachable")

    async def _all_pages(self, path: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        body = await self._request("GET", f"{API}{path}", params=params)
        while True:
            items.extend(body.get("data", []))
            nxt = (body.get("page") or {}).get("next")
            if not nxt:
                return items
            body = await self._request("GET", nxt)

    async def get_vendor(self, vendor_id: str) -> RampVendor:
        return vendor_from(await self._request("GET", f"{API}/vendors/{vendor_id}"))

    async def list_vendors_updated_since(self, since: dt.datetime | None) -> list[RampVendor]:
        params = {"page_size": 100} | ({"from_updated_at": since.isoformat()} if since else {})
        return [vendor_from(v) for v in await self._all_pages("/vendors", params)]

    async def list_vendor_accounts(self, vendor_id: str) -> list[RampAccount]:
        return [account_from(a) for a in await self._all_pages(f"/vendors/{vendor_id}/accounts", {})]

    async def list_bills_for_vendor(self, vendor_id: str) -> list[RampBill]:
        bills = [bill_from(b) for b in await self._all_pages("/bills", {"vendor_id": vendor_id, "page_size": 100})]
        return [b for b in bills if b.vendor_id == vendor_id]  # filter in code too, in case Ramp ignores vendor_id

    async def place_vendor_hold(self, vendor_id: str, reason: str) -> RampHold:
        j = await self._request("POST", f"{API}/vendors/{vendor_id}/hold", json={"reason": reason})
        return RampHold(id=j["id"], vendor_id=j.get("vendor_id", vendor_id), placed_at=_ts(j.get("placed_at")))

    async def release_vendor_hold(self, vendor_id: str) -> None:
        await self._request("POST", f"{API}/vendors/{vendor_id}/release")

    async def set_bill_payment_date(self, bill_id: str, payment_date: dt.date | None) -> RampBill:
        body = {"payment_date": payment_date.isoformat() if payment_date else None}
        return bill_from(await self._request("PATCH", f"{API}/bills/{bill_id}", json=body))

    async def get_audit_events(self, target_id: str, event_type: str | None = None) -> list[RampAuditEvent]:
        params = {"target_id": target_id} | ({"event_type": event_type} if event_type else {})
        return [audit_from(e) for e in (await self._request("GET", f"{API}/audit-logs/events", params=params)).get("data", [])]
