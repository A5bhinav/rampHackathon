# 0001: Walking skeleton, end to end

**Owner:** Abhi (with stubs from Inesh and Sami)  **Slice:** money path  **Target day:** Sun Nov 1

## Goal

One bank change travels the whole path on `main`: a mock webhook opens a case, the hold goes
on and is confirmed, a stub picks a number, a stub decision lands, an approver releases from
the console, the hold comes off, and every step is in the evidence chain. From this day on,
every later change improves something that already runs, and `main` runs the demo every
evening.

## Contract touched

None new. Uses `packages/contracts/schemas/evidence-event.schema.json` (done) and contract
examples for the `select_number` stub (`selection.schema.json`, Inesh) and the `call_ended`
answers (`call-answers.schema.json`, Sami).

## Acceptance

Status as of the `abhi/*` branches (spec 0002 has the branch plan). Each item names where
it is proven.

- [x] `POST /_mock/vendors/{id}/bank-change` on `apps/ramp-mock` sends a signed `vendors.updated` webhook to `apps/core`. (`apps/ramp-mock/tests`, the smoke run)
- [x] `apps/core` verifies the signature, stores the delivery in `app.inbound_webhooks`, answers 2xx, and a duplicate delivery is stored once and still answered 2xx. (`apps/core/tests/test_detection.py`)
- [x] The worker diffs the vendor's accounts against `app.bank_accounts`, writes one `app.bank_change_events` row, and calls `apply_case_event(... 'change_detected' ...)`; the case is `open` with the deadline timer set. (`test_detection.py`, SQL test 002)
- [x] The `place_hold` command saves the bill schedule, places the hold on the mock, reads it back, and emits `hold_confirmed {readback_ok: true, ramp_ref, original_schedule}`; the case is `held` and the mock's bills show `payment_date` cleared. (`test_worker.py`)
- [x] The resolver picks a number and the worker emits `number_resolved`; the case is `calling`. **Uses a test double** for Inesh's `select_number` until the ledger's stub lands; the real resolver already fails closed without it. (`test_worker.py`)
- [x] A call is placed and reported per `internal.yaml`, `decide()` runs, and the case is `awaiting_release`. **Uses a test double** for Sami's voice service until the text line lands; the core side (call start, digits compared then hashed, only the current call counts) is real. (`test_worker.py`, the smoke run)
- [x] The release console shows the case to an approver who is not the changer; a release with a reason calls `app.release_case()` under the approver's token; the case is `releasing`. (`apps/core/tests/test_api.py`, checked in a browser)
- [x] The `release_hold` command releases on the mock, reads it back, restores the saved payment dates, and emits `hold_release_confirmed`; the case is `released`. (`test_worker.py`, the smoke run)
- [x] `audit.verify_chain` reports the dataset's chain intact, and the case-detail page shows the timeline and the green badge; a hand-edited row turns it red at that row. (`test_api.py`, checked in a browser)
- [x] A reset starts a new dataset and chain; nothing is deleted. (`dialback-core new-dataset`, `retire-dataset`; a dashboard button comes with demo mode on Nov 8)
- [x] Failure path: with Ramp's hold endpoint down (the mock's `fail_holds`), the command retries, then emits `hold_failed`; the case is `needs_review` and the dashboard shows "Hold not confirmed". (the smoke run, `test_worker.py`)

## Follow-ups found while building

- Alerts are never resolved, so banners stay up after the cause is handled. Resolve an alert when its case moves on (or a person dismisses it, logged).
- The poller (Nov 2) and the five-minute reconciliation that re-checks every unfinished case is still held in Ramp are not built yet; `import_all` is the sweep they will call.
- Supabase sign-in waits for the project (spec 0002, Q5); dev sign-in is local only.
- The demo's evening calls fall outside the 9:00 to 16:30 window (spec 0002, Q7); the local stack opens the window all day.

## Tests (written first)

| Acceptance item | Test |
|---|---|
| webhook verify, store, dedupe | `apps/core/tests/test_webhooks.py` |
| diff and detection | `apps/core/tests/test_detection.py` |
| hold order and read-back | `apps/core/tests/test_hold.py` against the mock's SQL functions |
| state walk | already covered by `supabase/tests/002_case_machine.sql`; add the e2e `demo-smoke` script |
| failure path | `apps/core/tests/test_hold_failure.py` |

## Out of scope

The poller (Nov 2), the real decision engine (Nov 4), the Trust Ledger (Nov 2 and 3), live
calls (Nov 3), the unknown-changer path (Nov 5).

## Open questions

- Which host the mock webhook sender reaches `apps/core` on in Railway (service URL and the shared `RAMP_WEBHOOK_SECRET`).
- Whether the demo-smoke CI job runs against Railway or against both services started in the job.

## Commands

```bash
scripts/db/test.sh
```

```bash
cd apps/core && uv run pytest
```

```bash
uv run python scripts/dev/stack.py --smoke
```
