# supabase: the database, one schema per owner

Postgres is the referee (ADR-1). The rules that protect money, the state machine, the
four-eyes release and the evidence chain, live in SQL functions behind row-level security,
so they hold even if a service has a bug, a server key leaks, or a coding agent gets
something wrong.

## Layout and ownership

```
supabase/
  migrations/
    _bootstrap/   schemas, roles, grants                              shared, applied first
    audit/        chains, evidence_events, append_evidence, verify    Abhi
    app/          cases, holds, releases, plumbing, the two functions Abhi
    ledger/       documents, observations, contact_points, selections Inesh   (to add)
    voice/        calls, call_answers                                 Sami    (to add)
    lab/          config_versions, scenarios, lab_runs, lab_results   Sami    (to add)
    mock_ramp/    the Ramp-shaped mock's tables and behavior          Abhi
  tests/          SQL tests run by scripts/db/test.sh on every PR     each owner adds theirs
  seed/           demo and dev seeds                                  each owner (to add)
```

`scripts/db/apply.sh` applies the folders in that order (`_bootstrap`, `audit`, `app`,
`ledger`, `voice`, `lab`, `mock_ramp`), each file in one transaction, skipping folders that
don't exist yet. Name files `NNNN_what.sql` inside your folder and never edit a file after
it has reached `main`; add the next number.

> The Supabase CLI reads a flat `supabase/migrations/*.sql`. We keep per-owner folders so
> CODEOWNERS and parallel work stay clean, and apply with our own script (CI, laptops, and
> `DATABASE_URL` pointing at the hosted project). If someone wants `supabase db push`, a
> five-line script can concatenate the folders in order into a flat timestamped copy.

## Run it

```bash
scripts/db/test.sh
```

Starts a throwaway Postgres 16 (or uses `DATABASE_URL`), installs a local stand-in for
`auth.uid()` only if it is missing, applies every migration, runs every test file inside a
rolled-back transaction, redraws `docs/state-machine.md`, and checks the contract schemas
and the shared hash vectors. The `db` GitHub Actions job runs the same script.

**Python tests that need the database** (apps/core, apps/ramp-mock) use the same setup
through `scripts/db/pgtemp.py`: a pytest plugin with a session fixture, `database_url`, that
migrates a fresh database the same way. Put `scripts/db` on pytest's `pythonpath` and add
`-p pgtemp` to `addopts`. Tests isolate themselves by creating their own dataset, as the lab does.

## What is in each schema

### `audit` (Abhi)

| Object | Role |
|---|---|
| `audit.chains` | One chain per dataset. A demo reset starts a new chain rather than deleting rows. |
| `audit.evidence_events` | Append-only. `body_jcs` is the exact canonical JSON that was hashed; `content_hash`, `prev_hash`, `hash` form the chain. Unique `(chain_id, prev_hash)` rules out forks. No update or delete grant for anyone, and triggers refuse all three. |
| `audit.jcs(jsonb)` | RFC 8785 canonical JSON in SQL (keys by UTF-8 bytes, Postgres string escaping, integers only). |
| `audit.append_evidence(chain, envelope, expected_hash?)` | The only way to write evidence: canonicalize, hash, lock the chain, link to the tip, insert. |
| `audit.verify_chain(chain)` | Recomputes everything; names the first bad row. The dashboard badge and the five-minute verify job. |
| `audit.chain_tip(chain)` | For the GitHub witness commit. |

### `app` (Abhi)

Core tables: `datasets`, `vendors`, `bank_accounts` (last four and a keyed fingerprint,
never the number), `bank_change_events`, `cases`, `holds`, `releases`, `decisions`.
Plumbing: `case_transitions` (the state machine as rows), `case_event_receipts`
(idempotency), `outbox` (commands the worker claims with `FOR UPDATE SKIP LOCKED`),
`timers`, `inbound_webhooks`, `alerts`, `user_profiles`, `user_roles`.

| Function | Role |
|---|---|
| `app.apply_case_event(case_id, event_type, actor, idempotency_key, body)` | The one door. Checks the caller (a person must be `auth.uid()`; the system has no user), looks up the move in `case_transitions`, checks the actor kind and the guard (`app.eval_guard`), runs the side effects, queues follow-up commands and timers, moves the case, appends evidence, records the receipt. A repeated key returns `already_applied`. |
| `app.release_case(case_id, expected_version, action, reason)` | Four-eyes with the user's own token: approver role, not a changer, not the reviewer, version matches, written reason. When the changer is unknown, the first call co-approves and the second approver releases (ADR-10). |
| `app.eval_guard(guard_key, case, actor_id, body)` | The named guards in the transition table, in one place. |
| `app.now(dataset_id)` | The one clock. A lab dataset's simulated time (`app.datasets.sim_now`), otherwise the real clock. Every time the functions write comes from it. |
| `app.advance_clock(dataset_id, to)` | Lab datasets only, forward only. The attack lab sets a scenario's clock to `clock.start`, then moves it; due timers fire on the worker's next tick. Service role. |

