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

- [ ] `POST /_mock/vendors/{id}/bank-change` on `apps/ramp-mock` sends a signed `vendors.updated` webhook to `apps/core`.
- [ ] `apps/core` verifies the signature, stores the delivery in `app.inbound_webhooks`, answers 2xx within 10 seconds, and a duplicate delivery is stored once and still answered 2xx.
- [ ] The worker diffs the vendor's accounts against `app.bank_accounts`, writes one `app.bank_change_events` row, and calls `apply_case_event(... 'change_detected' ...)`; the case is `open` with the deadline timer set.
- [ ] The `place_hold` command saves the bill schedule, places the hold on the mock, reads it back, and emits `hold_confirmed {readback_ok: true, ramp_ref, original_schedule}`; the case is `held` and the mock's bills show `payment_date` cleared.
- [ ] `DIALBACK_RESOLVER=stub` returns a contract example; the worker emits `number_resolved`; the case is `calling`.
- [ ] `DIALBACK_VOICE=text` returns a scripted `call_ended` with `answers_recorded`; the worker emits `decision_made {outcome: verified}` from a stub `decide()`; the case is `awaiting_release`.
- [ ] The release console shows the case to an approver who is not the changer; a release with a reason calls `app.release_case()` under the approver's token; the case is `releasing`.
- [ ] The `release_hold` command releases on the mock, reads it back, restores the saved payment dates, and emits `hold_release_confirmed`; the case is `released`.
- [ ] `audit.verify_chain` reports the dataset's chain intact, and the case-detail page shows the timeline and the green badge.
- [ ] A reset starts a new dataset and chain; nothing is deleted.
- [ ] Failure path: with `fail_next_hold` set on the mock, the command retries, then emits `hold_failed`; the case is `needs_review` and the dashboard shows "Hold not confirmed".

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
uv run --package dialback-core pytest
```
