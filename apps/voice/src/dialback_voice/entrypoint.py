"""The voice service entrypoint (spec 0016).

`build_app` is the wiring: it turns `Settings` plus the injected store, http client, clock and
`lookup_call` into the FastAPI app, through the already-tested `build_twilio_deps` and `create_app`.
Text mode mounts no Twilio routes; live mode mounts them and wires the agent (spec 0015).

`create_asgi_app` reads the environment and assembles the app. The call store and `lookup_call` come
from factories that default to the ledger seam: their SQL reads the Trust Ledger pick and joins
`app.cases`, whose shapes are not final (Q40/Q50), so the real Postgres store and the DB pool land
with the ledger, and the default factories raise a clear pointer until then. `build_app` is tested
now against the in-memory store, so dropping the real store into the default is one line.
"""
from __future__ import annotations

import logging
import time
from collections.abc import Callable
from uuid import UUID

import httpx
from fastapi import FastAPI

from .app import create_app
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


def _ledger_store() -> CallStore:
    raise NotImplementedError(
        "the Postgres CallStore lands with the ledger: its SQL reads the pick and joins app.cases "
        "(Q40/Q50). Pass a store_for factory until then.")


def _ledger_lookup() -> Lookup:
    raise NotImplementedError(
        "lookup_call lands with the ledger: its SQL joins voice.calls, app.cases and the pick "
        "(Q40/Q50). Pass a lookup_for factory until then.")


def create_asgi_app(settings: Settings | None = None, *,
                    store_for: Callable[[], CallStore] = _ledger_store,
                    lookup_for: Callable[[], Lookup] = _ledger_lookup,
                    http: httpx.AsyncClient | None = None, clock: Clock = time.monotonic) -> FastAPI:
    """Read the environment and build the app. The store and lookup factories default to the ledger
    seam, so the real store drops into the default once the ledger lands; tests pass fakes."""
    settings = settings or load_settings()
    http = http or httpx.AsyncClient()
    return build_app(settings, store=store_for(), http=http, clock=clock, lookup_call=lookup_for())


def main() -> None:  # pragma: no cover - the serve glue, exercised live, not in the suite
    import uvicorn
    logging.basicConfig(level=logging.INFO)
    uvicorn.run(create_asgi_app(), host="0.0.0.0", port=8080)


if __name__ == "__main__":  # pragma: no cover
    main()