Access: everyone signed in can read; the service role writes mirrors and plumbing;
`cases`, `decisions`, `releases` and `case_event_receipts` are written only by the functions,
which run as their owner. Not even the service role can write them directly.

### `mock_ramp` (Abhi)

Ramp-shaped tables (`businesses`, `users`, `vendors`, `vendor_accounts`, `vendor_contacts`,
`bills`, `vendor_holds`, `audit_events`, `webhook_subscriptions`, `webhook_deliveries`,
`settings`) and the behavior that must match Ramp: `change_bank_account()`, `place_hold()`
(clears unpaid bills' payment dates), `release_hold()` (does not restore them),
`queue_webhook()` (duplicates, reordering, delay, silent mode), `consume_switch()`.

## The state machine

Rendered from the table into `docs/state-machine.md`. Ten states; `released` is the only
finished one. Two moves need a person: clearing a review and approving a release. Every
failure path ends in a state where the hold stays on.

## Invariants the SQL enforces (and the property tests and verify job check)

- **I1.** A released case has an approval for its current version from a person who isn't the changer.
- **I2.** While a case is unfinished, its vendor is held in Ramp (the reconciliation job alerts if not).
- **I3.** The only way into `released` is through `releasing`.
- **I4.** The AI emits no events; code turns its tool calls into the answers inside `call_ended`.

## Decisions made while writing this, for the team to confirm

These extend the design doc; none contradict it. Shout if any is wrong.

1. **`cases.version` is the change generation**, not a per-transition counter. It starts at 1 and goes up only when another change arrives while the case is unfinished. Person events pass `expected_version`; a stale one is refused. This keeps "an approval for its current version" meaningful.
2. **Transitions added from the doc's prose:** `release_rejected` (approver rejects from `awaiting_release` to `blocked`), `deadline_reached` and `command_failed` (to `needs_review`, hold on), `call_timeout`, the `release_co_approved` self-loop for the unknown-changer path, and explicit re-hold rows from every unfinished state on `change_detected`.
3. **`blocked` is unfinished.** The hold stays on, and the only ways out are a new change (back to `held`) or, later, a vendor offboarding we have not designed. Open question.
4. **Idempotency keys carry the version**: `{case}:hold:v{n}`, `{case}:select:v{n}`, `{case}:call:{attempt}`, `{case}:decide:v{n}:{attempt}`, `{case}:release:v{n}`.
5. **`changer_ids` and `has_unknown_changer`** on `cases`: every known changer across generations is excluded from release; any generation with no known changer triggers the two-approver path.
6. **Guards are evaluated in SQL** from the event body (`readback_ok`, `result`, `outcome`, `in_window`, `reason`, `contact_point_id`), so the worker cannot skip a step by naming a target state.
7. **Deadline** is three business days (weekends skipped, no holiday calendar).
8. **`mock_ramp` field names** marked "confirm" in the migration are to be checked against Ramp's OpenAPI spec.
9. **Migration `app/0004`** (spec `specs/0002-skeleton-integration.md`) adds the dataset clock (D2), the `call_started` event that pins `cases.current_call_id` so only the current call's report counts (D6), version-qualified call keys, `hold_confirmed` from `held` for re-holds, and a `decide` command that carries the stored call body (D8). Tests `006` and `007` fail without it.

## Hosted project setup (once, by Abhi)

- Create the Supabase project; set `DATABASE_URL` as a GitHub Actions secret for a dev branch if you want CI against it (the default CI uses a service container).
- API settings: expose the `app` and `audit` schemas (then `ledger`, `voice`, `lab` as they land). `public` stays empty.
- `app.user_profiles` and `app.user_roles` reference `auth.users` automatically when applied on Supabase.
- Realtime: the `app` migration adds `app.cases` and `app.alerts` to the `supabase_realtime` publication when it exists.
- The fingerprint HMAC key and the Ramp webhook secret go in Vault or Railway variables, never in SQL.
