"""Who is calling the dashboard API (spec 0002, D9; spec 0010).

The dashboard sends the signed-in user's JWT: Supabase's access token in production, a
short-lived dev token locally (minted by the dashboard's server with a shared secret). The
core trusts a token only if its signature checks out against a key source it was configured
with, and there is no bypass:

- the Supabase project's public signing keys, fetched from its JWKS endpoint (`SUPABASE_URL`);
- a shared HS256 secret (`DIALBACK_JWT_SECRET`): local development, or a project still on
  Supabase's legacy secret.

A valid token names a signed-in person of this project, and every query for that request runs
in Postgres as that person, so the database's own rules decide what they may do (R5).
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import httpx
import jwt
from fastapi import HTTPException, Request

from .settings import Settings

log = logging.getLogger(__name__)


def _boot_clock() -> float:
    """Seconds that keep counting while the machine is suspended (Linux), so cached keys age
    honestly across a VM pause; elsewhere, the monotonic clock."""
    return time.clock_gettime(time.CLOCK_BOOTTIME) if hasattr(time, "CLOCK_BOOTTIME") else time.monotonic()


def _shown(value: object) -> str:
    return repr(value)[:40]  # header values are attacker-controlled; keep the log line short

JWKS_PATH = "/auth/v1/.well-known/jwks.json"
ISSUER_PATH = "/auth/v1"
ASYMMETRIC = frozenset({"ES256", "RS256"})  # what Supabase's signing keys use (EdDSA is not offered yet)


@dataclass(frozen=True)
class Principal:
    user_id: UUID
    claims: dict[str, Any]


class TokenRejected(Exception):
    """The token proves nothing: refuse the request (401). The reason is logged, never returned."""


async def _get_json(url: str) -> dict:
    async with httpx.AsyncClient(timeout=5.0) as http:
        response = await http.get(url)
        response.raise_for_status()
        return response.json()


class SigningKeys:
    """The project's public signing keys, from its JWKS endpoint, cached.

    - A refresh is due `ttl` seconds after the last successful fetch.
    - A key id we don't know triggers a refetch, but at most once per `min_refresh` seconds, so a
      stream of made-up key ids can't hammer Supabase. That's also how a rotated-in key is found.
    - If Supabase can't be reached, the last keys keep working until `max_age` seconds after
      they were fetched. After that nothing verifies until a fetch succeeds (fail closed, R7).

    Supabase's edge caches the JWKS for 10 minutes and asks apps not to cache longer, so a
    revoked key stops working here within 20 minutes. Restarting the core empties the cache.
    """

    def __init__(self, url: str, fetch: Callable[[str], Awaitable[dict]] = _get_json, ttl: float = 600.0,
                 min_refresh: float = 30.0, max_age: float = 1200.0, clock: Callable[[], float] = _boot_clock) -> None:
        self.url = url
        self._fetch, self._ttl, self._min_refresh, self._max_age, self._clock = fetch, ttl, min_refresh, max_age, clock
        self._keys: dict[str, jwt.PyJWK] = {}
        self._fetched_at: float | None = None
        self._tried_at: float | None = None
        self._lock = asyncio.Lock()

    async def get(self, kid: str) -> jwt.PyJWK | None:
        if self._wanted(kid):
            # Wait for any fetch already under way, then fetch only if the throttle allows.
            async with self._lock:
                if self._wanted(kid) and self._may_fetch():
                    await self._refresh()
        if self._fetched_at is None or self._clock() - self._fetched_at >= self._max_age:
            return None
        return self._keys.get(kid)

    def _wanted(self, kid: str) -> bool:
        return self._fetched_at is None or self._clock() - self._fetched_at >= self._ttl or kid not in self._keys

    def _may_fetch(self) -> bool:
        return self._tried_at is None or self._clock() - self._tried_at >= self._min_refresh

    async def _refresh(self) -> None:
        self._tried_at = self._clock()
        try:
            document = await self._fetch(self.url)
        except Exception as exc:  # noqa: BLE001  (any failure keeps the old keys; max_age bounds them)
            log.warning("could not fetch the signing keys from %s: %s", self.url, type(exc).__name__)
            return
        keys: dict[str, jwt.PyJWK] = {}
        for entry in document.get("keys", []) if isinstance(document, dict) else []:
            if not isinstance(entry, dict) or not isinstance(entry.get("kid"), str):
                continue
            try:
                keys[entry["kid"]] = jwt.PyJWK(entry)
            except jwt.PyJWTError as exc:
                log.warning("skipping signing key %s: %s", entry.get("kid"), exc)
        self._keys, self._fetched_at = keys, self._clock()


@dataclass
class TokenVerifier:
    audience: str
    secret: bytes | None = None
    keys: SigningKeys | None = None
    issuer: str | None = None

    async def verify(self, token: str) -> Principal:
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError as exc:
            raise TokenRejected("malformed token") from exc
        alg = header.get("alg")
        if not isinstance(alg, str):
            raise TokenRejected("no algorithm")
        # The algorithm picks the key source, and each source accepts only its own algorithms,
        # so a public key can never be used as an HMAC secret.
        if alg == "HS256" and self.secret:
            key: Any = self.secret
        elif alg in ASYMMETRIC and self.keys is not None:
            kid = header.get("kid")
            jwk = await self.keys.get(kid) if isinstance(kid, str) else None
            if jwk is None or jwk.algorithm_name != alg:
                raise TokenRejected(f"no {_shown(alg)} signing key {_shown(kid)}")
            key = jwk.key
        else:
            raise TokenRejected(f"tokens signed with {_shown(alg)} are not accepted here")
        required = ["exp", "sub", "aud"] + (["iss"] if self.issuer else [])
        try:
            claims = jwt.decode(token, key, algorithms=[alg], audience=self.audience, issuer=self.issuer,
                                options={"require": required})
        except jwt.PyJWTError as exc:
            raise TokenRejected(f"{type(exc).__name__}: {exc}") from exc
        if claims.get("role") != "authenticated" or claims.get("is_anonymous") is True:
            raise TokenRejected("not a signed-in person")
        try:
            user = UUID(str(claims["sub"]))
        except ValueError as exc:
            raise TokenRejected("subject is not a user id") from exc
        return Principal(user, claims)


def verifier_from_settings(settings: Settings) -> TokenVerifier:
    base = settings.supabase_url
    if base and settings.jwt_secret:
        log.warning("trusting both the Supabase project's keys and a shared HS256 secret; "
                    "remove DIALBACK_JWT_SECRET once the project is off its legacy secret")
    return TokenVerifier(audience=settings.jwt_audience, secret=settings.jwt_secret or None,
                         keys=SigningKeys(base + JWKS_PATH) if base else None,
                         issuer=base + ISSUER_PATH if base else None)


async def current_user(request: Request) -> Principal:
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise HTTPException(401, detail={"code": "unauthenticated", "message": "Sign in to continue."})
    try:
        return await request.app.state.verifier.verify(token.strip())
    except TokenRejected as exc:
        log.info("refused a dashboard token: %s", exc)
        raise HTTPException(401, detail={"code": "unauthenticated", "message": "Your session is not valid; sign in again."}) from exc
