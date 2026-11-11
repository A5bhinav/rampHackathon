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
  `--like acme` retells that one seed vendor for every real vendor, each under document ids of its
  own, since only Acme's history proves a number. REF is then just the real vendor's short name.
- **D3. Known limit.** A demo dataset on the sandbox counts as the current demo if it is the newest,
  so `dialback-core demo` commands and the email door point at it, and `demo reset` retires it.
- **D4. Email and Ramp changes in one demo.** `dialback-core email-door --dataset ID --ramp-vendor ID`
  turns the door on for named vendors of a current demo dataset. An email never joins a case with a
  Ramp change (R1), so one vendor takes emails and others take Ramp changes: the setup script gives
  Salesforce the door, and `demo load --no-request salesforce` skips its preloaded request, since
  the live email is the request.
- **D5. A fast reset.** `dialback-core copy-vendors --from OLD --to NEW` starts an empty, current
  dataset from what the old one knew: its vendors, their accounts with a Ramp id, and its poll
  watermark and sweep time. Nothing is read from Ramp, so a reset takes seconds and can't collide with
  the worker's own read (the first setup's import hit Ramp's rate limit that way). Ramp changes since
  the old dataset last looked are new to the copy too, so they open cases: the copy can over-report a
  change, never hide one (R1). An email door's requested accounts have no Ramp id and aren't copied.
  `setup_sandbox_demo.sh <old id>` uses it.

## Acceptance

- [x] A demo dataset can be made on the sandbox, by the database and by `new-dataset`.
- [x] A real vendor takes a seed vendor's history, name and domain, with no `demo_refs`, and the rule
      picks the team phone.
- [x] A real vendor naming no seed vendor is refused.
- [x] Three real vendors can retell one history, and the rule picks the team phone for each.
- [x] The email door turns on only for named, imported vendors of a current demo dataset, and a
      refusal turns nothing on.
- [x] A vendor can skip its preloaded request.
- [x] A reset copies the old dataset's vendors and Ramp accounts, not an email door's requested
      account; what the old dataset knew opens no case, and a new account still does.
- [x] It copies the poll watermark and sweep time, and refuses the same dataset, a dataset with
      vendors, a retired one and one on a different Ramp.

## Tests

| Acceptance item | Test |
|---|---|
| 1 | `supabase/tests/016_dataset_ramp.sql`, `apps/core/tests/test_ramp_sandbox.py` |
| 2-4, 6 | `packages/ledger/tests/test_demo.py::test_a_real_vendor_*`, `::test_real_vendors_*`, `::test_a_vendor_can_skip_its_request` |
| 5 | `apps/core/tests/test_ramp_sandbox.py::test_email_door_*` |
| 7 | `apps/core/tests/test_pending_bank_change.py::test_a_reset_copies_what_the_old_dataset_knew` |
| 8 | `apps/core/tests/test_ramp_sandbox.py::test_copy_vendors_*` |

## Commands

```bash
cd packages/ledger && uv run pytest -q tests/test_demo.py
```
