# 0097: A translation layer for Ramp's sandbox, used only by sandbox datasets

**Owner:** Sami  **Slice:** money path  **Target day:** before the Nov 19 final  **Builds on:** 0096

## Goal

Spec 0096 routes a sandbox dataset to Ramp's sandbox, but the adapter still reads the mock's field
names, so syncing a sandbox dataset fails. When this is done, a separate adapter used only for
sandbox datasets talks to Ramp in Ramp's real shapes and hands the core exactly the objects it gets
from the mock today. The mock, the demo, the attack lab and the pipeline (detection, the worker, the
jobs) don't change, so sandbox support can be removed by deleting this adapter and 0096's routing.

Wherever Ramp's behavior isn't documented, the layer chooses the reading that fails closed (R7):
a wrong guess sends a case to a person, never a payment to a new account (R1).

## Contract touched

None.

## Decisions

- **D1. Where.** `ramp/sandbox.py`: `RampSandboxAdapter`, built on the shared HTTP adapter (rate
  limit, retries, pagination, login). The connector uses it for sandbox datasets only. The shared
  adapter gains one opt-in: a call may accept an empty 202 or 204, as Ramp's hold and release answer.
- **D2. Ramp to the core**, field by field:

  | Core object | From Ramp |
  |---|---|
  | vendor | `id`, `name`; `status` is `ACTIVE` or `INACTIVE` from `is_active`; `updated_at` |
  | bank account | `id`; `last4` from `account_number_last_four`, which must be four digits or the call fails (never invented); `is_active` when `status` is `active`; `routing_number` empty, because Ramp never returns it; `bank_name`, `account_type`, `created_at` empty |
  | bill | `id`; `vendor_id` from `vendor.id`; `payment_date` the date part of `payment.payment_date`; `paid` only when `status` is `PAID` (not on `paid_at` alone: the read-back skips paid bills, so a bill must never look paid early); `is_on_hold` (D3) |
  | audit event | `id`; `actor_id`; `occurred_at` from `event_time`; `target_id` from `primary_reference.id`; `payload` from `event_details`; `event_type` (D4) |
  | hold | Ramp answers 202 or 204 with no body, so the hold has no Ramp id (`""`) |

  Paths: `GET /vendors` (`from_updated_at`), `GET /vendors/{id}`, `GET /vendors/{id}/accounts`,
  `GET /bills` (`vendor_id`), `POST /vendors/{id}/hold` (`reason`) and `/release`,
  `PATCH /bills/{id}` (`payment_date`), `GET /audit-logs/events` (`object_id` with
  `resource_name=Vendor / Merchant`, which Ramp requires together; `event_types` joined with
  commas). Pages of 100, Ramp's largest, followed through `page.next`. A refusal carries Ramp's
  `error_v2` code, id and message; the mock's errors are unchanged.

  Found on the sandbox, 2026-11-10: the probe's first run (spec 0098) got a 422 for `object_id`
  without `resource_name`. Ramp's document says so (on `object_id`); the first version missed it
  because the fake didn't enforce it. The fake now refuses the same way.
- **D3. The hold read-back.** The worker confirms a hold only when the vendor shows one and every
  unpaid bill shows held. Ramp's vendor has no hold field, so `hold_id` is the newest of Ramp's
  `Vendor placed on hold` and `Vendor hold released` audit events for the vendor, when it is a
  placement. A bill shows held when `status_summary` is `ON_HOLD`, or when it is unpaid and has no
  payment date, since Ramp says a vendor hold takes its bills out of scheduled payment.
- **D4. Event names.** The core asks for `VENDOR_BANK_ACCOUNT_UPDATED`; the layer asks Ramp for
  `Bill pay bank account updated` and answers under the core's name. A core name with no Ramp
  equivalent is a `RampError`. Ramp doesn't document `event_details` for this event, so the change's
  author stays unknown, and the release needs two approvers (ADR-10).
- **D5. The hold from the vendor's own `status`** (added after the sandbox runs of spec 0099 showed
  the audit log about 7 seconds behind, both ways, and the vendor's `status` right at once; Q209).
  Ramp's vendor answer carries an undocumented `status`; its help center names the vendor statuses
  Active, Inactive, On hold, Pending and Unmanaged. `ON_HOLD` is a hold (`hold_id` is
  `status:ON_HOLD`, since Ramp gives a hold no id); `ACTIVE`, `INACTIVE`, `PENDING` and `UNMANAGED`
  are not. Anything else (no field, null, a value not in that list) falls back to D3's audit-log
  reading, so a status Ramp adds later can't be misread. And a hold Ramp refuses with
  `BILL_PAY_7284` ("Vendor is already on hold") counts as placed: it is on, by Ramp's word, so the
  worker's retry goes on to read it back. Any other refusal still raises.
