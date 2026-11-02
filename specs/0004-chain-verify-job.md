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
   in ADR-7 (the tip committed to GitHub with each scorecard), which is a separate piece.

## Acceptance

- [x] An intact chain: the job records `ok` with the number of events checked and the tip, and raises nothing.
- [x] One evidence row edited by hand: the next run raises one critical `chain_broken` alert naming that event's number and its case; later runs don't add another.
- [x] The job is due on the dataset clock, alongside the other jobs.
- [x] The smoke run edits a row by hand on the real services and sees the alert and the chain status point at it.

## Tests

| Acceptance item | Test |
|---|---|
| intact | `apps/core/tests/test_jobs.py::test_an_intact_chain_is_recorded_quietly` |
| edited row | `test_jobs.py::test_a_hand_edited_row_raises_one_alert_at_that_row` |
| dataset clock | `test_jobs.py::test_jobs_are_due_on_the_lab_clock` |
| real services | `scripts/dev/smoke.py` |

## Out of scope

The outside witness (committing the tip to GitHub with each scorecard, ADR-7) and resolving
alerts from the dashboard.

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
