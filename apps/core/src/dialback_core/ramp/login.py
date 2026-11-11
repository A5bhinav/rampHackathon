"""Ramp's client-credentials login (spec 0096, D4).

One token per server, kept and renewed five minutes before it runs out (Ramp's last ten days), one
renewal at a time. A 401 from Ramp drops it (`drop`), so the next try logs in again. Neither the
secret nor a token goes into an error message or a log line: failures name only the status.
"""
from __future__ import annotations

import asyncio
import base64
import time
from collections.abc import Callable

import httpx

from ..settings import RampSandbox
from .port import RampError

RENEW_BEFORE_SECONDS = 300.0


class RampLogin:
    def __init__(self, sandbox: RampSandbox, http: httpx.AsyncClient, clock: Callable[[], float] = time.monotonic) -> None:
        self._url = f"{sandbox.base_url}/developer/v1/token"
        self._form = {"grant_type": "client_credentials", "scope": " ".join(sandbox.scopes)}
        basic = base64.b64encode(sandbox.client_id.encode() + b":" + sandbox.client_secret).decode()
        self._headers = {"Authorization": f"Basic {basic}", "Accept": "application/json"}
        self._http, self._clock = http, clock
        self._token: str | None = None
        self._renew_at = 0.0
        self.granted: tuple[str, ...] | None = None  # the scopes Ramp's last login answer listed, if it listed any
        self._lock = asyncio.Lock()

    async def token(self) -> str:
        async with self._lock:
            if self._token is None or self._clock() >= self._renew_at:
                self._token, self._renew_at = await self._log_in()
            return self._token

    def drop(self) -> None:
        """Forget the kept token: Ramp refused it."""
        self._token = None

    async def _log_in(self) -> tuple[str, float]:
        started = self._clock()
        try:
            response = await self._http.post(self._url, data=self._form, headers=self._headers)
        except httpx.TransportError as exc:
            raise RampError(f"Ramp login: {type(exc).__name__}", retryable=True) from None
        code = response.status_code
        if code != 200:
            raise RampError(f"Ramp login refused: HTTP {code}", status=code, retryable=code == 429 or code >= 500)
        try:
            body = response.json()
            token, lifetime = str(body["access_token"]), float(body["expires_in"])
        except (ValueError, KeyError, TypeError):
            raise RampError("Ramp login: the answer carried no token", retryable=False) from None
        scope = body.get("scope")
        self.granted = tuple(scope.split()) if isinstance(scope, str) and scope.strip() else None
        return token, started + lifetime - RENEW_BEFORE_SECONDS
