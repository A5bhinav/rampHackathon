# 0002: How the money path plugs into everything else

**Owner:** Abhi  **Slice:** money path  **Target day:** Sun Nov 1, before and alongside 0001

## Goal

The walking skeleton (0001) is the first time every slice touches every other one. This
spec records the integration decisions the skeleton code depends on, so Inesh's ledger,
Sami's voice service and attack lab, and the dashboard can each build against them
without guessing. Each decision names the problem it solves, what changes, and who has to
agree. Nothing here changes a rule (R1 to R9) or reverses an ADR.

## Branch plan

Each branch is stacked on the one before it and merges in this order. Every branch keeps
`scripts/db/test.sh` green.

| # | Branch | What lands |
|---|---|---|
| 1 | `abhi/skeleton-contracts` | This spec; `openapi/dialback.yaml`; evidence-schema additions (D6, D7) |
| 2 | `abhi/skeleton-db` | Migration `app/0004` (D2, D6, D8); a Postgres test fixture beside the database scripts |
| 3 | `abhi/ramp-mock` | The Ramp-shaped mock service (D1, D3) |
| 4 | `abhi/core-foundation` | Settings and flags, the database layer, the Ramp adapter, webhook detection (D1, D3, D4, D9) |
| 5 | `abhi/core-worker` | Command handlers, the call report endpoint, the decision skeleton (D5, D6, D7, D8, D10, D11) |
| 6 | `abhi/core-api` | The dashboard API with the user's token passed to Postgres (D9, D12) |
| 7 | `abhi/web-skeleton` | Queue, case detail, release console |
| 8 | `abhi/demo-smoke` | One case end to end in CI, plus the hold-failure path |

## Decisions

### D1. One Ramp adapter, pointed at the mock or the sandbox

**Problem.** The design names two `RampPort` implementations, `RampHttpAdapter` and
`RampMock`. But the mock copies Ramp's paths and fields, so a second client class would
test the mock's client instead of the real one.

**Decision.** One `RampHttpAdapter` talks to both. `DIALBACK_RAMP` picks the base URL and
the credentials; nothing else knows which is running. The adapter is tested against the
mock, so the code that will talk to the sandbox is the code under test.

**Mock credentials, per dataset.** The lab runs every scenario in its own dataset with its
own mock business, in parallel. The mock accepts a bearer token
`mock.<dataset_id>.<hmac>` where the HMAC is over the dataset id with a shared
`MOCK_RAMP_SECRET`. The core mints it from the case's dataset, the mock checks it and serves
that dataset's business only. No credentials are stored in tables, and one scenario can
never read another's vendors.

### D2. One clock per dataset, kept in the database

**Problem.** ADR-11 says the lab runs production code on a simulated clock, and rules like
"three business days" and "inside the calling window" must reproduce. But the SQL functions
read `clock_timestamp()`, and a Python-only clock would leave the database on real time.

**Decision.** `app.datasets.sim_now` holds a lab dataset's simulated time; `app.now(dataset)`
returns it for `lab` datasets and the real clock for every other kind. Every SQL function
and the worker read time through it. The lab advances a scenario's clock by updating one
row, and due timers fire on the next worker tick. Demo and dev datasets can never be given a
fake time. `evidence_events.recorded_at` stays on the real clock, so both times are kept.

**Sami:** the lab sets `sim_now` to `clock.start` when it creates the scenario's dataset,
then moves it forward. No other hook is needed.

### D3. Webhooks name their dataset in the URL

`POST /webhooks/ramp/{dataset_id}`. The subscription URL carries the dataset, so the lab's
parallel scenarios and the production dataset share one endpoint. An unknown dataset gets
404 and nothing is stored.

### D4. How a bank change is recognized

- **Account identity is Ramp's account id.** Ramp returns account numbers masked to the
  last four digits, so the keyed fingerprint of ADR-8 (`HMAC(routing + account)`) cannot be
  computed from what Ramp sends. Until we know whether any endpoint returns the full
  number, the fingerprint is `HMAC(key, routing | last4 | ramp_account_id)`. It still never
  holds a full number, but it cannot answer "have we seen this account at another vendor".
  Open question Q1.
