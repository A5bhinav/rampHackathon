# 0085: Email door, receiving (PR 2 of the email door)

**Owner:** Sami  **Slice:** money path  **Target day:** Nov 10-11

## Goal

Resend receives a vendor's email and the core records that it arrived (design: `docs/superpowers/specs/2026-11-10-email-door-design.md`, sections 1 and 2). Task 3 adds the `DIALBACK_EMAIL` flag, its settings, Svix signature checking and `POST /webhooks/email`, which stores each signed notice. Task 4 makes the worker fetch the email from Resend and record it without its body. Nothing here reads the email's meaning or opens a case. A full account number is never stored or logged (ADR-8), and the email body is never persisted.

What the route stores (ADR-8: a subject line or a forged body can carry a full account number): a signed delivery keeps only the notice's `type`, `created_at` and `data.email_id`, `message_id`, `from` and `to`. A bad delivery keeps only `{"sha256", "length"}` of its body (`event_id` stays `unsigned:<sha>`). The route has no secret in its path, so it raises at most one `bad_webhook_signature` alert per dataset per 10 minutes and stores at most 100 bad deliveries per dataset per hour; beyond that it answers 401 and stores nothing. A repeated `svix-id`, `svix-timestamp` or `svix-signature` header is a refusal. At startup the signing secret must be base64 (optionally prefixed `whsec_`) of at least 16 bytes. A bad delivery stores no header values, only the names of the kept headers that were present.

Note: attachments and subject are read only after the fetch (Task 4).

## Contract touched

None. `app.inbound_webhooks` already has `source`; this uses `source = 'resend'`.

## Acceptance

- [ ] Task 3: with `DIALBACK_EMAIL=off` the route is a 404. With a current demo dataset and a good Svix signature the notice is stored once (`source='resend'`, `event_id` the `svix-id`, `event_type` from the payload) and answered 200, repeats included. The Ramp webhook worker leaves `resend` deliveries alone.
- [ ] Task 4: the worker fetches each stored delivery's email from Resend and inserts an `app.inbound_emails` row (sender, subject, thread ID, `raw_sha256`; the row is made as `pending` when the delivery is first claimed and filled in by the fetch; the subject is stored with digit runs of 5 or more masked to their last four, ADR-8) with outcome `not_flagged`, or `door_off` when the dataset's `email_door_vendors` is empty. The body is kept nowhere. HTML-only mail becomes text for matching; the thread ID is the first `References` ID, else `In-Reply-To`, else `Message-ID`.
- [ ] Failure path: a bad or stale signature is stored with `signature_valid=false`, raises a `bad_webhook_signature` alert and gets 401; no current demo dataset gets 503 so Resend retries; with `resend` and a missing `RESEND_API_KEY` or `RESEND_WEBHOOK_SECRET` the core refuses to start. In Task 4, Resend failing five ticks in a row records outcome `failed`, marks the delivery processed and opens no case (R7).

## Tests (written first)

| Acceptance item | Test |
|---|---|
| 1 | `apps/core/tests/test_email_route.py` (signature, off, stored once, 503, 413, worker leaves resend alone, startup refusal) |
| 2 | `apps/core/tests/test_email_inbox.py` |
| 3 | both files above |

## Out of scope

Reading the email (flags, account numbers, vendor match), the decision, the reply, the public page and the mail list. Later PRs.

## Open questions

None for this PR.

## Commands

```bash
cd apps/core && uv run pytest tests/test_email_route.py tests/test_email_inbox.py -q
cd apps/core && uv run pytest -q
```
