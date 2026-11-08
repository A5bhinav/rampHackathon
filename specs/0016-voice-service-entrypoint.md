# 0016: The voice service entrypoint

**Owner:** Sami  **Slice:** call  **Target day:** wiring before code freeze (Nov 8); the serve path and the real store land with the ledger.

## Goal

Nothing boots the voice service. `create_app` is only ever built in tests, and there is no process that reads the environment, constructs the resources, and serves. This spec adds the wiring that turns the tested seams into a runnable service: `build_app()`, which assembles `create_app` from `Settings` and the injected store, Twilio deps and model; the one setting it still needs (the internal API token, which authenticates the core's calls on the text line too, not only in live mode); and an ASGI entrypoint that wires the real resources.

The real `CallStore` (and `lookup_call`) read the ledger pick, whose shape is not final (Q40/Q50), so the Postgres store, the `lookup_call` query, and the DB pool are the thin glue that lands with the ledger. `build_app` is wired and tested now against the in-memory store double, so dropping in the real store is one line when the ledger is in.

## Contract touched

None.

## Decisions (proposed; Sami to confirm)

1. **`settings.internal_api_token`, read in both modes.** `POST /calls` is authenticated with a shared secret (`create_app`'s `internal_api_token`) on the text line as well as live, so `load_settings` reads `DIALBACK_INTERNAL_TOKEN` into `internal_api_token: bytes` whatever the mode, empty when unset (so `create_app` rejects every call, fail closed, R7). It is the same shared secret `TwilioConfig.internal_token` carries (the one the core and the voice service share, `docs/deploy.md`): in live mode they hold the same value; in text mode only `internal_api_token` is set, because there is no `TwilioConfig`.

2. **`build_app(settings, *, store, http, clock, lookup_call)`.** Pure wiring: it calls `build_twilio_deps(settings, lookup_call=..., http=..., clock=...)` and `create_app(store=store, internal_api_token=settings.internal_api_token, agent_version=settings.agent_version, twilio=<deps>)`. In text mode the deps are None, so the app serves `POST /calls` and `/healthz` with no Twilio routes; in live mode the Twilio routes mount and the agent is wired (spec 0015). Tested with the in-memory store and fakes, no DB.

3. **The serve path is the ledger seam.** `create_asgi_app` reads `load_settings()`, builds the store and `lookup_call` from injectable factories, the httpx client and the clock, and calls `build_app`; `main` serves it under uvicorn. The store and `lookup_call` factories default to a clear ledger seam (they raise with a pointer to Q40/Q50 until the ledger lands), so `create_asgi_app` is exercised in tests with fakes and the real store drops into the default later. The live agent's model turn is synchronous, so the serve path runs the agent off the event loop (`asyncio.to_thread`) rather than blocking the WebSocket (Q55).

## Acceptance

- [ ] `load_settings` reads `DIALBACK_INTERNAL_TOKEN` into `internal_api_token` (bytes) in both text and live mode, and leaves it empty when the key is unset.
- [ ] `build_app` in text mode returns an app that serves `/healthz` and `POST /calls`, mounts no Twilio routes, and authenticates `POST /calls` with `internal_api_token` (a wrong or missing token is 401; a correct one gets past auth).
- [ ] `build_app` in live mode (a validated `TwilioConfig`) mounts the Twilio routes and wires the deps through `build_twilio_deps`.
- [ ] `create_asgi_app` assembles the app from injected store and `lookup_call` factories (tested with fakes, no DB); the real Postgres store, `lookup_call` query, and pool are the documented ledger seam.

## Tests (written first)

| Acceptance item | Test |
|---|---|
| 1 | `apps/voice/tests/test_settings.py::test_internal_token_is_read_in_both_modes` |
| 2 | `apps/voice/tests/test_entrypoint.py::test_text_mode_serves_calls_and_healthz_no_twilio_routes`, `::test_post_calls_needs_the_internal_token` |
| 3 | `apps/voice/tests/test_entrypoint.py::test_live_mode_mounts_the_twilio_routes` |
| 4 | `apps/voice/tests/test_entrypoint.py::test_create_asgi_app_wires_injected_factories` |

Driven with the in-memory store double, a fake `lookup_call`, and `TestClient`. No Postgres, no live account, no key.

## Out of scope

- The Postgres `CallStore` (`find_call`, `record_call`, `mark_dialed`, `mark_failed`), the `lookup_call` query, and the DB pool: their SQL reads the ledger pick and joins `app.cases`, so they land with the ledger (Q40/Q50), and their test needs a money-path-seeded case. Adding `psycopg` lands with them.
- Deployment config (Railway image, env secrets), provisioning, the live go/no-go (Q48).

## Open questions

- **Q55:** the sync model turn (spec 0015) blocks the event loop if `reply` is awaited inline. Confirm the fix is `asyncio.to_thread(agent.reply, ...)` in `run_relay` (so every agent is wrapped once) rather than in the entrypoint. Until the live agent is dialed, it does not bite.
- Carried: **Q40/Q50** (the ledger shapes), **Q48** (the live go/no-go).

## Commands

```bash
cd apps/voice && uv run pytest
python3 scripts/ci/boundaries.py
uv run --no-project --with ruff ruff check apps packages scripts
```
