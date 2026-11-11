# 0085: Email door, receiving (PR 2 of the email door)

**Owner:** Sami  **Slice:** money path  **Target day:** Nov 10-11

## Goal

Resend receives a vendor's email and the core records that it arrived (design: `docs/superpowers/specs/2026-11-10-email-door-design.md`, sections 1 and 2). Task 3 adds the `DIALBACK_EMAIL` flag, its settings, Svix signature checking and `POST /webhooks/email`, which stores each signed notice. Task 4 makes the worker fetch the email from Resend and record it without its body. Nothing here reads the email's meaning or opens a case. A full account number is never stored or logged (ADR-8), and the email body is never persisted.

What the route stores (ADR-8: a subject line or a forged body can carry a full account number): a signed delivery keeps only the notice's `type`, `created_at` and `data.email_id`, `message_id`, `from` and `to`, with digit runs masked in every string of `from` and `to` (the sender types the display name and chooses `to`'s local part: Resend's free address takes any, so mail to `000123454821@<id>.resend.app` arrives; final review finding I2a). A bad delivery keeps only `{"sha256", "length"}` of its body (`event_id` stays `unsigned:<sha>`). The route has no secret in its path, so it raises at most one `bad_webhook_signature` alert per dataset per 10 minutes and stores at most 100 bad deliveries per dataset per hour; beyond that it answers 401 and stores nothing. A repeated `svix-id`, `svix-timestamp` or `svix-signature` header is a refusal. At startup the signing secret must be base64 (optionally prefixed `whsec_`) of at least 16 bytes. A bad delivery stores no header values, only the names of the kept headers that were present.

Note: attachments and subject are read only after the fetch (Task 4).

## Contract touched

None. `app.inbound_webhooks` already has `source`; this uses `source = 'resend'`.

## Acceptance

- [ ] Task 3: with `DIALBACK_EMAIL=off` the route is a 404. With a current demo dataset and a good Svix signature the notice is stored once (`source='resend'`, `event_id` the `svix-id`, `event_type` from the payload) and answered 200, repeats included. The Ramp webhook worker leaves `resend` deliveries alone.
- [ ] Task 4: the worker fetches each stored delivery's email from Resend and inserts an `app.inbound_emails` row (sender, subject, thread ID, `raw_sha256`; the row is made as `pending` when the delivery is first claimed and filled in by the fetch; the subject and the sender are stored with digit runs of 5 or more masked to their last four, also when the digits are grouped by up to 5 separator characters (whitespace, `,` `.` `-` `/` `_`, Unicode dashes U+2010 to U+2015 and U+2212, the zero-width characters U+200B to U+200D, U+2060 and U+FEFF, and the invisible formatting characters U+00AD, U+200E, U+200F, U+2066 to U+2069, U+202A to U+202E and U+061C; the last two groups are bidi controls a mail client can insert, final review finding I2b), ADR-8 (an accepted residual, by the Task 4 rulings: groups more than 5 separator characters apart, or with a letter or a visible symbol such as `*` `|` `:` `#` between them, are separate runs, so each keeps its digits when it has fewer than 5); a failed fetch is retried after 30 s, 2 min, 10 min and 30 min via `next_try_at`, any error counting a try (a failure before the email row exists, in `_open_row`, marks the delivery failed at once, without a row: there is no row to count a try on, so it is never retried), and the fifth failure records `failed`) with outcome `not_flagged`, or `door_off` when the dataset's `email_door_vendors` is empty. The body is kept nowhere. HTML-only mail becomes text for matching (in a thread, off the event loop the API shares); the thread ID is the first `References` ID, else `In-Reply-To`, else `Message-ID`.
- [ ] Task 4, bounded (final review finding I1): the claim order is due and never-tried deliveries first, then fewer tries, then received time, then id. One tick claims at most 20 deliveries and none once 5 s (`TICK_BUDGET_S`) of its email step have passed (the first claim always goes ahead); a fetch is cut off after 15 s of wall clock (`FETCH_TIMEOUT_S`; httpx's 10 s applies per operation), the timeout counting a try, and so is the reply's send (`SEND_TIMEOUT_S`, recorded as a failed reply). The model gets one attempt (`max_retries=0`, 30 s), since the inbox retries spaced. The worker runs the email step last in its tick, after the commands, so holds, calls and releases never wait behind it; an error escaping the email step, or the Ramp webhook step, ends that step only and is logged by type name. One tick's email step can still take about 5 + 15 + 30 + 15 s in the worst case (the budget, then one email's fetch, model call and send); a command queued while it runs waits for it.
- [ ] Failure path: a bad or stale signature is stored with `signature_valid=false`, raises a `bad_webhook_signature` alert and gets 401; no current demo dataset gets 503 so Resend retries; with `resend` and a missing `RESEND_API_KEY` or `RESEND_WEBHOOK_SECRET` the core refuses to start. In Task 4, Resend failing five ticks in a row records outcome `failed`, marks the delivery processed and opens no case (R7).

## Tests (written first)

| Acceptance item | Test |
|---|---|
| 1 | `apps/core/tests/test_email_route.py` (signature, off, stored once, 503, 413, worker leaves resend alone, startup refusal) |
| 2 | `apps/core/tests/test_email_inbox.py` |
| 3 | `test_email_inbox.py::test_the_email_step_stops_claiming_once_its_time_budget_is_spent`, `::test_a_fetch_that_never_answers_is_cut_off_and_retried`, `::test_a_reply_send_that_never_answers_is_cut_off`, `::test_html_becomes_text_off_the_event_loop`; `test_email_door.py::test_the_email_step_runs_after_the_commands_and_its_failure_is_contained`, `::test_a_failing_ramp_webhook_step_does_not_stop_the_email_step`; `email_door/test_classifier.py::test_the_client_tries_once_because_the_inbox_retries_spaced` |
| 4 | `test_email_route.py` and `test_email_inbox.py` |

## Out of scope

Reading the email (flags, account numbers, vendor match), the decision, the reply, the public page and the mail list. Later PRs.

## Open questions

None for this PR.

## Commands

```bash
cd apps/core && uv run pytest tests/test_email_route.py tests/test_email_inbox.py -q
cd apps/core && uv run pytest -q
```
