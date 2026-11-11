# 0093: Open the case when a bank change is entered in Ramp, while it waits for review

**Owner:** Sami  **Slice:** money path (detection) and the sandbox layer  **Target day:** before the Nov 19 final  **Builds on:** 0097 (Q204, Q205)

## Goal

In Ramp, a bank change entered in the dashboard waits for an approver ("pending review"); until
then Ramp won't pay the new account. Today Dialback only sees the change once it is approved, so
the approver decides without Dialback's verdict. When this is done, a sandbox dataset opens the case
as soon as the change is entered: Dialback holds the vendor, calls and decides while the change
waits, and the approver approves or rejects in Ramp with Dialback's case in front of them. When
Ramp approves, the case carries on; no second case opens for the same change.

Mock datasets don't change: the mock never sends a pending account.

## Contract touched

None. The database gains one column (D2).

## Decisions

- **D1. The layer reads pending accounts.** `list_vendor_accounts` asks Ramp with
  `include_pending=true`. An account Ramp marks `pending_approval` maps to a `RampAccount` with
  `pending=True` and `is_active=False`. `RampAccount` gains `pending` (default false), so the mock
  adapter is unchanged.
- **D2. A new pending account is a bank change**, like a new active one: one change row, one case,
  the hold (R1 then covers the moment it is approved, if the approver doesn't wait). Its mirror row
  in `app.bank_accounts` is marked `ramp_pending` (new column, default false) and isn't current.
- **D3. An approval isn't a second change.** Ramp gives an approved account a new id and drops its
  `proposal_id`, and never sends a routing number (spec 0097, Q204), so the last four are all the
  pending and approved accounts share. A new active account is the approval of a pending one when
  the vendor has a mirror row that is `ramp_pending`, that a bank change recorded (Dialback saw it
  pending and opened a case), that Ramp no longer lists, and that has the same last four; each such
  row matches one account, and only in the sync that first finds it gone: that sync clears the
  row's `ramp_pending`, approved or not, so a later account with the same last four is a new change
  (a fraudster can choose the last four; found by review). Then the active account is mirrored as current and nothing else is
  written: the pending change's case carries on, and every case state but `released` keeps the
  vendor held. Anything else is a new change, as today: at worst a second verification, never a
  missed one. A pending account seen at a dataset's first import has no change, so its approval is
  a new change.
- **D4. Finding a pending change.** It moves neither the vendor's `updated_at` nor the audit log
  (spec 0097, Q204), so only the full sweep finds it. A sandbox deployment runs the sweep every
  minute: `DIALBACK_FULL_SWEEP_SECONDS=60`. (First written as "about 95 vendors"; the first
  end-to-end run found 432, and a sweep of them took 7 to 8 minutes. Spec 0100 brings it to under a
  minute.)
- **D5. Where the approver sees the verdict**: Dialback's case page. Ramp has no API to show it on
  the approval. The approver approves or rejects in Ramp; a person releases in Dialback (R5).
- **D6. Running it end to end locally**: `scripts/dev/stack.py --sandbox`, with the developer's
  `RAMP_SANDBOX_*` in the terminal, also makes a dataset on Ramp's sandbox and imports its vendors
  (what Dialback knows), and sweeps every minute. The stack's stand-ins (a fixed contact point; a
  vendor on the phone who confirms with the new account's last four) answer its calls, so the run
  checks Ramp's side: detection, the hold, the release, the dates, the approval. Without the
  credentials it says so and stops; without `--sandbox` nothing changes (the smoke run still passes).

## Found on the sandbox, 2026-11-10 (first end-to-end run, `stack.py --sandbox`; one vendor, one run)

- A bank change entered in Ramp's dashboard and left pending opened one change and one case. The
  hold, the number and the stand-in call each succeeded on the first try.
- The decision was `needs_review` (`NR07_digits_ambiguous`): the test account number ended in the
  same four digits as the vendor's old account, so the digits proved nothing. The "verified" path
  was not exercised.
- A person cleared the review and two others released: the worker lifted the hold on its first try,
  Ramp showed the vendor Active, and Dialback recorded the bill's date as restored.
- Approved in Ramp afterwards: the account arrived active under a new id and was mirrored as
  current, with still one change and one case (D3). Both the old and the new account are now
  current in the mirror, as both are active in Ramp.
- The decision waited about 7 minutes (03:14 to 03:21 UTC) behind a sweep: the worker runs the
  scheduled jobs before each round of case steps.
- A needs-review case with no known author takes three approvers (the reviewer can't release, and
  the release needs two). The stack seeds two; a third was added by hand to that run's database.
- Not shown: a real call, a payment, a rejection in Ramp.

## Acceptance

- [x] The layer asks Ramp for pending accounts and marks a `pending_approval` one pending, not active.
- [x] A new pending account opens one change and one case, and is mirrored as pending, not current.
- [x] Approved (its pending id gone, an active account with the same last four): no second change
      or case event, and the active account is mirrored as current.
- [x] A new active account is a change when its last four differ, when the pending account is
      still listed, or when no change recorded the pending one.
- [x] A pending account already known is not a change again.
- [x] Mock datasets are unchanged: the core suite passes.

## Tests (written first)

| Acceptance item | Test |
|---|---|
| 1 | `apps/core/tests/test_ramp_sandbox_shapes.py::test_pending_accounts_*` |
| 2-5 | `apps/core/tests/test_pending_bank_change.py::test_*` |
| 6 | `cd apps/core && uv run pytest` |

## Out of scope, and open questions

- **Q211:** a change rejected in Ramp: its pending account disappears and nothing replaces it. The
  case stays open and the vendor held until a person releases it (fail closed). Closing it by
  itself needs a new case event, a change to `app.apply_case_event`.
- **Q212:** the evidence log doesn't record that Ramp approved the change (a log line only).
- **Q213:** a pending change edited during review: if Ramp keeps the pending account's id and its
  last four, Dialback can't see the edit (Ramp sends nothing else about the account). Not seen yet.
- The hold reason could carry a link to the case, so the approver finds it from Ramp. Not built.

## Commands

```bash
cd apps/core && uv run pytest -q tests/test_pending_bank_change.py tests/test_ramp_sandbox_shapes.py
cd apps/core && uv run pytest -q
```
