# 0087: Email door, opening the case (PR 4 of the email door)

**Owner:** Sami  **Slice:** money path  **Target day:** Nov 10-11

## Goal

A flagged bank-change email opens a Dialback case on its own, through the existing state machine, without touching Ramp (design: `docs/superpowers/specs/2026-11-10-email-door-design.md`, sections 6 and 7, and "Flagging: the rule plus a model"). The email is filed in the Trust Ledger as the vendor's change request first, so every number first seen in it is excluded from selection (R2); then, in a second transaction, the requested account and the change are written and `apply_case_event('change_detected')` opens or joins the case. Errors after the ledger write fail closed: no case, the email is listed `failed` (R7). A full account number exists only in memory while it is fingerprinted (ADR-8). Task 8 builds this; Task 9 adds `demo prepare --email-door`.

## Contract touched

None. `app.inbound_emails` (spec 0084) already has every column, outcome and red flag used here.

## Acceptance

- [ ] Task 8, flagging: `flag_email(subject, text, html_text, worker.classifier)` runs in a thread. Not flagged: the email is still matched and its bank details extracted; when a door vendor matched, a routing number and an account were found, and they are not the vendor's current account (routing number and last four, or the vendor has none), the email counts as flagged (`on_file_mismatch`, a silent change); otherwise it ends `not_flagged`. A `ClassifierFailed` propagates, so the inbox retries the email (5 tries, then `failed`). A dataset that is not the current demo, or has no door vendors, ends `door_off` without flagging.
- [ ] Task 8, in order: no vendor `no_vendor`; several `several_vendors`; a vendor outside `email_door_vendors` `not_demo_vendor`; no routing number `no_routing_number`; no account `no_account_number` (extraction reads the text part and the HTML part's text; the first routing number and the longest account are used); the vendor's current routing number and last four `same_as_on_file`; the same email again (its `Message-ID` already made a change) or the same account fingerprint among the email changes of the vendor's unfinished case `repeat_request`, with nothing written.
- [ ] Task 8, ledger first: one `email` document (`ledger_document`: id `uuid5(NAMESPACE_URL, "email:" + Message-ID)`, custody channel `email`, our receive time on the dataset's clock, the masked sender, the thread ID, the masked subject stored in `app.inbound_emails`, and one span per line of `ledger_text(text) + "\n" + ledger_text(html_text)` with every found account masked; nothing else from the body), ingested with `email-door-v1` and marked as the vendor's change request, committed before the case transaction.
- [ ] Task 8, the case: `app.bank_accounts` gets the requested account (routing, last four, `HMAC(key, routing|account)`, key version, `ramp_account_id` null, `is_current` false; reused on a repeat), `app.bank_change_events` the change (`detected_via 'email'`, `source_event_id 'email:<Message-ID>'`, `change_thread_id`, the current account as `old_account_id`, `changed_by` null), and `apply_event(change_detected)` opens a case (`case_opened`) or joins the unfinished one (`joined_case`, version + 1). The row stores the vendor, case, change, routing number, last four and red flags (`lookalike_domain`, `sender_off_domain`, `reply_to_differs`, `sender_unauthenticated` when neither DMARC nor DKIM passed, `unknown_phone` when a phone in the email is not among the vendor's ledger sightings outside change requests). Ramp is never written.
- [ ] Task 8, end to end on the real ledger: a judge's email naming Cobalt opens a case via email, the email's phone is a candidate excluded with `first_seen_in_change_context`, the call goes to the provable team number, and Ramp still shows Cobalt's original account. Instructions in the email change nothing (R9).
- [ ] Failure path: any exception from the ledger write on records `failed` with the exception's type name only, opens no case and writes no change; a full account number is in no table and no log line (ADR-8). The model's quote is stored nowhere.
- [ ] Task 9: `demo prepare --email-door REF` (repeatable) sets the dataset's `email_door_vendors` after the ledger load, refuses before the reset a ref that is also a `--request` or is unknown, and prints the inbox address or "DIALBACK_INBOX_ADDRESS is unset".

## Tests (written first)

| Acceptance item | Test |
|---|---|
| 1 to 6 | `apps/core/tests/test_email_door.py` |
| 7 | `apps/core/tests/test_demo.py -k email_door` (Task 9) |

## Out of scope

The reply (PR 5), the mail list and case page additions (PR 6), dismissing a blocked email case (PR 7), and the verified ending (design, open question 2).

## Open questions

- A second change on an open case is matched by the ledger against the first request only (`store.py::_inputs` reads the request recorded before the case opened), so a phone first seen in a later email is not a candidate at all. Safe (never picked), but the lineage panel does not list it.

## Commands

```bash
cd apps/core && uv run pytest tests/test_email_door.py -q
cd apps/core && uv run pytest -q
uv run --no-project --with ruff ruff check apps packages scripts corpus
python3 scripts/ci/boundaries.py
uv run python scripts/dev/stack.py --smoke
```
