"""Hands out the RampPort for a dataset (spec 0002, D1).

- mock: one adapter per dataset, authenticated with that dataset's mock token, so a lab
  scenario can only ever reach its own mock business.
- sandbox: not wired yet. Ramp's client-credentials flow, scopes and base URL are open
  questions (spec 0002; apps/core README); until they're answered this fails closed at
  startup rather than guessing.
"""
from __future__ import annotations

import hashlib
import hmac
from uuid import UUID

import httpx

from ..flags import RampMode
from ..settings import ConfigurationError, Settings
from .http import RampHttpAdapter, TokenBucket
from .port import RampPort


def mock_token(secret: bytes, dataset_id: UUID) -> str:
    """The mock's per-dataset bearer token: mock.<dataset_id>.<hex HMAC-SHA256(secret, dataset_id)>."""
    return f"mock.{dataset_id}.{hmac.new(secret, str(dataset_id).encode(), hashlib.sha256).hexdigest()}"


class RampConnector:
    def __init__(self, settings: Settings, http: httpx.AsyncClient, limiter: TokenBucket | None = None) -> None:
        if settings.ramp_mode is RampMode.SANDBOX:
            raise ConfigurationError("DIALBACK_RAMP=sandbox is not wired yet: Ramp credentials and scopes are open questions")
        if not settings.mock_ramp_secret:
            raise ConfigurationError("MOCK_RAMP_SECRET is required with DIALBACK_RAMP=mock")
        self._settings, self._http = settings, http
        self._limiter = limiter or TokenBucket(settings.ramp_requests_per_10s)
        self._adapters: dict[UUID, RampPort] = {}

    def for_dataset(self, dataset_id: UUID) -> RampPort:
        adapter = self._adapters.get(dataset_id)
        if adapter is None:
            token = mock_token(self._settings.mock_ramp_secret or b"", dataset_id)
            adapter = RampHttpAdapter(self._settings.ramp_base_url, token, self._http, self._limiter)
            self._adapters[dataset_id] = adapter
        return adapter
