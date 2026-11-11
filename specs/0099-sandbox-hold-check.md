# 0099: Hold and release one vendor in Ramp's sandbox, the way the worker does

**Owner:** Sami  **Slice:** money path (tooling)  **Target day:** before the Nov 19 final  **Builds on:** 0097, 0098

## Goal

Spec 0097's layer places and lifts holds and reschedules bills through Ramp's API, but only a fake
has seen those calls; the probe (spec 0098) only reads. This check runs the worker's hold round trip
once on one sandbox vendor, through the real layer, and reports what Ramp did: whether the hold and
its release read back as the worker expects, how long each took to show, and whether every unpaid
bill got its payment date back. It leaves the vendor as it found it, and changes nothing in Dialback.

## Contract touched

None.

## Decisions

- **D1. Writes to one vendor, asked for twice.** `--vendor` is required, and nothing is sent without
  `--yes`. The client refuses any request but a GET, the login, a hold or release of that vendor, and
  a payment-date change to one of that vendor's unpaid bills as read at the start. The login asks for
  the five scopes this needs (`vendors:read vendors:write bills:read bills:write audit_logs:read`); if
  the configured scopes lack one, nothing is sent. Only `demo-api.ramp.com`, as the core checks.
  After logging in, if Ramp's answer lists the scopes it granted (its document's `scope` field) and
  one is missing, nothing is written (D7).
- **D2. A vendor already on hold is left alone**: that hold may be a person's decision, and the
  release would undo it. Remove it by hand first, or pick another vendor (`probe --list`).
- **D3. The worker's steps** (`worker.py`, `place_hold` and `release_hold`), through the layer:
  save each unpaid bill's payment date; place the hold (reason: "Dialback sandbox hold check");
  read back until the vendor shows the hold and every unpaid bill shows held; release; read back
  until the hold is gone; set each saved date again; read the bills once more.
- **D4. Read-backs wait up to 60 seconds**, one read a second, and report how long the hold took to
  show. Ramp answers a hold with 202 or 204, so it may apply it after answering; the worker reads
  three times, 0.2 and 0.4 seconds apart, and then retries the whole step, so the report says
  whether that first read-back would have seen it: yes on this check's first read; maybe on its
  second or third (the worker's last read starts before this check's third, by about 1.4 seconds);
  no after that. (The first version said "no" for any read after the first; corrected after the
  sandbox run below, where each read took about 3 seconds.)
- **D5. It cleans up.** Once the hold is placed, the release runs even if the read-back failed or
  timed out, and the dates are set again once the release is confirmed. If the release fails, it
  says how to remove the hold by hand and which dates to set; it doesn't set dates on a held vendor.
- **D6. The report**: Ramp's status for each write; seconds until the hold showed and until it was
  gone; each unpaid bill before, while held and after; whether every date came back. No secret or
  token. It answers Q202 for a release, Q203 for this vendor, and tests D2's payment-date mapping.
- **D7. Granted scopes**, added after the first sandbox run: the report shows the scopes Ramp's
  login answer lists, and the check stops before writing if one it needs is missing.
- **D8. A second hold**, added after the first sandbox run: once the hold shows, the check places it
  again, as the worker does when it retries after a read-back that came too early, and reports
  whether Ramp accepts a hold on a vendor already held (spec 0097, Q206).
- **D9. Each bill's Ramp status** (`status_summary`) before the hold, at the last read-back, and
  after, taken from the bill reads the check makes anyway (no extra request, so the timings stay
  honest). Added after the first run, whose bill turned out to be waiting for a transfer match.

## Found on the sandbox, 2026-11-10 (first run, one vendor: DocuSign, one unpaid bill)

- Hold and release each answered 204. The hold read back as the worker expects (the vendor showed
  it; the bill lost its date and showed held) after 10.4 seconds, on the third read; the release
  read back as gone after 12.4 seconds, on the fifth. Each read (vendor, audit log, bills) took
  about 3 seconds.
- Setting the date back failed: HTTP 403, "These scopes are not allowed for this client:
  bills:write". The login had asked for bills:write and Ramp issued a token without complaint, so
  the check held and released before it found out. D7 now stops that before the hold, if Ramp's
  login answer lists its scopes (not yet seen). The bill was left with no payment date.
- The release didn't give the date back by itself, as Ramp's help center says.
- The bill was waiting for a transfer match (paid by direct debit; `status` OPEN until matched), not
  a scheduled Ramp payment; matched by hand afterwards, it was paid. Salesforce's unpaid bill is the
  same kind, so the only scheduled bills in this sandbox are AWS's two, under a hold placed by hand.

## Acceptance

- [x] Without `--yes`, or without a needed scope, nothing reaches Ramp.
- [x] The client refuses a write to another vendor, another vendor's bill, or anything but a hold,
      release or payment date.
- [x] A vendor already on hold: no write is sent.
- [x] A full round trip: hold, read-back, release, read-back, dates set again, in that order; each
      unpaid bill ends with the date it started with; the report says so.
- [x] A hold that shows after a few reads is waited for and timed; one that never shows within 60
      seconds is reported as failed, and the release and dates still run.
- [x] A refused release, or one Ramp accepts but that still shows after 60 seconds: no dates are
      set, and the report says how to finish by hand.
- [x] A bill Ramp leaves scheduled fails the read-back, and the report says the vendor did show the
      hold; a date Ramp refuses to set again is reported.
- [x] A scope missing from Ramp's login answer stops the check before any write; an answer that
      lists no scopes is reported, and the check goes on (D7).
- [x] Once the hold shows, a second hold is sent and Ramp's answer reported; a refusal doesn't stop
      the release or the dates; no second hold when the first never showed (D8).
- [x] The first read-back is "yes" on read 1, "maybe" on reads 2 and 3, "no" after (D4).
- [x] Each unpaid bill's Ramp status is reported before, while held and after, with no extra bill
      read; an error answer to a bill read stays Ramp's error (D9).

## Tests (written first)

| Acceptance item | Test |
|---|---|
| 1-11 | `apps/core/tests/test_ramp_hold_check.py::test_*` |

## Out of scope

More than one vendor; a bank change; the case, its evidence or the database (the check never opens a
case); undoing a payment Ramp started before the hold (Ramp can't stop those).

## Commands

```bash
export RAMP_SANDBOX_CLIENT_ID='...'
read -rs RAMP_SANDBOX_CLIENT_SECRET && export RAMP_SANDBOX_CLIENT_SECRET
export RAMP_SANDBOX_SCOPES='vendors:read vendors:write bills:read bills:write audit_logs:read'
uv run --package dialback-core python -m dialback_core.ramp.holdcheck --vendor <ramp vendor id> --yes
cd apps/core && uv run pytest -q tests/test_ramp_hold_check.py
```
