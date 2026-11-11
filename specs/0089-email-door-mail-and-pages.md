# 0089: Email door, the mail list and the pages (PR 6 of the email door)

**Owner:** Sami  **Slice:** money path  **Target day:** Nov 10-11

## Goal

The dashboard can show what the email door did with each email. The core serves the mail list for a dataset and, for a case an email opened, the email that started it (design: `docs/superpowers/specs/2026-11-10-email-door-design.md`). Neither carries an email body or a full account number (ADR-8). Task 11 builds the core API; Task 12 adds the dashboard pages and the public page.

## Contract touched

`packages/contracts/openapi/dialback.yaml`: `GET /api/datasets/{dataset_id}/mail` (`listMail`), `MailItem`, `EmailInfo` and `ChangeInfo.email`. Already added by Task 1 in its own PR; this spec only implements them.

## Acceptance

- [ ] `GET /api/datasets/{dataset_id}/mail` returns `{items}`, newest first, at most 200, read as the signed-in person (row-level security applies). Each item has the vendor as `{id, legal_name}` or null, and the case id, routing number and last four when the email gave them.
- [ ] Signed out is 401. An unknown dataset, or one the person cannot see, is 404 `not_found`. One dataset's mail never shows in another's.
- [ ] In the case detail, `change.email` is the stored email whose `change_event_id` is the case's current change event (sender, subject, received_at, red_flags), or null when the change did not come from an email.
- [ ] Failure path: no response holds an email body or a full account number. The table has neither and the queries select neither; a test checks the response text anyway.
- [ ] The AP sidebar has a "Mail" page at `/d/<dataset>/mail` (not "Inbox", which names the case queue). Each row shows received time, sender, subject, flagged, outcome, reply, red flags, vendor, a link to the case when there is one, and routing plus last four when given. No body. An empty list shows a short sentence.
- [ ] `outcomeLabel`, `replyLabel` and `flagLabel` give a non-empty plain ASCII sentence for every value of `MailOutcome` (13), `ReplyStatus` (7) and `RedFlag` (5). The value lists come from the generated contract, so a new value without a label fails.
- [ ] The case page shows an email card (sender, subject, received time, red flags as badges, routing and last four) only when `change.email` is non-null.
- [ ] The public page `/bank-changes` needs no sign-in, shows no data, and says exactly the approved wording (`<company>` as "the company you're writing to").

## Tests (written first)

| Acceptance item | Test |
|---|---|
| 1 | `apps/core/tests/test_email_api.py::test_mail_lists_outcomes_newest_first_and_matches_the_contract`, `::test_mail_returns_at_most_200_items` |
| 2 | `apps/core/tests/test_email_api.py::test_mail_is_refused_signed_out_and_scoped_to_the_dataset` |
| 3 | `apps/core/tests/test_email_api.py::test_case_detail_carries_the_email_info` |
| 4 | `apps/core/tests/test_email_api.py::test_no_mail_response_holds_a_body_or_full_number` |
| 5 | `apps/web/tests/apps.test.ts` (AP nav lists Mail); the page itself is checked by `pnpm build` and by opening it |
| 6 | `apps/web/tests/mail.test.ts` (three tests) |
| 7 | typed by `pnpm typecheck`; checked by opening a case an email opened |
| 8 | `apps/web/tests/bank-changes.test.ts` |

## Out of scope

Sending mail.

## Open questions

None.

## Commands

```bash
cd apps/core && uv run pytest -q tests/test_email_api.py && uv run pytest -q
cd ../web && pnpm check:api && pnpm typecheck && pnpm test && pnpm build
```
