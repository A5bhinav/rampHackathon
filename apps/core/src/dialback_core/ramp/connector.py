"""Hands out the RampPort for a dataset (spec 0002, D1).

- mock: one adapter per dataset, authenticated with that dataset's mock token, so a lab
  scenario can only ever reach its own mock business.
- sandbox: not wired yet. Ramp's client-credentials flow, scopes and base URL are open
  questions (spec 0002; apps/core README); until they're answered this fails closed at
  startup rather than guessing.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
from uuid import UUID

import httpx

from ..flags import RampMode
from ..settings import ConfigurationError, Settings
from .http import RampHttpAdapter, Sleep, TokenBucket
from .port import RampPort


def mock_token(secret: bytes, dataset_id: UUID) -> str:
    """The mock's per-dataset bearer token: mock.<dataset_id>.<hex HMAC-SHA256(secret, dataset_id)>."""
    return f"mock.{dataset_id}.{hmac.new(secret, str(dataset_id).encode(), hashlib.sha256).hexdigest()}"


class RampConnector:
    def __init__(self, settings: Settings, http: httpx.AsyncClient, limiter: TokenBucket | None = None,
                 sleep: Sleep = asyncio.sleep) -> None:
        if settings.ramp_mode is RampMode.SANDBOX:
            raise ConfigurationError("DIALBACK_RAMP=sandbox is not wired yet: Ramp credentials and scopes are open questions")
        if not settings.mock_ramp_secret:
            raise ConfigurationError("MOCK_RAMP_SECRET is required with DIALBACK_RAMP=mock")
        self._settings, self._http, self._sleep = settings, http, sleep
        self._limiter = limiter or TokenBucket(settings.ramp_requests_per_10s)
        self._adapters: dict[UUID, RampPort] = {}

    def for_dataset(self, dataset_id: UUID) -> RampPort:
        adapter = self._adapters.get(dataset_id)
        if adapter is None:
            token = mock_token(self._settings.mock_ramp_secret or b"", dataset_id)
            adapter = RampHttpAdapter(self._settings.ramp_base_url, token, self._http, self._limiter, sleep=self._sleep)
            self._adapters[dataset_id] = adapter
        return adapter

    async def mock_bank_change(self, dataset_id: UUID, ramp_vendor_id: str, routing_number: str, account_number: str,
                               actor_ramp_user_id: str) -> None:
        """Demo only (spec 0077): a bank change made in the Ramp mock as a seeded Ramp user, the way a
        clerk makes one in Ramp, then the mock's webhooks sent now. The account number goes to the mock
        and nowhere else: nothing here logs it, and a refusal says only the status code."""
        if self._settings.ramp_mode is not RampMode.MOCK:
            raise MockRefused("not the Ramp mock")
        headers = {"Authorization": f"Bearer {mock_token(self._settings.mock_ramp_secret or b'', dataset_id)}"}
        base = self._settings.ramp_base_url
        try:
            made = await self._http.post(f"{base}/_mock/vendors/{ramp_vendor_id}/bank-change", headers=headers,
                                         json={"routing_number": routing_number, "account_number": account_number,
                                               "actor_user_id": actor_ramp_user_id})
        except httpx.HTTPError as exc:
            raise MockRefused(type(exc).__name__) from None  # never the request, which carries the number
        if made.status_code != 200:
            raise MockRefused(f"status {made.status_code}")
        try:  # the change is made; if this fails the mock's sender (or the poller) delivers it soon
            await self._http.post(f"{base}/_mock/webhooks/deliver", headers=headers)
        except httpx.HTTPError:
            pass


class MockRefused(RuntimeError):
    """The Ramp mock didn't take a demo bank change. The message never carries the account number."""
