# 0027: The voice service reads the database: lookup_call and the call store

**Owner:** Sami  **Partner:** Inesh  **Slice:** call-and-adversary (voice)  **Target day:** Sun Nov 8

## Goal

`apps/voice/entrypoint.py` ships two factories that deliberately raise (`_ledger_lookup`,
`_ledger_store`): the live service cannot resume or place a call because nothing reads the
database. The ledger now stores the pick (spec 0024) and the schema is migrated, so the voice
service can read it. This spec fills the read side.

Delivered in two pull requests to stay small:

- **PR1 (this spec's acceptance): `lookup_call`.** The Twilio-callback read path. Given a
  `call_id`, return the `CallDial` the routes mint the token and speak the disclosure from, or
  `None` fail-closed.
- **PR2 (follow-up, specced when PR1 lands): the `CallStore` adapter.** The `POST /calls` path:
  `gather_pre_dial` and the four write methods. Its pre-dial facts (the hold, the calling window,
  the allowlist, the 30-day cap) raise their own open questions and get their own section then.

Serves R2 (the number is the pick bound to the call, never a new one), R7 (any missing fact is
`None`, never a guessed dial) and R8 (the disclosure names come from code and config, never from
the callee).

## Decisions

1. **`lookup_call` stays synchronous.** `app.py` calls it `dial = twilio.lookup_call(call_id)`
   (no `await`) in three async routes. The `CallStore` is async, but changing the lookup signature
   would ripple through `app.py` and its doubles for no behaviour gain. The real lookup runs one
   short synchronous psycopg query. The Twilio-callback volume is one query per callback at no more
   than one concurrent call (the attempt caps), so the brief event-loop block is acceptable (Q60).
2. **The number is the one pinned on the call.** `voice.calls.contact_point_id` is the pick
   recorded when the call was created (R2). `lookup_call` reads `ledger.contact_points.e164` by
   that id; it never re-runs selection. A call with no pinned contact point, or whose contact point
   is gone, resolves to `None`. The join also ties the pinned contact point to the case's own vendor
   and dataset, so a mismatched pin fails closed rather than dial another vendor's number (defense in
   depth; `contact_point_id` has no FK yet, Q40).
3. **`customer_name` and `callback_number` come from settings, not the database.** Dialback is one
   customer per deployment and the README describes these as context the service holds, so they are
   new voice settings (`DIALBACK_CUSTOMER_NAME`, `DIALBACK_AP_CALLBACK_NUMBER`), read in live mode.
   `lookup_call` fills them onto the row that `dial_from_row` maps, so an empty setting fails the
   call closed through the existing mapper.
4. **`DATABASE_URL` is a new setting, read in live mode.** Without it the lookup factory fails
   closed (raises on boot, as the seam does today). `apps/voice` gains a `psycopg[binary]`
   dependency.
5. **`dial_from_row` is unchanged.** It stays the single fail-closed gate; the query produces its
   row keys (`case_id`, `attempt`, `number_e164`, `vendor_names`) and the settings supply
   `customer_name` and `callback_number`.
6. **The voice database test migrates a throwaway Postgres with psycopg, keyed on `DATABASE_URL`,
   not `pgtemp`.** The adapter and CI are psycopg-only, and `pgtemp` needs the `psql` client, which
   the live-call path never uses. The fixture creates a fresh database on the `DATABASE_URL` server
   (CI's service container, or a local Docker Postgres), applies `_local_auth_stub.sql`, migrates
   with `scripts/db/migrate.py`, and drops it after. With no `DATABASE_URL` the test skips.

## Contract touched

None. `dial_from_row` and `CallDial` are unchanged; no `packages/contracts` change.

## Acceptance (PR1)

- [ ] `lookup_call(call_id)` runs one query joining `voice.calls`, `app.cases`, `app.vendors` and
      `ledger.contact_points` (by the call's pinned `contact_point_id`), and returns the `CallDial`
      for a complete row.
- [ ] `vendor_names` is the vendor's `legal_name` followed by its `trade_names`.
- [ ] A missing call row, a null or absent pinned contact point, or any missing required field
      resolves to `None` (fail closed, R2/R7).
- [ ] A pin whose contact point is not the case's own vendor and dataset resolves to `None`, never
      dialing a number proven for someone else (defense in depth for R2).
- [ ] `customer_name` and `callback_number` come from settings; an empty setting resolves the call
      to `None` through `dial_from_row`.
- [ ] `Settings` gains `database_url`, `customer_name`, `callback_number`, read in live mode; text
      mode needs none of them.
- [ ] `_ledger_lookup` no longer raises: `create_asgi_app` in live mode builds a working lookup from
      `DATABASE_URL`; with no `DATABASE_URL` it fails closed on boot.
- [ ] The voice suite stays green; `scripts/ci/boundaries.py` passes; no contract change.

## Tests (written first)

| Item | Test |
|---|---|
| the real SQL and the mapper against a seeded row, and each fail-closed case | `apps/voice/tests/test_lookup_sql.py` (throwaway Postgres via psycopg, decision 6) |
| the new settings fields, and the fail-closed boot without `DATABASE_URL` | `apps/voice/tests/test_settings.py`, `apps/voice/tests/test_entrypoint.py` |

## Out of scope

The `CallStore` adapter (PR2): `gather_pre_dial` and the write methods. `DIALBACK_RESOLVER` (the
core's flag, spec 0024). The live Twilio account, numbers and deploy (ops). Multi-customer sourcing
of the disclosure names.

## Open questions

- **Q60 (Inesh, to confirm):** `lookup_call` runs a synchronous query inside async routes, briefly
  blocking the loop. Fine at this volume; the alternative is to make the lookup async and `await` it
  in the three routes. Confirm sync is acceptable, or I switch it in PR1.
- **Q61:** env names `DIALBACK_CUSTOMER_NAME` and `DIALBACK_AP_CALLBACK_NUMBER`; rename if
  `docs/deploy.md` prefers others.
- **PR2 (to spec when PR1 lands):** where `within_calling_window` comes from (an `app.timers`
  `calling_window` row vs a timezone computation), how `number_opted_out` maps from
  `ledger.contact_points.status`, and whether the dial allowlist is code or a table.

## Commands

```bash
cd apps/voice && uv run pytest -q
```

```bash
scripts/db/test.sh
```
