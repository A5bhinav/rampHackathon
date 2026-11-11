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

## Acceptance

- [x] A sweep and a first import read each listed vendor's accounts once and the vendor itself never.
- [x] A sweep has more than one vendor in flight, and never more than five.
- [x] A vendor's error leaves the others synced, and the sweep still fails with that error.
- [ ] Everything else is unchanged: the core suite passes (309); the smoke run is still to run (its
      ports were in use by an end-to-end run).

## Tests (written first)

| Acceptance item | Test |
|---|---|
| 1-3 | `apps/core/tests/test_sweep.py::test_*` |
| 4 | `cd apps/core && uv run pytest`, `uv run python scripts/dev/stack.py --smoke` |

## Commands

```bash
cd apps/core && uv run pytest -q tests/test_sweep.py
```
