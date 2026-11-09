"""The voice service entrypoint (spec 0016).

`build_app` is the wiring: it turns `Settings` plus the injected store, http client, clock and
`lookup_call` into the FastAPI app, through the already-tested `build_twilio_deps` and `create_app`.
Text mode mounts no Twilio routes; live mode mounts them and wires the agent (spec 0015).

`create_asgi_app` reads the environment and assembles the app. With no factory injected, the store
and `lookup_call` default to the ledger seam: `lookup_call` is the live psycopg query built from
`DATABASE_URL` (spec 0029), and the Postgres store still lands with the ledger (PR2), so the store
factory raises a clear pointer until then. `build_app` is tested against the in-memory store.
"""
from __future__ import annotations

import logging
import time
from collections.abc import Callable
from uuid import UUID

import httpx
from fastapi import FastAPI

from .app import create_app
from .db import make_lookup
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
    raise NotImplementedError(
        "the Postgres CallStore lands with the ledger (spec 0029, PR2): its SQL reads the pick and "
        "joins app.cases. Pass a store_for factory until then.")


def _ledger_lookup(settings: Settings) -> Lookup:
    """The live lookup_call from settings (spec 0029): a psycopg query by call_id. With no
    DATABASE_URL it fails closed on boot (R7) rather than serve a lookup that resolves nothing."""
    if not settings.database_url:
        raise RuntimeError("lookup_call needs DATABASE_URL in live mode (spec 0029); set it, or pass "
                           "a lookup_for factory in a test.")
    return make_lookup(settings.database_url, customer_name=settings.customer_name,
                       callback_number=settings.callback_number)


def create_asgi_app(settings: Settings | None = None, *,
                    store_for: Callable[[], CallStore] | None = None,
                    lookup_for: Callable[[], Lookup] | None = None,
                    http: httpx.AsyncClient | None = None, clock: Clock = time.monotonic) -> FastAPI:
    """Read the environment and build the app. With no factory injected, the store and lookup default
    to the ledger seam built from settings (lookup from DATABASE_URL; the Postgres store lands in
    PR2); tests pass fakes."""
    settings = settings or load_settings()
    http = http or httpx.AsyncClient()
    store = store_for() if store_for is not None else _ledger_store(settings)
    lookup = lookup_for() if lookup_for is not None else _ledger_lookup(settings)
    return build_app(settings, store=store, http=http, clock=clock, lookup_call=lookup)


def main() -> None:  # pragma: no cover - the serve glue, exercised live, not in the suite
    import uvicorn
    logging.basicConfig(level=logging.INFO)
    uvicorn.run(create_asgi_app(), host="0.0.0.0", port=8080)


if __name__ == "__main__":  # pragma: no cover
    main()