- **D6. Vendors whose bank accounts changed, though `updated_at` didn't** (Q210). With a `since`,
  `list_vendors_updated_since` also reads Ramp's audit log across the business from `since` for the
  bank-account event names that can add or change a vendor's account (`Bank account updated`, seen
  on approval; `Bill pay bank account updated`; `Bill pay accepted sync for bank account from vendor
  network`, Ramp's automatic replacements), and adds each vendor one is filed under (`Vendor /
  Merchant`) that the `updated_at` list missed, read by id. Listing a vendor too many is harmless:
  detection only acts on accounts it hasn't seen. The core's poll already looks back two minutes
  (`WATERMARK_OVERLAP`), which covers the audit log's seconds of lag. A full read (no `since`) is
  unchanged. A change still pending writes no event (Q204), so it isn't found here; that is Q205's.

## Acceptance

- [x] Each of the eight calls sends Ramp's documented path, method, parameters and body.
- [x] Each answer becomes the core's object exactly as in D2, D3 and D4, from Ramp-shaped data.
- [x] An account without four last digits, and an event name with no Ramp equivalent, raise a
      `RampError` that isn't retried.
- [x] A sandbox dataset reaches the sandbox through this adapter; a mock dataset through the
      unchanged mock adapter.
- [x] Importing a sandbox dataset's vendors stores them and their accounts, with an empty routing
      number and Ramp's last four.
- [x] Everything else is unchanged: the core and SQL suites and the smoke run pass.
- [x] D5: a vendor whose `status` is `ON_HOLD` is held, and one whose `status` is a known other value
      isn't, both without reading the audit log; a missing or unknown `status` reads the audit log as
      before; a hold refused with `BILL_PAY_7284` returns as placed, any other refusal raises.
- [x] D6: with a `since`, a vendor with one of those events since then is listed though its
      `updated_at` is older, once, and an event filed under anything but a vendor adds nothing;
      without a `since`, the audit log isn't read; and the core's poll, given a watermark, finds a
      new account on a vendor whose `updated_at` didn't move.

## Tests (written first)

| Acceptance item | Test |
|---|---|
| 1-3 | `apps/core/tests/test_ramp_sandbox_shapes.py::test_*` |
| 4 | `apps/core/tests/test_ramp_sandbox_shapes.py::test_routing_*` |
| 5 | `apps/core/tests/test_ramp_sandbox_shapes.py::test_import_*` |
| 6 | `cd apps/core && uv run pytest`, `scripts/db/test.sh`, `uv run python scripts/dev/stack.py --smoke` |

## Out of scope

- Changing the mock to Ramp's shapes (option B, not chosen), and the webhook setup.
- Spotting a bank change while it waits for approval in Ramp (`include_pending`).
- Checking these fixtures against Ramp's document with spec 0078's checker, once both are merged.

## Open questions (a sandbox run answers each; a wrong answer fails closed)

- **Q202, answered for placement:** does Ramp file a vendor's hold and release events under the
  vendor's id (`object_id`)? Yes for a placement: the probe (spec 0098), 2026-11-10, after a hold
  placed by hand on a sandbox vendor with two unpaid bills, found `Vendor placed on hold` under the
  vendor's id with `resource_name=Vendor / Merchant`, and the layer read the hold as on. And for a
  release: the hold check (spec 0099), 2026-11-10, held and released a second vendor through this
  layer; both answered 204, and the release read back as off. (One vendor each: confirmed, not general.)
- **Q203, answered:** do a held vendor's bills show `ON_HOLD`, or lose their payment date? Both, in
  the same run: each unpaid bill showed `status_summary` `ON_HOLD` and no payment date, so by the
  layer's reading the worker's read-back would pass. (One vendor, one hold: confirmed, not general.)
  The hold check's vendor did the same, and its release gave the date back only when the worker's
  step sets it again (Ramp's help center: removing a hold reschedules nothing).
- **Q204:** is `Bill pay bank account updated` the event for a vendor's new bank account, and does
  its `event_details` name the new account? Until then, the author is unknown (two approvers).
  First try, 2026-11-10: a bank change made by hand in the sandbox's dashboard (its ownership check
  failed; saved without verifying) showed no new account and no event under the vendor's id. Ramp
  showed it as "pending review". The probe now lists pending accounts and recent events from the
  whole business (spec 0098, D7) to find where it went. In Ramp's dashboard (read 2026-11-10):
  the change is a vendor-edit proposal (`d85f2735-9946-431f-a9ac-f6846d03d0b4`, "Requested edits
  to vendor details: Added ACH bank account", the requester's own entry, 2:40 PM) adding an
  account ending 4321 (routing 021000021). Ramp's ownership check gave "No ownership result" (no
  account holder name was available), and its verification history reads "Manually verified:
  Confirmed with vendor directly", by the requester: the person's word that Dialback's call
  replaces. Approval is "in progress", by "Any Admin", and the requester, an admin, is offered
  Approve. Through the API (the probe, the same day): `include_pending=true` lists it as account
  `5d950fa7-36a7-404a-9227-98e19acaa0c4`, last four 4321, `pending_approval`, `proposal_id`
  `3653632a-f167-41fe-bdb6-1dffe8f1a97c`, which is the id of the dashboard's approval group for the
  change, not of its proposal page (`d85f2735-...`). The vendor's `updated_at` didn't move, and no
  audit event that mentions the vendor, the pending account or that id appeared after the hold
  (22:25 UTC) among the events read (the read stops at 1,000; whether it stopped wasn't printed).
  So while pending, the change shows only as a pending account. Whether approving it writes an
  event, and what the account's new id is, is still open: approve it and run the probe again.
  **Answered on approval (same day, approved in the dashboard):** the account came back `active`
  under a new id (`4879cce7-d182-4d04-a319-b9506d0feec9`), its pending id gone and `proposal_id`
  null, so the pending and the approved account share only the last four. The old account (6789)
  stayed active: the edit added an account. Ramp wrote `Bank account updated` (with `Approval chain
  updated`) under the vendor, `event_details` keys `references` only; no `Bill pay bank account
  updated`. So D4 asks for the wrong name for a change made in the dashboard, and the author can't
  be read from it as built (no `new_account_id` key): the release needs two approvers (fail closed).
  What `references` holds isn't printed yet. And the vendor's `updated_at` moved neither on the
  request nor on the approval: see Q210.
- **Q205:** a bank change can wait for approval in Ramp, and until then the proposed account shows
  only with `include_pending`, under an id Ramp replaces when it approves the change (Ramp's
  document). The layer reads without `include_pending`, so it sees the change only once approved,
  and a payment Ramp schedules between that approval and the core's next sync isn't held (R1).
  Should a sandbox dataset open the case while the change is pending, and how does the case follow
  the account across the new id (the last four and `proposal_id` are the candidates)?
  **Decided (Sami, 2026-11-10): yes, so the approver sees Dialback's verdict.** Built as spec 0093:
  the layer reads pending accounts, detection opens the case on one, and follows it by the last four,
  since Ramp drops `proposal_id` on approval (Q204).
- **Q206:** Ramp showed a hold about 7 to 10 seconds after answering 204 (the hold check, one run;
  each read took about 3 seconds). The worker's read-back reads three times, 0.2 and 0.4 seconds
  apart, so it may miss a hold that is on; it then retries the step, which places the hold again.
  The worker assumes a hold on a vendor already held "changes nothing" (true of the mock). If Ramp
  refuses it, every slow read-back ends with the case sent to a person after five tries (fail
  closed, but no case would get through). The hold check now sends that second hold (spec 0099, D8);
  its next run answers this. If Ramp refuses, the retry should read before placing again.
  **Answered, 2026-11-10 (second hold-check run): Ramp refuses it**, HTTP 400, `BILL_PAY_7284`
  "Vendor is already on hold". The worker retries any `RampError` (`_run`), so after a read-back
  that came too early every retry fails the same way and, after five, the case goes to a person
  with the vendor held. Two runs: the hold showed after 9.9 and 10.4 seconds, the release after 8.6,
  each read taking 2 to 3 seconds, so the worker's three quick reads may miss either (likely for a
  release, whose read-back failure puts the hold back: `release_hold` retries, and the release
  would rarely complete). The reconcile job can hit the same 400 if it runs within those seconds of
  a hold, and would raise a false "could not be put back" alert. See Q209.
- **Q209:** what should the worker do about Ramp's delay? Candidates: the layer treats
  `BILL_PAY_7284` as the hold being on (it is, by Ramp's own word), so a retry goes on to read back;
  and the worker's read-backs wait as long as Ramp takes (tens of seconds, not under one), or defer
  the step instead of failing it. Which part lags isn't known: the layer reads the hold from the
  audit log; the vendor's own (undocumented) `status` field, or the bills' `ON_HOLD`, may show it
  sooner. Measuring each read-back's parts would tell. The worker is the money path's; this needs
  its own spec.
  **Measured, 2026-11-10 (third hold-check run, spec 0099 D10): the audit log is what lags.** On the
  first read after the hold (+0.0 s), the vendor's own `status` was already `ON_HOLD`, its
  `updated_at` had moved, and the bill showed `ON_HOLD` with no date; the audit log first showed the
  hold on the third read (+6.6 s). After the release, the vendor's `status` was back and the bill
  back to `APPROVAL_PENDING` on the first read; the audit log still showed the hold until the third
  (+7.0 s). (One run each way: the pattern is plain, the seconds are coarse.) So the layer could read
  the hold from the vendor's `status` (undocumented, so falling back to the audit log if it's
  missing), and treat `BILL_PAY_7284` as the hold being on; then the worker's quick read-backs
  would pass at once, with no change to the worker. Built as D5: the worker's own read-back
  (`Worker._read_back_hold`) now passes on its first read against a fake whose audit log is ten
  reads behind, and misses without the status (`test_the_workers_own_read_back_*`). **Seen on the
  sandbox, 2026-11-10 (fourth hold-check run):** the hold and the release each read back on the
  first read (2.4 and 2.9 seconds, the length of that read), while the audit log showed neither yet;
  the second hold's 400 read as on; the date came back. One vendor, one run: confirmed, not general.
- **Q210, the biggest gap found:** on Ramp, a bank change doesn't move the vendor's `updated_at`
  (seen: neither the request nor the approval did). The core's 60-second poll asks for vendors
  updated since its last run (`list_vendors_updated_since`), so it never sees a bank change; only
  the full sweep does, once a day by default. A payment to the new account could go out before
  Dialback notices (R1). Candidates, sandbox side: the layer's updated-since list also returns
  vendors with a `Bank account updated` event since then (the audit log, seconds behind, is fine
  for a one-minute poll); and, for a change still pending (no event, no `updated_at`), a sweep
  every minute (the sandbox has about 430 vendors, one accounts read each; spec 0100).
  Built for approved changes as D6: the core's poll now finds a new account on a vendor whose
  `updated_at` didn't move (`test_the_poll_finds_a_bank_change_*`). Not yet seen on the sandbox; a
  change still pending is Q205's.
- **Q207:** Ramp issued a token for a scope the app isn't allowed (`bills:write`) and refused only
  the call (HTTP 403). The core's login doesn't compare what Ramp granted with what it needs, so a
  sandbox dataset missing `bills:write` would hold and call normally, then fail every release when
  it sets the dates back: the hold is put back and the case goes to a person (fail closed, but
  found late). Should the core check the scopes Ramp's login answer lists at start-up? Not yet
  known whether Ramp lists them; the hold check's next run shows it. Second run: Ramp's answer does
  list them (all five, once `bills:write` was allowed). Whether it leaves out a scope the app isn't
  allowed is still not seen; if it only echoes the request, a start-up check wouldn't catch this.
- **Q208:** does a vendor hold restart its bills' approvals, and can the worker set a payment date
  on a bill waiting for approval? Ramp's help center says approvals for payments affected by a hold
  "start again from the first step". If Ramp refuses the date, `release_hold` fails, puts the hold
  back and retries, and every release ends with the case sent to a person (fail closed, but no
  release would complete). Not seen yet. After the hold check, DocuSign's only unpaid bill showed
  "0 of 1 approvals" and was waiting for a transfer match; matched by hand, it was paid. Its state
  before the check wasn't recorded, so it may have been waiting for that match all along (a first
  version of this question said the hold restarted its approval; that wasn't shown). A bill like
  that (paid by direct debit; `status` OPEN until matched; `status_summary`
  WAITING_FOR_TRANSFER_MATCH) counts as unpaid in the layer, so the worker would save its date and
  set it again after a release; what Ramp does with that date is unknown. Partly answered by the
  second hold-check run: an ACH bill waiting for approval (`APPROVAL_PENDING`) lost its date to the
  hold, showed `ON_HOLD`, and after the release Ramp accepted its date again (PATCH 200) and it was
  `APPROVAL_PENDING` as before. Whether a hold restarts the approval of a bill already approved is
  still not seen.

## Commands

```bash
cd apps/core && uv run pytest -q
scripts/db/test.sh
uv run python scripts/dev/stack.py --smoke
```
