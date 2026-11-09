# 0030: The voice call store (0029's PR2), the report facts decide() needs, and the deploy

**Owner:** Abhi (Sami's slice, at the team's request on Nov 8; Sami reviews). Continues Sami's
spec 0029, whose PR1 (`lookup_call`, `db.py`) merged as #65: this is its PR2 (the call store), plus
the call report's facts, the allowlist for every dataset, and the Railway deploy.  **Slice:** call  **Target day:** Sun Nov 8

## Goal

The core can't place a call today: the voice service isn't deployed, and it can't be. Its serve
path raises until the Postgres `CallStore` and `lookup_call` exist (spec 0016 left them for "when
the ledger lands", and it has). After this, the voice service:
- runs on Railway from its own image;
- reads the case, the call history and the Trust Ledger's pick from Postgres;
- places live calls through Twilio once the team sets its secrets;
- reports each call to the core with every fact `decide()` checks about the call itself, so a good
  live call can verify.

## Decisions

1. **`PgCallStore` (new `pg_store.py`)** implements the `CallStore` protocol, reading and writing
   as `service_role`, like the core's worker. Each fact `pre_dial_refusal` needs comes from:
   - **case exists, finished:** `app.cases`, with `released` as finished.
   - **held:** the state is `calling` (the core places calls only from there) and the case has a
     `confirmed` hold with no `released_at`.
   - **the pick:** the newest `ledger.number_selections` row for the case. Only `picked` counts,
     joined to `ledger.contact_points` for the number. A contact point that isn't `eligible`
     counts as opted out. No pick means `number_not_selected` (R2, R7).
   - **attempts used:** calls already recorded for this case version, matched by the core's
     Idempotency-Key prefix `{case_id}:call:v{version}:`.
   - **the number's 30-day calls:** `voice.calls` on the same contact point in 30 days. Contact
     points are per dataset, so each `demo reset` starts a fresh count, which keeps rehearsals possible.
   - **calling window:** a `demo` dataset is always inside it (the core already applies the
     demo window, spec 0011). Other kinds: Monday to Friday, 9:00 to 16:30 in `DIALBACK_CALLING_TZ`
     (default `America/Los_Angeles`).
   - **allowlist:** `DIALBACK_DIAL_ALLOWLIST`, comma-separated E.164 numbers, set in Railway and
     never committed. It applies to **every** dataset: AGENTS.md says never call outside the team
     allowlist, so `guards.py` no longer limits it to demo datasets (this changes Sami's rule; the
     voice README said demo only). Empty means nobody is called.

   `record_call` inserts once per Idempotency-Key (a race returns the first row). `mark_dialed`
   and `mark_failed` update the row.
2. **`lookup_call` is Sami's `db.make_lookup` (0029, PR1).** This adds three things to it:
   - `contact_point_id`, which the call report needs;
   - a 5-second connect timeout;
   - a database error reads as `None` (fail closed).
   `app.py` runs it with `asyncio.to_thread`, so the synchronous query never blocks other calls.
   The disclosure names are Sami's settings: `DIALBACK_CUSTOMER_NAME` and `DIALBACK_AP_CALLBACK_NUMBER`.
3. **The report carries what `decide()` checks.** Before this, the report had none of the call
   facts the verified rules read, so no live call could ever verify:
   - **`contact_point_id` (V01):** `CallDial` gains it, and every report carries it: the WebSocket
     report, the status callback and the failed dial.
   - **`dialed_number_is_pick` (V02):** true when the ConversationRelay setup passed
     `verify_setup`, which checks the one-time token and that Twilio dialed the pick.
   - **`direction` (V02):** Twilio's own `direction` from the setup message.
   - **`answered_by` (V03):** Twilio's synchronous machine detection arrives on the instruction
     fetch. The instruction route carries it to the WebSocket as a `<Parameter>`. It reads "human"
     only when Twilio said so; "unknown" claims nothing.
   - **`checks` (V10, V11):** the policy's `CallChecks`, computed from the transcript.

   A setup that fails verification vouches for none of these.
4. **Live needs the disclosure's names.** Without `DIALBACK_CUSTOMER_NAME` and
   `DIALBACK_AP_CALLBACK_NUMBER`, live mode stays off and the service runs the text line, as with a
   missing Twilio key. Otherwise a call would ring and then play no disclosure (R8).
5. **No hangs:** each database connection has a 5-second connect timeout. The synchronous lookup
   runs off the event loop (`asyncio.to_thread`).
6. **The entrypoint** builds the Postgres store and lookup when `DATABASE_URL` is set. Without it,
   the service still refuses to boot (spec 0016's seam test stands). `DATABASE_URL` is now read in
   text mode too, because the store serves `POST /calls` on the text line. This changes Sami's
   "not read in text mode" test. `main` serves on `$PORT`
   (default 8080).
7. **Deploy:** `apps/voice/Dockerfile`, built from the repo root like the core's. A Railway
   service `voice` gets a public domain, which Twilio needs for its webhooks and the WebSocket.
   The core reaches it over the private network (`VOICE_URL`). `docs/deploy.md` lists every
   variable. The team sets the secrets (Twilio, the Anthropic key, the allowlist); agents never
   see them.

## Contract touched

None. `contact_point_id` is already an optional field of the call-ended request (internal.yaml).

## Acceptance

- [ ] On a real migrated database, a held case in `calling` with a picked, eligible, allowlisted number passes every pre-dial check. `record_call` stores it once, and a replay returns the same call.
- [ ] Each refusal comes from the database facts: no pick, an excluded number, the case not held, attempts used up, the number's cap reached, a demo number not on the allowlist, and outside the window for a non-demo dataset.
- [ ] `mark_dialed` and `mark_failed` update the row. `lookup_call` returns the dial with the pick, the vendor names and the configured customer and callback number, or `None` when any is missing.
- [ ] Every call-ended report from the voice service carries `contact_point_id`. A verified live call's report also carries `dialed_number_is_pick`, `direction`, `answered_by` (when Twilio detected a human) and `checks`; a rejected setup carries none of them.
- [ ] Every dialed number must be on the allowlist, in every dataset.
- [ ] Live mode without the disclosure's names runs the text line.
- [ ] With `DATABASE_URL` the ASGI app boots with the Postgres store. Without it, it still refuses to boot.
- [ ] The image builds and serves `/healthz`.

## Tests (written first)

| Acceptance item | Test |
|---|---|
| store passes and records | `apps/voice/tests/test_pg_store.py (Sami's `database_url` fixture: a throwaway DB on the `DATABASE_URL` server)` `apps/voice/tests/test_pg_store.py::test_a_dialable_case_passes_and_is_recorded_once` |
| refusals from the data | `test_pg_store.py::test_each_refusal_comes_from_the_data` |
| dialed, failed, lookup | `test_pg_store.py::test_dialed_failed_and_lookup` |
| report carries the facts | `test_relay.py::test_the_websocket_route_runs_a_call_and_reports`, `::test_a_forged_setup_reports_failed_to_the_core`, `test_status_callback.py`, `test_origination.py::test_originate_failure_reports_failed`, `test_twiml.py::test_machine_detection_rides_to_the_call_as_a_parameter` |
| allowlist everywhere | `test_guards.py::test_allowlist_is_required_for_every_dataset`, `test_pg_store.py::test_each_refusal_comes_from_the_data` |
| live needs the names | `test_settings.py::test_live_stays_off_without_the_disclosures_names` |
| held, newest pick, 30 days | `test_pg_store.py::test_held_means_calling_with_a_confirmed_hold`, `::test_the_newest_selection_wins_and_the_cap_counts_30_days_of_the_number` |
| boot | `test_entrypoint.py::test_create_asgi_app_with_a_database_builds_the_postgres_store` |
| image | `docker build -f apps/voice/Dockerfile .` and `/healthz` (by hand; the `images` CI job builds it) |

## Out of scope

- Writing the call's end state and its answers to `voice.calls` and `voice.call_answers`. The core
  records the report in the evidence chain; filling the voice tables is Sami's follow-up.
- Q49's background dial (the dial stays inside `POST /calls`), Q46's single-use token, Q55's
  off-loop model turn.
- Recordings and transcripts.

## Open questions

- **Q56 (Sami):** confirm the 30-day number cap counted per contact point (per dataset), so each demo reset starts fresh.
- **Q57 (Sami):** confirm "not eligible" counts as opted out, as opposed to a separate opt-out list.
- **Q58 (Sami):** the pick is the newest selection for the case. Reading the current version's
  `number_selection_id` from its `number_resolved` event would be exact. Worth it?
- **Q59 (Sami):** `lab` datasets run on simulated time, but this store's window and 30-day count
  use the wall clock. Fine while lab runs go through the text line?

## Commands

```bash
cd apps/voice && uv run pytest
python3 scripts/ci/boundaries.py
docker build -f apps/voice/Dockerfile .
```
