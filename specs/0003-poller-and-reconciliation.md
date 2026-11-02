# 0003: The poller and the reconciliation job

**Owner:** Abhi  **Slice:** money path  **Target day:** Mon Nov 2

## Goal

Two scheduled jobs that close the gaps webhooks and one-time holds leave open (design,
"Ramp adapter and change detection" and "Case orchestrator"):

- **The poller** finds bank changes no webhook announced. Nobody has confirmed that Ramp
  sends `vendors.updated` for a bank-account change (spec 0002, open questions), so without
  it a silent change is never seen and its bills get paid (R1).
- **The reconciliation job** checks, every five minutes, that every vendor with an
  unfinished case is still held in Ramp (invariant I2). A hold someone lifted by hand in Ramp,
  or one Ramp never accepted, is put back and raised as a critical alert (R1, R7).

## Decisions

1. **Jobs are rows, scheduled on the dataset's clock.** `app.job_runs` keeps one row per
   dataset and job (`poll`, `full_sweep`, `reconcile`): when it last started and finished,
   the result, the error, and a lease so two workers never run the same job at once. A job is
   due when its interval has passed on `app.now(dataset)`, so the lab drives polling and
   reconciliation by moving a scenario's clock (spec 0002, D2), exactly as it drives timers.
2. **The poller is incremental, with a nightly full sweep.** `poll` asks Ramp for vendors
   updated since a watermark and syncs each one through the same `sync_vendor` the webhook
   path uses, so every door still converges on one change and one case. The watermark is
   Ramp's clock (the time the sweep started, minus a two-minute overlap), never the dataset's.
   A dataset's first `poll` is a full import: the baseline. `full_sweep` syncs every vendor
   once a day, catching a change Ramp didn't reflect in `updated_at`.
3. **Reconciliation puts a missing hold back, then alerts.** For every unfinished case whose
   hold should be on (`held`, `calling`, `retry_wait`, `deciding`, `awaiting_release`,
   `needs_review`, `blocked`), it reads the vendor back from Ramp. If the hold is gone, it
   places it again at once (holds only ever delay a payment, so this fails closed), marks the
   hold confirmed, and raises one critical `vendor_not_held` alert per case (not one every
   five minutes). `open` and `releasing` are skipped: a hold is being placed or deliberately
   lifted. If Ramp refuses, the case keeps its existing "Hold not confirmed" alert and the
   next run tries again; when Ramp recovers, the hold goes on without anyone acting.
4. **Re-placing a hold is an alert, not an evidence event, for now.** The case's state does
   not change, so no transition fires. Whether a reconciliation re-hold should also be a
   chain event is open question Q9.
5. **A failing job never stops the worker.** Errors are recorded on the job's row and retried
   at the next interval; a dataset with no Ramp business (a bare test dataset) just records
   why.

## Acceptance

- [x] `app.job_runs` with a lease: two workers claiming the same due job, only one runs it.
- [x] A dataset's first poll imports every vendor as a baseline and opens no case.
- [x] A silent bank change (no webhook) is found by the next poll and opens one case, `detected_via = poll`.
- [x] The poll after that asks Ramp only for vendors updated since the watermark.
- [x] A hold lifted by hand in Ramp is put back by reconciliation, with one critical `vendor_not_held` alert even across repeated runs.
- [x] When Ramp refused the hold earlier (`hold_failed`), reconciliation places it once Ramp recovers and marks the hold confirmed.
- [x] `open` and `releasing` cases are left alone.
- [x] Jobs are due on the dataset clock: on a lab dataset, moving the clock past the interval makes them due, and not before.
- [x] The smoke run proves a silent change is caught and a lifted hold is put back, on the real services.

## Tests

| Acceptance item | Test |
|---|---|
| lease | `apps/core/tests/test_jobs.py::test_a_job_runs_once_at_a_time` |
| a release waits behind a re-hold | `test_jobs.py::test_a_release_waits_behind_a_reconciliation_lock` |
| baseline, silent change, watermark | `test_jobs.py::test_the_poller_baselines_then_finds_a_silent_change` |
| lifted hold, one alert | `test_jobs.py::test_reconciliation_puts_a_lifted_hold_back_once` |
| Ramp recovers | `test_jobs.py::test_reconciliation_holds_once_ramp_recovers` |
| skip open and releasing | `test_jobs.py::test_reconciliation_leaves_open_and_releasing_cases_alone` |
| dataset clock | `test_jobs.py::test_jobs_are_due_on_the_lab_clock` |
| real services | `scripts/dev/smoke.py` |

## Out of scope

The scheduled evidence-chain check (Nov 3), resolving alerts when their cause clears
(Nov 7), and showing job status in the dashboard.

## Open questions

- **Q9.** Should a reconciliation re-hold also be appended to the evidence chain (a new
  event type, so a contract change in its own PR)? Today it raises a critical alert only.

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
