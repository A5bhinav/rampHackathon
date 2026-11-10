# 0048: A known vendor's first bank account is a change

**Owner:** Sami (author; `apps/core` is Abhi's, and Abhi reviews)  **Slice:** money path  **Target day:** Mon Nov 9

## Goal

`sync_vendor` baselines a vendor whenever Dialback has mirrored no account for it
(`baseline = not known`). So a vendor Dialback already synced but that had no bank account on
file (paid by check) is baselined when its first account appears: no change, no case. "We're
moving from checks to ACH, here are our details" is a standard fraud email, so this is an R1 gap.
After this spec, first sight means the vendor itself has never been synced into this dataset (no
`app.vendors` row for `(dataset_id, ramp_vendor_id)`). A known vendor's first active account is a
change with no previous account, and it opens a case through the normal path: hold, selection,
call, decision. The design already says "a new account writes one bank_change_events row and
opens a case" (design doc, Ramp adapter and change detection).

## Decisions

1. **First sight is the vendor row, not the accounts.** Phase 1 reads whether the vendor row
   exists and which Ramp account ids are mirrored in one statement, so both come from one
   snapshot (read committed gives one per statement). No row: a baseline, as today (spec 0002, D4
   and Q2), including every vendor in a dataset's first import. A row: every active Ramp account
   not mirrored is a change. Phase 3 still writes the vendor row and its accounts in one
   transaction, so a row never exists without the accounts Ramp listed at that sync.
2. **Why one snapshot.** A webhook and a poll can sync a new vendor at once. A sync that read
   "no accounts" before the other sync committed, and "the vendor exists" after, would report
   every account as new. Deciding first sight in phase 3 (after the other sync wrote everything)
   has the same flaw.
3. **The change has no previous account** (`old_account_id` null, which the schema allows). The
   rest of the path already treats it as optional: the worker's and the call report's left joins,
   `compare_last_four(spoken, new, None)`, the case detail (`old_account: null`, allowed by the
   contract), the timeline text and the evidence envelope (which never carries the old account).
4. **Unchanged:** the actor lookup in Ramp's audit log (no match means an unknown changer and
   two approvers, ADR-10), vendors with known accounts, idempotency keys, the transition table.

## Contract touched

None.

## Acceptance

- [ ] 1. A vendor synced before with no account that now has an active one opens a case: one change row with `old_account_id` null, the case `open`, the hold queued; a re-sync joins it.
- [ ] 2. That case walks the normal path: the hold goes on, R1 holds its bills, a call is placed, code decides. Digits matching the new account verify; any others do not (there is no old account to match).
- [ ] 3. The case detail shows no previous account, the timeline reads as for any change, and the chain verifies.
- [ ] 4. The audit log names the changer as today; no match means an unknown changer (ADR-10).
- [ ] 5. First sight of a vendor with an account is still a baseline (no change, no case), in the initial import or later.
- [ ] 6. A webhook and a poll racing at a vendor's first sight make no change.
- [ ] 7. A vendor with known accounts that gets a new one opens a case, as today.
- [ ] 8. An unchanged re-sync creates nothing, for a vendor with accounts and one without.
- [ ] 9. Fails closed: if Ramp fails mid-sync (the audit-log read), nothing is written, so the next sync (retry, poller or sweep) still sees the account as new and opens the case. A failed sync never baselines it.

## Tests (written first)

| Acceptance item | Test |
|---|---|
| 1 | `apps/core/tests/test_detection.py::test_a_known_vendor_with_no_account_gets_its_first_account_and_a_case` |
| 2 | `apps/core/tests/test_worker.py::test_a_first_account_case_walks_the_money_path` (match and mismatch) |
| 3 | `apps/core/tests/test_api.py::test_a_first_account_change_shows_no_previous_account` |
| 4 | `test_detection.py::test_a_first_account_from_an_unknown_changer_needs_two_approvers` |
| 5 | `test_detection.py::test_first_sight_of_a_vendor_with_an_account_is_still_a_baseline`, `test_initial_import_is_a_baseline_not_a_change` (existing) |
| 6 | `test_detection.py::test_a_webhook_and_a_poll_racing_at_first_sight_make_no_change` |
| 7 | `test_detection.py::test_a_bank_change_webhook_opens_one_case_and_queues_the_hold` (existing) |
| 8 | `test_detection.py::test_an_unchanged_re_sync_creates_nothing` |
| 9 | `test_detection.py::test_a_failed_audit_lookup_leaves_the_first_account_new_for_the_next_sync` |

Items 5 to 8 pass before the fix too; they guard behavior the fix must keep.

## Out of scope

- A reactivated old account (Ramp turns a mirrored account active again). The decision rules'
  "digits match the old account" check would misfire for a genuine revert.
- An account edited in place under the same Ramp account id.
- Holding a brand-new vendor's first payment (spec 0002, Q2): first sight of the vendor stays a
  silent baseline.

## Open questions

| # | Question | Who answers |
|---|---|---|
| Q102 | Does Ramp log a vendor's first bank account as `VENDOR_BANK_ACCOUNT_UPDATED` (the type the actor lookup reads), or as another audit-log type? If another, every first-account case has an unknown changer and needs two approvers. That fails closed, but it is stricter than intended. | Ramp sandbox; then Abhi |

## Commands

```bash
cd apps/core && DATABASE_URL=... uv run pytest
cd apps/ramp-mock && DATABASE_URL=... uv run pytest
DATABASE_URL=... scripts/db/test.sh
python3 scripts/ci/boundaries.py
uv run ruff check apps packages
```
