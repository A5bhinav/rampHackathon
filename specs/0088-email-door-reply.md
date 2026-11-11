# 0088: Email door, the fixed reply (PR 5 of the email door)

**Owner:** Sami  **Slice:** money path  **Target day:** Nov 10-11

## Goal

Every flagged email gets one fixed reply (design: `docs/superpowers/specs/2026-11-10-email-door-design.md`, section 8 and "What Sami's later decisions add"), the same whatever the door did with it, so it tells a sender nothing. It goes only to the `From` address, never `Reply-To`, and is held back for robots, lists, ourselves, unauthenticated senders and rate limits. Until a domain exists `DIALBACK_REPLY_FROM` is unset, nothing is sent and the row says `not_configured`. Task 10 builds this.

## Contract touched

None. `app.inbound_emails.reply` (spec 0084) already has every value used here.

## Acceptance

- [ ] The reply body is the approved wording with `<company>` from `DIALBACK_COMPANY_NAME`, then a blank line and `DIALBACK_BANK_CHANGES_URL` on the last line. The subject is fixed: `Re: your message about payment details`, threaded by `In-Reply-To`. Nothing of the incoming email is in the reply. Without `DIALBACK_REPLY_FROM` or without `DIALBACK_BANK_CHANGES_URL` the reply is `not_configured` (the reply carries the link).
- [ ] Only a row that ends flagged (including a silent change) gets a reply, after its outcome is final. Other rows keep `reply = 'not_flagged'`. The reply happens once per row.
- [ ] `decide_reply` checks in order: not configured; suppressed (the raw `From` header, `headers["from"]`, does not hold exactly one mailbox or its address differs from the bare address Resend reports as `sender`; `Auto-Submitted` present and not `no`; `Precedence` bulk, list or junk; `List-Id` present; the address is `DIALBACK_REPLY_FROM`'s, the inbox's, any address at the inbox's domain, or `noreply`, `no-reply`, `mailer-daemon`); unauthenticated (neither `dmarc` nor `dkim` is `pass`, compared trimmed and lowercase, or no authentication data; Resend reports dkim "gray" when the signing domain does not match From, so a dkim pass is aligned); rate limited (a `sent` row for the same masked destination address in the last 24 hours, a stored sender that cannot be read counting as a match, or 20 `sent` rows in the last hour, on the dataset clock); else sent.
- [ ] The send carries `Auto-Submitted: auto-replied`, an `In-Reply-To` only when the Message-ID is `<printable ASCII, no spaces>` of at most 250 characters, and an `Idempotency-Key` derived from the email's Resend id, so a retry never sends twice.
- [ ] Failure path: a send error records `reply = 'failed'`, never raises out of `handle_email`, never undoes the case outcome, and is logged and stored by its type name only. No email text or address is logged. A database error while deciding is logged by type name and records nothing.

## Known limits

- `sent` is recorded before the send, under a lock per dataset, so the counts are right with several workers. A crash between the record and the send leaves `sent` with nothing sent. A timeout after Resend accepted the mail leaves `failed`, and the same sender could then get a second reply.
- The inbox claim transaction is held open across the send (up to the 10 second HTTP timeout).

## Tests (written first)

| Acceptance item | Test |
|---|---|
| 1 | `apps/core/tests/email_door/test_reply.py::test_reply_text_is_the_approved_wording`, `::test_the_subject_is_fixed_and_never_the_senders`, `test_email_door.py::test_without_a_link_the_reply_is_not_configured`, `::test_the_reply_never_echoes_the_subject` |
| 2 | `apps/core/tests/test_email_door.py::test_unflagged_mail_gets_no_reply`, `::test_a_silent_change_gets_the_reply`, `::test_without_reply_from_nothing_is_sent_and_it_says_so` |
| 3 | `apps/core/tests/email_door/test_reply.py` (one test per branch), `apps/core/tests/test_email_door.py::test_a_second_email_from_the_same_sender_is_rate_limited` and the other rate tests; `::test_a_flagged_email_gets_the_fixed_reply_to_its_from_address` (never Reply-To), `::test_a_production_shaped_email_with_two_from_mailboxes_gets_no_reply`, `::test_a_second_decision_on_a_sent_row_changes_nothing`, `::test_the_hourly_cap_counts_the_last_sixty_minutes`, `::test_in_reply_to_is_sent_only_for_a_well_formed_message_id` |
| 4 | `test_reply.py::test_the_client_sends_an_idempotency_key_and_threads_the_reply` |
| 5 | `apps/core/tests/test_email_door.py::test_a_send_error_records_failed_and_keeps_the_case` |

## Out of scope

Showing the reply status in the dashboard (PR 6). A real domain and sending setup.

## Open questions

None.

## Commands

```bash
cd apps/core && uv run pytest -q tests/email_door/test_reply.py tests/test_email_door.py && uv run pytest -q
```
