# 0100: A sweep that reads each vendor once, five at a time

**Owner:** Sami  **Slice:** money path (detection, jobs)  **Target day:** before the Nov 19 final  **Builds on:** 0093

## Goal

On Ramp's sandbox a bank change waiting for review is found only by the full sweep (spec 0093, D4),
and the first end-to-end run showed a sweep of the sandbox's 430-odd vendors taking 7 to 8 minutes:
each vendor is read, then its accounts, one vendor after another, about a second each. When this is
done, a sweep (and the first import, and the poll) reads each vendor's accounts only, five vendors
at a time, so the same sandbox sweeps in under a minute, held by the existing rate limiter (150
requests per 10 seconds, under Ramp's 200). Every vendor is still read: nothing is skipped.

## Contract touched

None.

## Decisions

- **D1. The list's vendor, not a second read.** The vendor list Ramp returns already carries each
  vendor's id, name, status and `updated_at`, which is all a sync uses of it. `sync_vendor` takes an
  optional `vendor`; given it, it doesn't read the vendor again. A webhook still passes only the id.
- **D2. Five at a time.** `sync_vendors` syncs a list of vendors with at most five in flight: at
  about half a second a read, five is about 11 requests a second, near the limiter's 15, and the
  core's ten database connections are shared with the API and the worker. A sync in a sweep holds
  one only briefly; the webhook door and reconciliation do hold one across a Ramp call, for a peak
  of about seven. The poll, the sweep and the first import use it; the results keep the list's order.
- **D3. One vendor's error doesn't stop the others.** Every vendor is synced; then, if any failed,
  the first error (in the list's order) is raised, so the job fails and runs again, as it did when
  the first error stopped the loop. A vendor after a failing one is no longer skipped.
- **D4. Scheduled jobs run beside the case steps, not in front of them.** In the first end-to-end
  run a decision waited about 7 minutes behind a sweep, because each worker tick ran the jobs before
  the commands. `run_forever` now runs two loops: one for webhooks, timers and commands, one for the
  jobs. That is the same concurrency as two workers, which the jobs' claims and the case locks are
  built for. An error in one loop is logged and doesn't stop either. Shutdown still waits for
  what is running. `tick` keeps its order and still runs the jobs, for `drain`, tests and the lab.

## Acceptance

- [x] A sweep and a first import read each listed vendor's accounts once and the vendor itself never.
- [x] A sweep has more than one vendor in flight, and never more than five.
- [x] A vendor's error leaves the others synced, and the sweep still fails with that error.
- [x] While a job is running, case steps still run; a failing job doesn't stop later jobs; with
      `run_jobs` off no job runs; shutdown waits for a running job; `tick` still runs the jobs.
- [x] Everything else is unchanged: the core suite and the smoke run pass.

## Tests (written first)

| Acceptance item | Test |
|---|---|
| 1-4 | `apps/core/tests/test_sweep.py::test_*` |
| 5 | `cd apps/core && uv run pytest`, `uv run python scripts/dev/stack.py --smoke` |

## Open questions and known limits (from the fresh-session review of the Ramp stack)

- **Q214:** an account with no last four (Ramp's document allows it; methods include CHECK, CARD and
  CRYPTO_WALLET_TRANSFER) makes the sandbox layer's account read fail, so that vendor's sync fails
  and a real bank change on it goes unseen, with only a failed job to show for it. Not seen on the
  sandbox. Options: skip payment methods that aren't bank accounts, and raise an alert for a bank
  account with no digits. A crypto wallet is also a redirect of payment, so skipping isn't free.
- **Q215:** a hold a person placed earlier is adopted: `BILL_PAY_7284` counts as placed, and the
  release later lifts it. The mock's hold behaves the same way. Should the worker remember that a
  hold was already on and leave it at release?
- **Q216:** Ramp's `pending_deletion` account is still live, but the layer reads it as not active.
- Limits: `DIALBACK_FULL_SWEEP_SECONDS` is server-wide, so a mock dataset on a sandbox server sweeps
  every minute too. A process killed mid-sweep leaves that job's lease until it expires (10
  minutes). Parallel syncs reach the mock in arrival order, so its seeded chaos no longer repeats
  exactly for a poll or sweep. No test runs a real sweep beside real case steps.

## Commands

```bash
cd apps/core && uv run pytest -q tests/test_sweep.py
```