- **One change, one case, whichever door finds it.** The change's `source_event_id` is
  `ramp-account:<ramp_account_id>`, so a webhook, the poller and a replay all dedupe to the
  same row and the same case.
- **First sight is a baseline.** A vendor with no history in our mirror is baselined, not
  treated as a change. That is only safe after the dataset's initial import, which the seed
  and the onboarding step run before monitoring starts. Open question Q2.
- **Who changed it.** Ramp's audit log names the actor; `app.user_profiles.ramp_user_id`
  maps it to a Dialback user. No match means an unknown changer and two approvers (ADR-10).

### D5. The bill schedule is saved before the hold, durably

A Ramp hold clears every unpaid bill's payment date. If the worker crashed after placing the
hold, a retry would read the cleared dates and lose the schedule. So the worker writes the
schedule into `app.holds` (status `placing`) before placing the hold, and every retry reuses
the stored schedule. A re-hold after a second change keeps the first schedule.

### D6. Calls are bound to the case's current generation

**Problem.** Sami's draft keys calls as `{case_id}:call:{attempt}`. After a second change
re-holds a case, the attempt count restarts at 1, so the new call reuses the old key: the
voice service would return the old call instead of dialing, and the old call's report would
be accepted for the new generation.

**Decision.**

1. The core sends `Idempotency-Key: {case_id}:call:v{version}:{attempt}` to `POST /calls`.
   The header is a free-form string in the contract, so nothing breaks; the description
   should be updated (ask to Sami).
2. When the voice service answers 202 with a `call_id`, the core appends a new event,
   `call_started {call_id, attempt}`. It puts call start into the evidence chain (the design
   lists it) and pins `cases.current_call_id`.
3. `call_ended` is accepted only for the current call. A late report from an earlier
   generation gets 409, which the contract already defines.
4. The core's own event key for a report is `{case_id}:call:{call_id}:ended`, unique across
   generations. The voice service's `Idempotency-Key` still dedupes HTTP replays.

### D7. Spoken digits are compared at report time and never stored

Sami's contract carries the spoken digits to the core in memory only. So when a report
arrives, the core:

1. computes `last_four_result` with `packages/decision` (match, matches_old_account,
   near_miss, mismatch, not_provided, ambiguous, inconsistent) against the case's new and old
   account last fours;
2. replaces the digits with the stored shape (`digits_hmac`, `digit_count`, `key_version`), where
   `digits_hmac` is HMAC-SHA256 with the server's digits key over `<call_id>:<digits>`, so the same
   four digits on two calls never hash alike;
3. appends `call_ended` with the stored answers, `last_four_result`, and Sami's `checks`.

The later `decide` command is then a pure function of stored facts, with no digits in reach.

**Contract additions (this branch).** `CallEndedBody` gains `last_four_result`,
`contact_point_id` and `checks` (Sami proposed the last two). `CallChecks` moves into
`evidence-event.schema.json` `$defs`, unchanged, so the stored body and the request share one
definition. **Sami:** please point `internal.yaml`'s `CallChecks` at
`../schemas/evidence-event.schema.json#/$defs/CallChecks` when you rebase.

### D8. Fixes found by walking a re-hold end to end

These are bugs in the merged `app` migrations that the skeleton would have hit. Migration
`0004` fixes them; earlier files are never edited.

- `hold_confirmed` was only allowed from `open`, so a re-hold (`held` at version 2) could
  never be confirmed and the case stuck in `held`. Now `held → held` is allowed.
- `place_call` keys were `{case}:call:{n}` without the version, so after a re-hold the new
  call collided with the old one and was never queued. Now `{case}:call:v{version}:{n}`.
- The `decide` command now carries the stored answers, the last-four result and the checks
  from `call_ended`, so deciding never has to reach into Sami's tables.

### D9. A person acts as themselves in Postgres

The core verifies the dashboard's JWT (Supabase's signing secret; HS256 now, asymmetric keys
later), then runs the request in a transaction with `set local role authenticated` and
`request.jwt.claims` set, exactly as Supabase's own API would. `auth.uid()` is the person,
row-level security applies, and `release_case()` sees who is releasing (ADR-9). System work
runs with `set local role service_role` and no claims, so the grants in the migrations are
exercised, not bypassed.

