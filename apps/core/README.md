# apps/core: the core service (API and case worker)

**Owner: Abhi. Partner reviewer: Sami.** Part of the money path with `apps/ramp-mock`,
`packages/decision`, `packages/evidence` and the `app`, `audit` and `mock_ramp` schemas.

FastAPI on Railway, one always-on container. It runs each case and is the only thing that
talks to Ramp. It holds three of the design's components: the Ramp adapter and change
detection, the case orchestrator with its worker, and the core API the dashboard calls.
It imports `packages/decision`, `packages/evidence` and `packages/ledger` as libraries,
and the attack lab imports this service's code too, so nothing here may depend on the
real clock or on a fixed dataset.

## Job

Turn Ramp's signals into exactly one case per bank change, move each case through its
states with every side effect run exactly once, keep the hold on until a person releases,
and record all of it in the evidence chain.

## Interfaces

| Direction | Interface | Contract |
|---|---|---|
| In, from Ramp | `POST /webhooks/ramp`: verify `X-Ramp-Signature` (HMAC-SHA256 of the raw body, constant-time compare) before parsing; store the raw delivery in `app.inbound_webhooks` (unique on Ramp's event id); answer 2xx within Ramp's 10-second limit, for duplicates too | Ramp's webhook docs |
| In, from the voice service | `call_ended` with the recorded answers; also Twilio status callbacks for calls that never connected | `packages/contracts/openapi/internal.yaml` |
| In, from the dashboard | REST with the signed-in user's token, passed through to Postgres so `auth.uid()` is the person | `packages/contracts/openapi/dialback.yaml`, served by Prism until each endpoint lands |
| Out, to Ramp | `RampPort`, implemented by one `RampHttpAdapter` that talks to the Ramp-shaped mock or to Ramp's sandbox (spec 0002, D1). `DIALBACK_RAMP=mock|sandbox` picks the base URL and credentials; no other code knows which. `RampConnector.for_dataset()` hands out a dataset's adapter (per-dataset mock tokens) | Ramp Developer API |
| Out, to the voice service | `POST /calls {case_id, attempt}`; the voice service reads the chosen number from the database itself | `internal.yaml` |
| Out, to the Trust Ledger | `select_number(case)` and `lineage(vendor_id)` as Python calls | `packages/contracts/schemas/selection.schema.json` (Inesh) |
| Out, to Postgres | `app.apply_case_event()` for every state change; `audit.verify_chain()` for the badge and the verify job | `supabase/migrations/app/0002_app_functions.sql` |

### RampPort methods

| Method | Ramp endpoint | Notes |
|---|---|---|
| `get_vendor` | `GET /developer/v1/vendors/{vendor_id}` | The vendor record |
| `list_vendors_updated_since` | `GET /developer/v1/vendors?from_updated_at=…` | The poller's sweep |
| `list_vendor_accounts` | `GET /developer/v1/vendors/{vendor_id}/accounts` | Masked to the last four digits |
| `list_vendor_contacts` | `GET /developer/v1/vendors/{vendor_id}/contacts` | Contact edits also go to the Trust Ledger |
| `list_bills_for_vendor` | `GET /developer/v1/bills` | Pages of 100; filter by vendor in code if the API can't |
| `place_vendor_hold` | `POST /developer/v1/vendors/{vendor_id}/hold` | Beta endpoint |
| `release_vendor_hold` | `POST /developer/v1/vendors/{vendor_id}/release` | Beta endpoint |
| `set_bill_payment_date` | Bill update (exact path to confirm) | Restores schedules after a release |
| `get_audit_events` | `GET /developer/v1/audit-logs/events` | Finds who made the change |
| `verify_webhook` | None (local HMAC check) | Checks Ramp's signature |

Stay under Ramp's limit of 200 requests per 10 seconds per IP with a token bucket and
backoff on 429. Ask only for read access to vendors, bills and audit logs plus whatever the
hold endpoints need; never a scope that can create or pay a bill.

## How it works inside

**Detection: webhooks are hints, the poll diff is the truth.**

1. A `vendors.updated` webhook arrives, is verified, stored, acknowledged.
2. The worker fetches the vendor's current accounts and compares them with `app.bank_accounts`, Dialback's own history, because Ramp has no history endpoint. A new account writes one `app.bank_change_events` row and calls `apply_case_event(null, 'change_detected', system, '<source_event_id>:detected', {change_event_id})`.
3. A poller runs the same comparison for every vendor updated since its last sweep, plus a full sweep each night. This catches silent changes.
4. A change-request email forwarded to Dialback is the third way in; the Trust Ledger uses it to mark the change context.
5. Ramp's audit log says who made the change. Map the Ramp user to a Dialback user through `app.user_profiles.ramp_user_id`; when there is no match, `changed_by` stays null and the release will need two approvers (ADR-10).

**Hold, in a fixed order** (a Ramp vendor hold clears each bill's payment date):

1. Save every unpaid bill's `payment_date` into the hold's `original_schedule`.
2. Place the vendor hold.
3. Read the bills back, confirm each one is on hold, catch any bill created in between.
4. Emit `hold_confirmed` with `readback_ok: true`, the `ramp_ref` and the schedule. If the read-back still fails after retries, emit `hold_failed`: the case goes to needs_review and the dashboard shows the red "Hold not confirmed" banner.

Release runs the same steps in reverse: release the hold, read it back, restore the saved
payment dates, emit `hold_release_confirmed`, and let Ramp's own payment approvals run.

**The worker.** Claims due `app.outbox` rows with `FOR UPDATE SKIP LOCKED`, runs each one
under its idempotency key (`{case}:hold:v{n}`, `{case}:call:{n}`, `{case}:release:v{n}`), and
marks it done. A command that keeps failing stops after `max_attempts` and emits
`command_failed`, which sends the case to needs_review with the hold still on. The same loop
fires `app.timers` (retry slots, callbacks, the three-business-day deadline), runs the
poller, the five-minute reconciliation (every vendor with an unfinished case must still be
held in Ramp) and the chain verify job. Either check failing raises a critical alert in
`app.alerts`.

**Commands the schema queues for you**

| Command | Queued by | You do | Then emit |
|---|---|---|---|
| `place_hold` | `change_detected` | the four hold steps | `hold_confirmed` or `hold_failed` |
| `select_number` | `hold_confirmed` | call `ledger.select_number(case)` | `number_resolved` or `no_provable_number` |
| `place_call` | `number_resolved`, `retry_timer_fired` | `POST /calls` to the voice service | nothing; the voice service sends `call_ended` |
| `decide` | `call_ended` with answers | build `CaseFacts`, call `decide()` | `decision_made` |
| `release_hold` | `release_approved` | release, read back, restore dates | `hold_release_confirmed` |
| `notify` | `decision_made` | email or Slack the controller | nothing |

The events, their guards and the bodies they need are in `docs/state-machine.md` and
`packages/contracts/schemas/evidence-event.schema.json`.

**Clock.** Every time read goes through one `Clock` interface. Production uses the real clock;
the attack lab passes a simulated one. Business-day and "90 days old" rules must reproduce.

## Data you own

`app.*` (vendors, bank_accounts, bank_change_events, cases, holds, releases, decisions,
case_transitions, case_event_receipts, outbox, timers, inbound_webhooks, alerts, datasets,
user_profiles, user_roles) and `audit.*` (chains, evidence_events). See `supabase/README.md`.
Direct writes are allowed only to the mirror and plumbing tables; cases, decisions,
releases and receipts move only through the SQL functions, and not even the service role
can write them.

## Rules that bite here

R1, R3, R6, R7. ADR-1 (Postgres is the referee), ADR-3 (tables, not a workflow engine),
ADR-6 (REST holds, polling is the truth), ADR-8 (last four plus fingerprint), ADR-9 (release
with the user's own token), ADR-10 (unknown changer means two approvers).

## How it fails

Closed. A slow call, a crash, a confused answer, a Ramp 500, a timeout: all of them can only
delay a payment, never send one, because the hold goes on at step 2 and comes off only
after a person's release and a confirmed read-back.

## Your days

| Day | Deliverable |
|---|---|
| Sat Oct 31 | Repo, CI, AGENTS.md; Supabase, Railway and Vercel projects; transition-table and evidence-chain migrations (done: `supabase/`) |
| Sun Nov 1 | Walking skeleton: mock webhook → case → hold → stub decision → release console (`specs/0001-walking-skeleton.md`) |
| Mon Nov 2 | Poller and diff; hold read-back and reconciliation |
| Tue Nov 3 | `release_case()` four-eyes tests (done in `supabase/tests/`); chain verify job and badge |
| Wed Nov 4 | Decision engine, one test per rule (`packages/decision`) |
| Thu Nov 5 | Retry timers, deadlines, the unknown-changer path |
| Fri Nov 6 | Chaos mode in the mock; hold-failure paths; held-out scenarios |
| Sat Nov 7 | Queue and case-detail polish |
| Sun Nov 8 | Demo mode and reset (new dataset and chain, nothing deleted) |
| Mon Nov 9 | README, docs and ADRs |
| Tue Nov 10 | Submit the repo and video |

If this slice is behind at the Nov 4 checkpoint, `packages/decision` moves to Inesh.

## Tests you owe

- Property tests: random event sequences never break I1 to I4 (`supabase/README.md` lists them).
- Hash vectors: Python builds the same chain as SQL (`packages/contracts/vectors/`).
- Contract tests: the API matches `dialback.yaml` and `internal.yaml`; examples validate in both languages.
- End-to-end smoke from Nov 2: seed the demo data and run one case from webhook to release in text mode (`demo-smoke` CI job).
- Database tests already exist in `supabase/tests/`; extend them when you add a transition.

## What exists so far

| Piece | Where | Branch |
|---|---|---|
| Flags with safe defaults, settings that fail closed on missing secrets | `flags.py`, `settings.py` | `abhi/core-foundation` |
| Database access as the service role or as the signed-in person | `db.py` | `abhi/core-foundation` |
| The typed wrapper around `apply_case_event` (refusals become `TransitionRefused`, `NotAllowed`, `VersionConflict`) | `events.py` | `abhi/core-foundation` |
| `RampPort`, the HTTP adapter (token bucket, retries, safe pagination), the per-dataset connector | `ramp/` | `abhi/core-foundation` |
| `POST /webhooks/ramp/{dataset_id}`: verify, store, dedupe, alert on bad signatures | `webhooks.py` | `abhi/core-foundation` |
| Detection: initial import, sync a vendor, process stored webhooks; one case per change across every door | `detection.py` | `abhi/core-foundation` |
| New datasets with their own evidence chain (demo reset, lab scenarios) | `datasets.py` | `abhi/core-foundation` |
| The worker: commands with retries, deferral, stale drops and escalation; timers on the dataset clock | `worker.py` | `abhi/core-worker` |
| `POST /internal/calls/{call_id}/ended` per Sami's `internal.yaml`: digits compared then hashed, only the current call counts | `calls.py`, `answers.py` | `abhi/core-worker` |
| Ports to Inesh's `select_number` and Sami's `POST /calls`, both failing closed when the other side is missing | `ports.py` | `abhi/core-worker` |
| Company-name match, calling window | `company.py`, `calling_window.py` | `abhi/core-worker` |
| CLI: `serve`, `new-dataset`, `import-vendors`, `retire-dataset`, `run-job` | `main.py` | `abhi/core-worker`, `abhi/poller-reconcile` |
| Scheduled jobs on the dataset clock: the poller (incremental, with a nightly full sweep) and reconciliation (a missing hold goes back on, with a critical alert) | `jobs.py` | `abhi/poller-reconcile` |
| The chain verify job: recomputes each dataset's evidence chain every five minutes; a broken row raises one critical `chain_broken` alert naming it | `jobs.py` | `abhi/chain-verify` |
| The dashboard API per `dialback.yaml`: me, datasets, chain status, alerts, queue, case detail (timeline, decision, permissions), release and review, each run in Postgres as the signed-in person | `api.py`, `auth.py`, `timeline.py` | `abhi/core-api` |

### Configuration

| Variable | Required | Meaning |
|---|---|---|
| `DATABASE_URL` | yes | Postgres (Supabase) |
| `RAMP_BASE_URL` | yes | The Ramp mock's URL, or Ramp's API |
| `DIALBACK_RAMP` | no | `mock` (default) or `sandbox` (not wired yet; fails at startup) |
| `MOCK_RAMP_SECRET` | with `mock` | Shared with the mock; per-dataset tokens are derived from it |
| `RAMP_WEBHOOK_SECRET` | yes | Verifies `X-Ramp-Signature` |
| `FINGERPRINT_KEY`, `FINGERPRINT_KEY_VERSION` | yes | Keyed fingerprints of bank accounts (32+ characters) |
| `DIGITS_KEY`, `DIGITS_KEY_VERSION` | yes | Keyed hash of spoken digits (32+ characters) |
| `INTERNAL_API_TOKEN` | yes | The voice service's bearer token for call reports (24+ characters) |
| `VOICE_URL` | no | Sami's voice service. Unset: every call is refused and the case goes to a person |
| `DIALBACK_RESOLVER` | no | `stub` (default) calls the ledger's `select_number_stub`; `ledger` its `select_number` |
| `DIALBACK_CALLING_TZ`, `DIALBACK_CALLING_WINDOW`, `DIALBACK_CALLING_DAYS` | no | Default `America/Los_Angeles`, `09:00-16:30`, `mon-fri` |
| `DIALBACK_RUN_WORKER` | no | `0` runs the API without the worker |
| `DIALBACK_RUN_JOBS` | no | `0` turns off the scheduled jobs: poller, reconciliation, chain check (specs 0003, 0004) |
| `DIALBACK_POLL_SECONDS`, `DIALBACK_RECONCILE_SECONDS`, `DIALBACK_FULL_SWEEP_SECONDS`, `DIALBACK_VERIFY_SECONDS` | no | Job intervals on the dataset clock; default 60, 300, 86400 and 300 |
| `DIALBACK_JWT_SECRET` | yes | Verifies the dashboard's JWTs (Supabase's signing secret; a dev secret locally). 32+ characters |

Secrets live in Railway variables and Supabase Vault; never in the repo or in logs.

Run the tests (a throwaway Postgres starts automatically, or set `DATABASE_URL`):

```bash
cd apps/core && uv run pytest
```

The tests run the core and the Ramp mock in process, wired to each other: the real adapter
talks to the mock, and the mock's signed webhooks arrive at the real endpoint.

## Start here

1. `scripts/db/test.sh` once, to see the schema, the functions and the full case walk pass.
2. Read `supabase/migrations/app/0002_app_functions.sql` top to bottom. You call `apply_case_event` everywhere; nothing else moves a case.
3. Read `specs/0001-walking-skeleton.md` and split it into today's tasks.
4. Scaffold the FastAPI app in `src/dialback_core/`: `main.py` (routes), `ramp/port.py` (`RampPort`), `ramp/http.py`, `ramp/mock.py` (thin client for `apps/ramp-mock`), `worker/outbox.py`, `worker/timers.py`, `worker/poller.py`, `cases/events.py` (typed wrappers that call `apply_case_event` with the right body per event), `clock.py`.
5. Flags come from one module: `settings.py` reads `DIALBACK_RAMP`, `DIALBACK_VOICE`, `DIALBACK_RESOLVER`; unknown values fall back to the safe path.
6. Secrets: the Supabase service key, the Ramp client id and secret, the webhook secret and the fingerprint key live in Railway variables and Supabase Vault. Never in the repo, never in logs.

## Open questions for Ramp (ask the organizers, or look up with the Developer MCP or a sandbox call)

1. Can we get a sandbox business with an Admin user, or should we use the self-serve sandbox form?
2. Are the beta vendor hold and release endpoints enabled for sandbox apps, and which scope do they need?
3. Does `vendors.updated` fire when a payee's bank account changes, from the dashboard, the API, the vendor or an ERP sync?
4. What is the audit-log event type for a bank-account change, and is `actor_id` the user who made the edit?
5. Can a sandbox bill be run through to paid, so the demo can show a held bill not being paid?
6. The exact schemas for vendor accounts, contacts and bills; the hold and release responses; the webhook signature encoding; whether listing bills can filter by vendor; the path for updating a bill's payment date.
