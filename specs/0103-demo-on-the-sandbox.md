# 0103: A demo dataset on Ramp's sandbox, with a real call

**Owner:** Sami  **Slice:** datasets and the demo ledger  **Target day:** before the Nov 19 final  **Builds on:** 0025, 0096, 0102

## Goal

A real bank change in Ramp's sandbox should end in a real phone call, all hosted. A demo dataset
already gets the demo's calling hours and shared accounts, but was barred from the sandbox, and the
demo ledger only knew the mock's vendors.

## Contract touched

None.

## Decisions

- **D1. The rule goes.** Migration 0027 drops `datasets_demo_on_the_mock` and `new-dataset` stops
  refusing. This reverses spec 0034's and 0096's rule, by choice: the sandbox holds no real money.
  The cost, accepted: anyone with a shared demo account can act on the sandbox. The
  one-current-sandbox index stays.
- **D2. The ledger.** `dialback-ledger demo load --real-vendor REF=RAMP_VENDOR_ID=NAME` loads only
  the named seed vendors, retelling each history under the real vendor's name. The history is
  synthetic, like the rest of the seed. The team phone still comes from `--phone`, at run time.
- **D3. Known limit.** A demo dataset on the sandbox counts as the current demo if it is the newest,
  so `dialback-core demo` commands and the email door point at it, and `demo reset` retires it.

## Acceptance

- [x] A demo dataset can be made on the sandbox, by the database and by `new-dataset`.
- [x] A real vendor takes a seed vendor's history, name and domain, with no `demo_refs`, and the rule
      picks the team phone.
- [x] A real vendor naming no seed vendor is refused.

## Tests

| Acceptance item | Test |
|---|---|
| 1 | `supabase/tests/016_dataset_ramp.sql`, `apps/core/tests/test_ramp_sandbox.py` |
| 2-3 | `packages/ledger/tests/test_demo.py::test_a_real_vendor_*` |

## Commands

```bash
cd packages/ledger && uv run pytest -q tests/test_demo.py
```
