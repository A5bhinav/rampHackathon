# 0084: Email door, contracts and stored mail (PR 1 of the email door)

**Owner:** Sami  **Slice:** money path  **Target day:** Nov 10-11

## Goal

A bank-change email will open a Dialback case on its own (design: `docs/superpowers/specs/2026-11-10-email-door-design.md`). This PR lays the ground for it and nothing more: the API contract (Task 1) and the database tables (Task 2). No handler, webhook, reply or page ships here. A full account number is never stored or returned (ADR-8), and the email body is never returned.

## Contract touched

`packages/contracts/openapi/dialback.yaml`, additive only, regenerated with `pnpm gen:api`:
- Schemas `MailItem`, `EmailInfo`, `MailOutcome`, `ReplyStatus`, `RedFlag`. `MailOutcome` is `pending`, `not_flagged`, `door_off`, `not_demo_vendor`, `no_vendor`, `several_vendors`, `no_routing_number`, `no_account_number`, `same_as_on_file`, `repeat_request`, `case_opened`, `joined_case`, `ramp_case_open`, `failed`. `ramp_case_open` (added by the final review's C1 fix, while this PR is unmerged): the vendor's unfinished case has a change that did not come by email, so the email changed nothing; its `case_id` names that case.
- `ChangeInfo.email` (optional, `EmailInfo` or null) and `Permissions.can_dismiss` (optional boolean).
- `GET /api/datasets/{dataset_id}/mail` (`listMail`): `{ items: MailItem[] }`, newest first, at most 200.

## Acceptance

- [ ] Task 1: the contract validates, `pnpm gen:api`, `pnpm check:api` and `pnpm typecheck` pass, and no existing required list changed.
- [ ] Task 2: `app.datasets.email_door_vendors uuid[] not null default '{}'` and table `app.inbound_emails` exist, with the columns `id` (identity), `dataset_id`, `delivery_id` (references `app.inbound_webhooks`, indexed), `resend_email_id`, `message_id`, `thread_id`, `sender` (not null), `sender_domain`, `subject`, `received_at` (not null), `raw_sha256` (null until the email is fetched), `has_attachments` (not null), `flagged` (default false), `outcome` (default `pending`, checked against the `MailOutcome` values), `reply` (default `not_flagged`, checked against the `ReplyStatus` values), `vendor_id`, `case_id`, `change_event_id`, `routing_number`, `last4` (four digits or null), `red_flags` (`text[]`, default empty), `processed_at`, `tries` (default 0), `error`, and `next_try_at` (spec 0085, migration 0021); `unique (dataset_id, resend_email_id)`. Never a body or a full account number. The tables come from migration 0020; 0023 replaces the `outcome` check to add `ramp_case_open`, and 0024 adds the `delivery_id` index (0020 is never edited after it is applied). The door's migrations are numbered 0020 to 0024, after main's 0018 and 0019. An authenticated person reads the rows (the repo has no per-person dataset membership), a demo account only those of demo datasets; only `service_role` writes.
- [ ] Failure path: an unknown `outcome`, a duplicate `(dataset_id, resend_email_id)` or an insert by `authenticated` is rejected.

## Tests (written first)

| Acceptance item | Test |
|---|---|
| 1 | `packages/contracts/scripts/validate.py` (inline `listMail` example), `pnpm check:api`, `pnpm typecheck` |
| 2, 3 | `supabase/tests/019_email_door.sql`, `scripts/db/test_migrate.py` |

## Out of scope

Receiving (webhook, signature), parsing, red-flag and decision logic, the reply, dismiss, `GET /mail` handler, and every web page. Later PRs.

## Open questions

None for this PR.

## Commands

```bash
uv run --with jsonschema --with rfc8785 --with pyyaml --with openapi-spec-validator python packages/contracts/scripts/validate.py
cd apps/web && pnpm gen:api && pnpm check:api && pnpm typecheck
cd apps/core && uv run pytest tests/test_api.py tests/test_clerk_change.py -q
dbtest
```
