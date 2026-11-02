# 0004: The scheduled evidence-chain check

**Owner:** Abhi  **Slice:** money path  **Target day:** Tue Nov 3

## Goal

The evidence log is tamper-evident only if someone checks it (design, "How the evidence log
stays tamper-evident": "Checked, not trusted"). Today the chain is recomputed only when a
person opens a case page. This adds the verify job the design calls for: every five minutes,
recompute each dataset's chain and raise a top-priority alert at the first broken row. It is
also the demo's sixth beat: one row edited by hand, and the job points straight at it.

## Decisions

1. **A fourth scheduled job, `verify_chain`,** on the same rails as the poller and
   reconciliation (spec 0003): one row per dataset in `app.job_runs`, due on the dataset's
   clock, leased. Default interval five minutes (`DIALBACK_VERIFY_SECONDS`).
2. **It calls `audit.verify_chain`**, the same SQL the dashboard badge uses, so there is one
   definition of "intact".
3. **A broken chain is a finding, not a job failure.** The job records `ok`, `checked`,
   `first_bad_seq`, `detail` and the chain tip in its result, and raises one critical
   `chain_broken` alert per dataset, naming the event number and pointing at that event's
   case. It does not repeat the alert every five minutes, and never resolves it on its own:
   a chain that was once broken stays on record even if the row is later put back.
4. **The tip is kept in the result** (`tip_seq`, `tip_hash`), ready for the outside witness
   in ADR-7 (the tip committed to GitHub with each scorecard), which is a separate piece. The
   tip is recorded even when the chain is broken, so the witness must commit it only when
   `ok` is true. A failed run keeps the last good result.
5. **The chain only grows, so each run also checks the last run's tip.** `audit.verify_chain`
   alone passes a chain whose last rows were deleted, or one recomputed from some row onward.
   So when the chain checks out, the job also looks up the row at the tip the last run
   recorded: if it is gone or its hash changed, that is a break at that row. This is a
   tripwire, not a witness: `app.job_runs` is writable by the service role, and the outside
   witness (ADR-7) is still what makes a rewrite provable.
6. **The alert trusts nothing in the bad row.** It links a case only when the row's `case_id`
   names a real case, and never for a gap (the row after a missing one belongs to another
   event). One open `chain_broken` alert per dataset is enforced by a unique index, so two
   racing checks can't raise two.

## Acceptance

- [x] An intact chain: the job records `ok` with the number of events checked and the tip, and raises nothing.
- [x] One evidence row edited by hand: the next run raises one critical `chain_broken` alert naming that event's number and its case; later runs don't add another.
- [x] The alert stays open after the row is put back.
- [x] A row that also lies about its case still raises the alert, without a case.
- [x] A deleted row is a gap: the alert names the row after it and no case.
- [x] A chain shortened, or its tip recomputed, since the last run is caught at the last run's tip.
- [x] The job is due on the dataset clock, alongside the other jobs.
- [x] The smoke run edits a mid-chain row by hand on the real services and sees the alert and the chain status point at it and its case.

## Tests

| Acceptance item | Test |
|---|---|
| intact | `apps/core/tests/test_jobs.py::test_an_intact_chain_is_recorded_quietly` |
| edited row, stays open | `test_jobs.py::test_a_hand_edited_row_raises_one_alert_at_that_row` |
| lying case_id | `test_jobs.py::test_a_row_that_lies_about_its_case_still_raises_the_alert` |
| deleted row | `test_jobs.py::test_a_deleted_row_is_a_gap_and_names_no_case` |
| shortened chain, failed run keeps the tip | `test_jobs.py::test_a_shortened_chain_is_caught_by_the_last_tip` |
| recomputed tip | `test_jobs.py::test_a_recomputed_tip_is_caught` |
| dataset clock | `test_jobs.py::test_jobs_are_due_on_the_lab_clock` |
| real services | `scripts/dev/smoke.py` |

## Out of scope

The outside witness (committing the tip to GitHub with each scorecard, ADR-7) and resolving
alerts from the dashboard.

## Open questions

- **Q10.** A job that keeps failing (the chain check included) is recorded on its row but
  shown nowhere. Should repeated failures raise an alert, or should job status go on the
  dashboard?
- **Q11.** `audit.verify_chain` checks the hashes but not the copied columns (`case_id`,
  `type`, `actor_*`, `occurred_at`) against `body_jcs`, so editing only `case_id` moves an
  event off its case's timeline and the chain still reads intact. Fix it in the audit
  schema, with its own SQL test.

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
