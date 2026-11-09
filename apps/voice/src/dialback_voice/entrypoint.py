"""The voice service entrypoint (spec 0016).

`build_app` is the wiring: it turns `Settings` plus the injected store, http client, clock and
`lookup_call` into the FastAPI app, through the already-tested `build_twilio_deps` and `create_app`.
Text mode mounts no Twilio routes; live mode mounts them and wires the agent (spec 0015).

`create_asgi_app` reads the environment and assembles the app. With no factory injected, the store
and `lookup_call` are the Postgres ones built from `DATABASE_URL` (spec 0029): the call store in
`pg_store.py`, the lookup in `db.py`. Without `DATABASE_URL` both factories raise, so the service
refuses to boot rather than serve half-wired (R7). `build_app` is tested against the in-memory store.
"""
from __future__ import annotations

import logging
import os
import time
from collections.abc import Callable
from uuid import UUID

import httpx
from fastapi import FastAPI

from .app import create_app
from .db import make_lookup
from .pg_store import PgCallStore
from .settings import Settings, load_settings
from .store import CallDial, CallStore
from .transport import Clock
from .wire import build_twilio_deps

log = logging.getLogger(__name__)

Lookup = Callable[[UUID], CallDial | None]


def build_app(settings: Settings, *, store: CallStore, http: httpx.AsyncClient, clock: Clock,
              lookup_call: Lookup) -> FastAPI:
    """Assemble the app from settings and the injected seams (text line, or live with Twilio routes)."""
    twilio = build_twilio_deps(settings, lookup_call=lookup_call, http=http, clock=clock)
    return create_app(store=store, internal_api_token=settings.internal_api_token,
                      agent_version=settings.agent_version, twilio=twilio)


def _ledger_store(settings: Settings) -> CallStore:
    """The Postgres call store (spec 0029, `pg_store.py`), or a refusal to boot without DATABASE_URL."""
    if settings.database_url:
        return PgCallStore(settings.database_url, dial_allowlist=settings.dial_allowlist,
                           calling_tz=settings.calling_tz)
    raise NotImplementedError(
        "no call store: set DATABASE_URL for the Postgres store (spec 0029), or pass a store_for factory")


def _ledger_lookup(settings: Settings) -> Lookup:
    """The live lookup_call from settings (spec 0029): a psycopg query by call_id. With no
    DATABASE_URL it fails closed on boot (R7) rather than serve a lookup that resolves nothing."""
    if not settings.database_url:
        raise RuntimeError("lookup_call needs DATABASE_URL (spec 0029); set it, or pass "
                           "a lookup_for factory in a test.")
    return make_lookup(settings.database_url, customer_name=settings.customer_name,
                       callback_number=settings.callback_number)


def create_asgi_app(settings: Settings | None = None, *,
                    store_for: Callable[[], CallStore] | None = None,
                    lookup_for: Callable[[], Lookup] | None = None,
                    http: httpx.AsyncClient | None = None, clock: Clock = time.monotonic) -> FastAPI:
    """Read the environment and build the app. With no factory injected, the store and lookup are the
    Postgres ones built from DATABASE_URL (spec 0029); without it the service refuses to boot (R7).
    Tests pass fakes."""
    settings = settings or load_settings()
    http = http or httpx.AsyncClient()
    store = store_for() if store_for is not None else _ledger_store(settings)
    lookup = lookup_for() if lookup_for is not None else _ledger_lookup(settings)
    return build_app(settings, store=store, http=http, clock=clock, lookup_call=lookup)


def main() -> None:  # pragma: no cover - the serve glue, exercised live, not in the suite
    import uvicorn
    logging.basicConfig(level=logging.INFO)
    # HOST and PORT as the core reads them; Railway sets PORT, and HOST="::" adds its IPv6 private network.
    uvicorn.run(create_asgi_app(), host=os.environ.get("HOST", "0.0.0.0"), port=int(os.environ.get("PORT", "8080")))


if __name__ == "__main__":  # pragma: no cover
    main()
