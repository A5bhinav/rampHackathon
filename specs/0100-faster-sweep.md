# 0100: A sweep that reads each vendor once, five at a time

**Owner:** Sami  **Slice:** money path (detection, jobs)  **Target day:** before the Nov 19 final  **Builds on:** 0093

## Goal

On Ramp's sandbox a bank change waiting for review is found only by the full sweep (spec 0093, D4),
and the first end-to-end run showed a sweep of the sandbox's 430-odd vendors taking 7 to 8 minutes:
each vendor is read, then its accounts, one vendor after another, about a second each. When this is
done, a sweep (and the first import, and the poll) reads each vendor's accounts only, five vendors
at a time, so the same sandbox sweeps in under a minute, held to Ramp's limit (100 requests per 10
seconds) by the existing rate limiter. Every vendor is still read: nothing is skipped.

## Contract touched

None.

## Decisions

- **D1. The list's vendor, not a second read.** The vendor list Ramp returns already carries each
  vendor's id, name, status and `updated_at`, which is all a sync uses of it. `sync_vendor` takes an
  optional `vendor`; given it, it doesn't read the vendor again. A webhook still passes only the id.
- **D2. Five at a time.** `sync_vendors` syncs a list of vendors with at most five in flight: at
  about half a second a read, five already exceed Ramp's ten requests a second, so more would only
  wait on the limiter, and the core's ten database connections are shared with the API and the
  worker (each sync holds one only briefly: no transaction waits on Ramp). The poll, the sweep and
  the first import use it; the results keep the list's order.
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
- [ ] Everything else is unchanged: the core suite and the smoke run pass.

## Tests (written first)

| Acceptance item | Test |
|---|---|
| 1-4 | `apps/core/tests/test_sweep.py::test_*` |
| 5 | `cd apps/core && uv run pytest`, `uv run python scripts/dev/stack.py --smoke` |

## Commands

```bash
cd apps/core && uv run pytest -q tests/test_sweep.py
```
