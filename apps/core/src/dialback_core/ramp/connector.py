"""Hands out the RampPort for a dataset (spec 0002, D1; spec 0096).

Each dataset says which Ramp it uses, fixed when it is made (`app.datasets.ramp`):
- mock: the Ramp-shaped mock, authenticated with that dataset's mock token, so a lab scenario can
  only ever reach its own mock business. Needs `DIALBACK_RAMP=mock` (the default).
- sandbox: Ramp's sandbox, authenticated by the server's login. Needs the sandbox credentials.

`for_dataset` returns at once; the dataset's Ramp is read on its first call and kept, since it never
changes. A dataset this server can't reach, or one that doesn't exist, fails closed (R7).
"""
from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import hmac
from uuid import UUID

import httpx
import psycopg
from psycopg_pool import AsyncConnectionPool

from ..db import system_tx
from ..flags import RampMode
from ..settings import ConfigurationError, Settings
from .http import RampHttpAdapter, Sleep, TokenBucket
from .login import RampLogin
from .port import RampAccount, RampAuditEvent, RampBill, RampError, RampHold, RampPort, RampVendor


def mock_token(secret: bytes, dataset_id: UUID) -> str:
    """The mock's per-dataset bearer token: mock.<dataset_id>.<hex HMAC-SHA256(secret, dataset_id)>."""
    return f"mock.{dataset_id}.{hmac.new(secret, str(dataset_id).encode(), hashlib.sha256).hexdigest()}"


class RampConnector:
    def __init__(self, settings: Settings, http: httpx.AsyncClient, limiter: TokenBucket | None = None,
                 sleep: Sleep = asyncio.sleep, *, pool: AsyncConnectionPool) -> None:
        if settings.ramp_mode is RampMode.MOCK and not settings.mock_ramp_secret:
            raise ConfigurationError("MOCK_RAMP_SECRET is required with DIALBACK_RAMP=mock")
        if settings.ramp_mode is RampMode.SANDBOX and settings.ramp_sandbox is None:
            raise ConfigurationError("DIALBACK_RAMP=sandbox needs RAMP_SANDBOX_CLIENT_ID and RAMP_SANDBOX_CLIENT_SECRET")
        self._settings, self._http, self._sleep, self._pool = settings, http, sleep, pool
        self._limiter = limiter or TokenBucket(settings.ramp_requests_per_10s)
        self._login = RampLogin(settings.ramp_sandbox, http) if settings.ramp_sandbox else None
        self._ramps: dict[UUID, str] = {}
        self._adapters: dict[UUID, RampPort] = {}
        self._ports: dict[UUID, RampPort] = {}

    def for_dataset(self, dataset_id: UUID) -> RampPort:
        """The dataset's one port: a method swapped on it (a fault, a spy) is the one every caller gets."""
        port = self._ports.get(dataset_id)
        if port is None:
            port = self._ports[dataset_id] = _DatasetRamp(self, dataset_id)
        return port

    async def ramp_of(self, dataset_id: UUID) -> str:
        """`mock` or `sandbox`, read once: the database never lets it change."""
        ramp = self._ramps.get(dataset_id)
        if ramp is None:
            try:
                async with system_tx(self._pool) as conn:
                    row = await (await conn.execute("select ramp::text as ramp from app.datasets where id = %s",
                                                    (dataset_id,))).fetchone()
            except psycopg.Error as exc:
                raise RampError(f"couldn't read dataset {dataset_id}'s Ramp: {type(exc).__name__}", retryable=True) from exc
            if row is None:
                raise RampError(f"no dataset {dataset_id}")
            ramp = self._ramps[dataset_id] = row["ramp"]
        return ramp

    async def adapter(self, dataset_id: UUID) -> RampPort:
        adapter = self._adapters.get(dataset_id)
        if adapter is None:
            ramp = await self.ramp_of(dataset_id)
            if ramp == "mock":
                if self._settings.ramp_mode is not RampMode.MOCK or not self._settings.mock_ramp_secret:
                    raise RampError(f"dataset {dataset_id} is on the Ramp mock, and this server has none (DIALBACK_RAMP=sandbox)")
                token = mock_token(self._settings.mock_ramp_secret, dataset_id)
                adapter = RampHttpAdapter(self._settings.ramp_base_url, token, self._http, self._limiter, sleep=self._sleep)
            else:
                if self._login is None or self._settings.ramp_sandbox is None:
                    raise RampError(f"dataset {dataset_id} is on Ramp's sandbox, and this server has no sandbox credentials")
                adapter = RampHttpAdapter(self._settings.ramp_sandbox.base_url, self._login, self._http, self._limiter,
                                          sleep=self._sleep)
            self._adapters[dataset_id] = adapter
        return adapter

    async def mock_bank_change(self, dataset_id: UUID, ramp_vendor_id: str, routing_number: str, account_number: str,
                               actor_ramp_user_id: str) -> None:
        """Demo only (spec 0077): a bank change made in the Ramp mock as a seeded Ramp user, the way a
        clerk makes one in Ramp, then the mock's webhooks sent now. The account number goes to the mock
        and nowhere else: nothing here logs it, and a refusal says only the status code."""
        if self._settings.ramp_mode is not RampMode.MOCK:
            raise MockRefused("not the Ramp mock", sent=False)
        try:
            ramp = await self.ramp_of(dataset_id)
        except RampError:
            raise MockRefused("no such dataset", sent=False) from None
        if ramp != "mock":
            raise MockRefused("the dataset is on Ramp's sandbox", sent=False)
        headers = {"Authorization": f"Bearer {mock_token(self._settings.mock_ramp_secret or b'', dataset_id)}"}
        base = self._settings.ramp_base_url
        try:
            made = await self._http.post(f"{base}/_mock/vendors/{ramp_vendor_id}/bank-change", headers=headers,
                                         json={"routing_number": routing_number, "account_number": account_number,
                                               "actor_user_id": actor_ramp_user_id})
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:  # never reached the mock: nothing was changed
            raise MockRefused(type(exc).__name__, sent=False) from None  # never the request, which carries the number
        except httpx.HTTPError as exc:  # it may have saved the change before the answer was lost
            raise MockRefused(type(exc).__name__, sent=True) from None
        if made.status_code != 200:  # the mock refused, and a refusal rolls back
            raise MockRefused(f"status {made.status_code}", sent=False)
        try:  # the change is made; if this fails the mock's sender (or the poller) delivers it soon
            await self._http.post(f"{base}/_mock/webhooks/deliver", headers=headers)
        except httpx.HTTPError:
            pass