**Local development without Supabase.** With `DIALBACK_AUTH_MODE=dev` (never in
production builds), the dashboard's server mints short-lived JWTs for seeded users with a dev
secret. The core verifies them with the same code it uses in production; there is no bypass.

### D10. Who builds what in the skeleton

The skeleton is a team milestone, and each slice brings its own piece. The core reaches
the other slices only through two ports, and it never contains a stand-in for someone
else's code.

| Piece | Owner | How the core reaches it |
|---|---|---|
| `select_number` stub that returns contract examples, then the real rule | Inesh | `NumberResolver` port; `DIALBACK_RESOLVER=stub` calls the ledger package's stub, `ledger` its real rule |
| Demo seed v0 | Inesh | Loaded into a dataset before monitoring starts |
| Text phone line and the voice service | Sami | `CallPlacer` port; production wiring is the `internal.yaml` client |
| Everything in the money path | Abhi | |

If the ledger package has no stub yet, the resolver fails closed: the case goes to
needs_review with "no provable number", exactly as R7 asks. The core's tests and the
skeleton's smoke run inject **test doubles** for the two ports, named as doubles, that
behave as the contracts say. When Inesh's stub and Sami's text line land, the smoke run
switches to them and the doubles stay only in unit tests.

No new flag is needed: `DIALBACK_RAMP` and `DIALBACK_RESOLVER` as designed, and
`DIALBACK_VOICE=text|live` stays Sami's flag inside the voice service.

### D11. What the core needs from `select_number`

Until Inesh's `selection.schema.json` lands, the core's `NumberResolver` port expects:
`number_selection_id`, `rule_version`, and either a `contact_point_id` (a pick) or no pick
with a reason. **Inesh:** please make sure the schema has those fields; the e164,
alternates and candidate list are yours to shape, and the voice service reads the number
from your table, never from the core. The core calls `select_number` with the case id, the
dataset id, the vendor id and the cutoff time (the change's detection time), so a selection
reads only rows recorded before the case opened.

### D12. Dashboard API

`packages/contracts/openapi/dialback.yaml`: who am I, datasets, the case queue, case detail
(timeline from evidence, chain status, the latest decision, and what the caller may do),
release and review actions with the version the person saw, open alerts. Every sentence that
explains a decision comes from a template in the core, never from a model, and says
"provable" or "not provable", never "safe".

## Open questions

| # | Question | Who answers |
|---|---|---|
| Q1 | Does any Ramp endpoint return a vendor account's full number, so the fingerprint can follow ADR-8 exactly? | Ramp, or the sandbox |
| Q2 | Can a vendor appear for the first time with a bank account that changed seconds ago? If so, first sight cannot be a silent baseline. | Ramp; then Abhi |
| Q3 | Webhook payload shape and signature encoding (hex or base64, any prefix). The mock signs hex. | Ramp docs |
| Q4 | Field names for a vendor's hold and a bill's hold flag, for read-back. The mock uses `hold` and `is_on_hold`. | Ramp OpenAPI spec |
| Q5 | Supabase project keys: HS256 legacy secret or asymmetric JWKS. The verifier supports HS256 today. | Abhi, when the project exists |
| Q6 | A `blocked` case keeps its hold forever unless another change arrives. What ends it: vendor offboarding, or a manual unblock with two people? | Team |
| Q7 | The live final is in the evening, outside the 9:00 to 16:30 calling window, so a live demo call would wait until morning. Widen the window for the demo dataset only (`DIALBACK_CALLING_WINDOW`), or run the live call in the vendor's time zone? | Team, before Nov 19 |
| Q8 | The skeleton's `decide()` records three facts it cannot check yet (`U01` to `U03`). Who supplies them for the Nov 4 engine: the ledger's change context (Inesh) and per-turn speaker identity (Sami)? | Inesh, Sami |

## Acceptance

- [ ] Sami confirms D6 and D7 and rebases `internal.yaml` onto the shared `CallChecks`.
- [ ] Inesh confirms D11.
- [ ] The contracts validate (`packages/contracts/scripts/validate.py`), including both OpenAPI files.
- [ ] Each later branch cites the decision it implements.
