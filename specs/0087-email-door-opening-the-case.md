# 0087: Email door, opening the case (PR 4 of the email door)

**Owner:** Sami  **Slice:** money path  **Target day:** Nov 10-11

## Goal

A flagged bank-change email opens a Dialback case on its own, through the existing state machine, without touching Ramp (design: `docs/superpowers/specs/2026-11-10-email-door-design.md`, sections 6 and 7, and "Flagging: the rule plus a model"). The email is filed in the Trust Ledger as the vendor's change request first, so every number first seen in it is excluded from selection (R2); then, in a second transaction, the requested account and the change are written and `apply_case_event('change_detected')` opens or joins the case. Errors after the ledger write fail closed: no case, the email is listed `failed` (R7). A full account number exists only in memory while it is fingerprinted (ADR-8). Task 8 builds this; Task 9 adds `demo prepare --email-door`.

## Contract touched

None. `app.inbound_emails` (spec 0084) already has every column, outcome and red flag used here (`ramp_case_open` was added to spec 0084's contract and to the outcome check by `0023_email_ramp_mix.sql`).

## Acceptance

- [ ] Task 8, headers first (ADR-8): before the door switch and before flagging, the accounts of the text part, the HTML part and the subject are found (each read up to 1,000,000 characters). If one of 5 or more digits, as written or without its leading zeros, is among the digits of the stored or fetched `Message-ID`, the thread ID or the sender domain, the email ends `failed` (see the failure path) and the model is never asked.
- [ ] Task 8, bank details: the text part and the HTML part's text are each read on their own (each up to 50,000 characters). Each account is paired with a routing number: the nearest one on the same line, else the nearest within 200 characters on either side, else the first that is not the vendor's routing number on file, else the first. "Nearest" prefers one in the same clause (no `;`, and no `.` `!` `?` right after a digit and before whitespace, between them), then the fewest characters between, so both "Routing 1, Account A; Routing 2, Account B" and "Account A, Routing 1; Account B, Routing 2" pair A with 1 and B with 2. A period after a word, as in the abbreviation "Acct.", ends no clause, so "Routing 1 Acct. A, Wire Routing 2 Acct. B" pairs A with 1 too (final review M1).
- [ ] Task 8, flagging: `flag_email(subject, text, html_text, worker.classifier)` runs in a thread. Not flagged: the email is still matched and its bank details read; when a door vendor matched, a routing number and an account were found, and the account on file (routing number and last four) is not among the found pairs (or the vendor has none), the email counts as flagged (`on_file_mismatch`, a silent change); otherwise it ends `not_flagged`. An invoice listing the account on file next to another account is not a change. A `ClassifierFailed` propagates, so the inbox retries the email (5 tries, then `failed`). A dataset that is not the current demo, or has no door vendors, ends `door_off` without flagging.
- [ ] Task 8, in order: no vendor `no_vendor`; several `several_vendors`; a vendor outside `email_door_vendors` `not_demo_vendor`; no routing number `no_routing_number`; no account `no_account_number`; no account whose pair (routing number, last four) differs from the account on file's `same_as_on_file`. Otherwise the requested account is the longest such account (the first of equal length), with its paired routing number, so "from our old account ... to our new account ..." asks for the new one, and a new bank with the same last four is a change. Then the same email again (its `Message-ID` already made a change for this account) or the same account fingerprint among the email changes of the vendor's unfinished case is `repeat_request`, with nothing written; a `Message-ID` that already made a change for another account or vendor is `failed`. Then, when the vendor's unfinished case has any change that did not come by email (the opening change included, read from its `change_detected` evidence as `app.case_email_only`'s rule D does), the email is `ramp_case_open` with that case as its `case_id`, and nothing is written: no ledger document, no requested account, no change, no case event (R1, final review finding C1: a case verifies its current change, so an email joining a case Ramp changed would be verified while Ramp pays another account).
- [ ] Task 8, ledger first: one `email` document (`ledger_document`: id `uuid5(NAMESPACE_URL, "email:" + Message-ID)`, custody channel `email`, our receive time on the dataset's clock, the masked sender, the thread ID, the masked subject stored in `app.inbound_emails`, and one span per line of `ledger_text(text)`, plus `ledger_text(html_text)` only when the HTML part differs from the text part once whitespace is collapsed, each masking every account found in the text part, the HTML part and the subject, each read up to 1,000,000 characters (an account labeled past the 50,000 characters the ledger reads can repeat a phone inside them); nothing else from the body), ingested with the ledger's stub reader, passed by the door itself and never the one `DIALBACK_READER` names (final review I4: the document is rebuilt phone lines, and `llm` would be a synchronous model call), so the stored `extractor_version` is `stub`, and marked as the vendor's change request, committed before the case transaction.
- [ ] Task 8, the case: in one transaction holding the email's row and the vendor's row (the lock `apply_case_event` takes), the repeat check and the `ramp_case_open` check run again; then `app.bank_accounts` gets the requested account (routing, last four, `HMAC(key, routing|account)`, key version, `ramp_account_id` null, `is_current` false; reused on a repeat), `app.bank_change_events` the change (`detected_via 'email'`, `source_event_id 'email:<Message-ID>'`, `change_thread_id`, the vendor's current account read in this transaction as `old_account_id`, `changed_by` null), and `apply_event(change_detected)` opens a case (`case_opened`) or joins the unfinished one, whose every change came by email (`joined_case`, version + 1). The row stores the vendor, case, change, routing number, last four and red flags (`lookalike_domain`, `sender_off_domain`, `reply_to_differs`, `sender_unauthenticated` when neither DMARC nor DKIM passed, `unknown_phone` when a phone in the email is not among the vendor's ledger sightings outside change requests). Ramp is never written.
- [ ] Task 8, end to end on the real ledger: a judge's email naming Cobalt opens a case via email, the email's phone is a candidate excluded with `first_seen_in_change_context`, the call goes to the provable team number, and Ramp still shows Cobalt's original account. Instructions in the email change nothing (R9).
- [ ] Task 8, never a Ramp case (R1): an email asking for another account while the vendor's unfinished case was opened by a Ramp change, or an email case that a Ramp change has joined, is `ramp_case_open` and leaves the case exactly as it was (state, version, current change, decision); when the vendor then confirms the email's account on the call, the case is not verified. The final review's probe is kept as a regression test.
- [ ] Failure path: any exception from the ledger write on records `failed` with the exception's type name only, opens no case and writes no change; a full account number is in no table and no log line (ADR-8). An email whose `Message-ID`, thread ID or sender domain holds (among its digits) an account of 5 or more digits found in the email, as written or without its leading zeros, ends `failed` with a fixed error before anything else runs; its row's `message_id`, `thread_id` and `sender_domain` become `scrubbed`, and `data.message_id` is dropped from its stored Resend notices. The model's quote is stored nowhere.
- [ ] Task 9: `demo prepare --email-door REF` (repeatable) sets the dataset's `email_door_vendors` after the ledger load, refuses before the reset a ref that is also a `--request` or is unknown, and prints the inbox address or "DIALBACK_INBOX_ADDRESS is unset".

## Tests (written first)

| Acceptance item | Test |
|---|---|
| 1 to 9 | `apps/core/tests/test_email_door.py` (item 8: `test_an_email_never_joins_a_case_opened_by_a_ramp_change`, `test_an_email_cannot_change_what_a_ramp_case_verifies`, `test_a_later_email_never_joins_an_email_case_that_a_ramp_change_joined`, `test_the_case_transaction_checks_for_a_ramp_change_again_under_the_vendor_lock`) |
| 10 | `apps/core/tests/test_demo.py -k email_door` (Task 9) |

## Out of scope

The reply (PR 5), the mail list and case page additions (PR 6), dismissing a blocked email case (PR 7), and the verified ending (design, open question 2).

## Open questions

- A second change on an open case is matched by the ledger against the first request only (`store.py::_inputs` reads the request recorded before the case opened), so a phone first seen in a later email is not a candidate at all. Safe (never picked), but the lineage panel does not list it.
- An orphaned change request: when the ledger write commits and the case transaction then fails, the email is `failed` with no case, but its document stays the vendor's latest change request, and a later Ramp-detected case for that vendor would adopt it as its change context.
- `on_file_mismatch` (a silent change) has no stored reason yet: the row shows only `flagged`. Recording why an email was flagged needs a contract change in its own PR.
- The model call (`DIALBACK_EMAIL_CLASSIFIER=model`, one attempt of up to 30 s since the final review's I1 fix) runs while the inbox's claim transaction holds a pooled connection, so slow model calls can tie up connections. The worker's email step runs after its commands and within a time budget (spec 0085).
- An account labeled more than 1,000,000 characters into a part, whose digits also appear as a phone in the first 50,000, would still reach the ledger as that phone. Larger reach costs time on every email.

## Commands

```bash
cd apps/core && uv run pytest tests/test_email_door.py -q
cd apps/core && uv run pytest -q
uv run --no-project --with ruff ruff check apps packages scripts corpus
python3 scripts/ci/boundaries.py
uv run python scripts/dev/stack.py --smoke
```