class _DatasetRamp:
    """One dataset's Ramp, found on its first call, so no caller has to know which it is (spec 0096, D3)."""

    def __init__(self, connector: RampConnector, dataset_id: UUID) -> None:
        self._connector, self._dataset_id = connector, dataset_id

    async def _ramp(self) -> RampPort:
        return await self._connector.adapter(self._dataset_id)

    async def get_vendor(self, vendor_id: str) -> RampVendor:
        return await (await self._ramp()).get_vendor(vendor_id)

    async def list_vendors_updated_since(self, since: dt.datetime | None) -> list[RampVendor]:
        return await (await self._ramp()).list_vendors_updated_since(since)

    async def list_vendor_accounts(self, vendor_id: str) -> list[RampAccount]:
        return await (await self._ramp()).list_vendor_accounts(vendor_id)

    async def list_bills_for_vendor(self, vendor_id: str) -> list[RampBill]:
        return await (await self._ramp()).list_bills_for_vendor(vendor_id)

    async def place_vendor_hold(self, vendor_id: str, reason: str) -> RampHold:
        return await (await self._ramp()).place_vendor_hold(vendor_id, reason)

    async def release_vendor_hold(self, vendor_id: str) -> None:
        await (await self._ramp()).release_vendor_hold(vendor_id)

    async def set_bill_payment_date(self, bill_id: str, payment_date: dt.date | None) -> RampBill:
        return await (await self._ramp()).set_bill_payment_date(bill_id, payment_date)

    async def get_audit_events(self, target_id: str, event_type: str | None = None) -> list[RampAuditEvent]:
        return await (await self._ramp()).get_audit_events(target_id, event_type)


class MockRefused(RuntimeError):
    """The Ramp mock didn't take a demo bank change, or didn't confirm it (`sent`: the change may have
    been made). The message never carries the account number."""

    def __init__(self, what: str, *, sent: bool) -> None:
        super().__init__(what)
        self.sent = sent
