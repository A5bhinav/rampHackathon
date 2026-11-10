# 0088: Email door, the fixed reply (PR 5 of the email door)

**Owner:** Sami  **Slice:** money path  **Target day:** Nov 10-11

## Goal

Every flagged email gets one fixed reply (design: `docs/superpowers/specs/2026-11-10-email-door-design.md`, section 8 and "What Sami's later decisions add"), the same whatever the door did with it, so it tells a sender nothing. It goes only to the `From` address, never `Reply-To`, and is held back for robots, lists, ourselves, unauthenticated senders and rate limits. Until a domain exists `DIALBACK_REPLY_FROM` is unset, nothing is sent and the row says `not_configured`. Task 10 builds this.

## Contract touched

None. `app.inbound_emails.reply` (spec 0084) already has every value used here.

## Acceptance

- [ ] The reply body is the approved wording with `<company>` from `DIALBACK_COMPANY_NAME`, then a blank line and `DIALBACK_BANK_CHANGES_URL` on the last line (no URL set: the wording alone). It holds nothing from the incoming email; the subject is `Re: ` plus the original (not doubled), with CR, LF and NUL removed, capped at 200 characters, digit runs masked.
- [ ] Only a row that ends flagged (including a silent change) gets a reply, after its outcome is final. Other rows keep `reply = 'not_flagged'`. The reply happens once per row.
- [ ] `decide_reply` checks in order: not configured; suppressed (`Auto-Submitted` present and not `no`, `Precedence` bulk, list or junk, `List-Id` present, sender is the inbox or `noreply`, `no-reply`, `mailer-daemon`, or From has no single plain address); unauthenticated (neither `dmarc` nor `dkim` is `pass`, or no authentication data); rate limited (a `sent` row for the same masked sender address in the last 24 hours, or 20 `sent` rows in the last hour, on the dataset clock); else sent.
- [ ] The send uses `in_reply_to = Message-ID` and an `Idempotency-Key` derived from the email's Resend id, so a retry never sends twice.
- [ ] Failure path: a send error records `reply = 'failed'`, never raises out of `handle_email`, never undoes the case outcome, and is logged and stored by its type name only. No email text or address is logged.

## Tests (written first)

| Acceptance item | Test |
|---|---|
| 1 | `apps/core/tests/email_door/test_reply.py::test_reply_text_is_the_approved_wording`, `::test_the_subject_is_cleaned`, `::test_the_subject_is_capped_at_200_characters` |
| 2 | `apps/core/tests/test_email_door.py::test_unflagged_mail_gets_no_reply`, `::test_a_silent_change_gets_the_reply`, `::test_without_reply_from_nothing_is_sent_and_it_says_so` |
| 3 | `apps/core/tests/email_door/test_reply.py` (one test per branch), `apps/core/tests/test_email_door.py::test_a_second_email_from_the_same_sender_is_rate_limited` and the other rate tests; `::test_a_flagged_email_gets_the_fixed_reply_to_its_from_address` (never Reply-To) |
| 4 | `test_reply.py::test_the_client_sends_an_idempotency_key_and_threads_the_reply` |
| 5 | `apps/core/tests/test_email_door.py::test_a_send_error_records_failed_and_keeps_the_case` |

## Out of scope

Showing the reply status in the dashboard (PR 6). A real domain and sending setup.

## Open questions

None. Counts compare the stored (masked) sender address, which for a real delivery is the From header.

## Commands

```bash
source .superpowers/sdd/2026-11-10-email-door/testenv.sh
cd apps/core && uv run pytest -q tests/email_door/test_reply.py tests/test_email_door.py && uv run pytest -q
```
