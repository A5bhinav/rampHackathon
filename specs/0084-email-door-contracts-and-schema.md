# 0084: Email door, contracts and stored mail (PR 1 of the email door)

**Owner:** Sami  **Slice:** money path  **Target day:** Nov 10-11

## Goal

A bank-change email will open a Dialback case on its own (design: `docs/superpowers/specs/2026-11-10-email-door-design.md`). This PR lays the ground for it and nothing more: the API contract (Task 1) and the database tables (Task 2). No handler, webhook, reply or page ships here. A full account number is never stored or returned (ADR-8), and the email body is never returned.

## Contract touched

`packages/contracts/openapi/dialback.yaml`, additive only, regenerated with `pnpm gen:api`:
- Schemas `MailItem`, `EmailInfo`, `MailOutcome`, `ReplyStatus`, `RedFlag`.
- `ChangeInfo.email` (optional, `EmailInfo` or null) and `Permissions.can_dismiss` (optional boolean).
- `GET /api/datasets/{dataset_id}/mail` (`listMail`): `{ items: MailItem[] }`, newest first, at most 200.

## Acceptance

- [ ] Task 1: the contract validates, `pnpm gen:api`, `pnpm check:api` and `pnpm typecheck` pass, and no existing required list changed.
- [ ] Task 2: `app.datasets.email_door_vendors uuid[] not null default '{}'` and table `app.inbound_emails` exist (columns per the plan's Task 2; `unique (dataset_id, resend_email_id)`; checks on `outcome`, `reply` and `last4`; `raw_sha256` is null until the email is fetched). An authenticated person reads the rows (the repo has no per-person dataset membership), a demo account only those of demo datasets; only `service_role` writes.
- [ ] Failure path: an unknown `outcome`, a duplicate `(dataset_id, resend_email_id)` or an insert by `authenticated` is rejected.

## Tests (written first)

| Acceptance item | Test |
|---|---|
| 1 | `packages/contracts/scripts/validate.py` (inline `listMail` example), `pnpm check:api`, `pnpm typecheck` |
| 2, 3 | `supabase/tests/016_email_door.sql`, `scripts/db/test_migrate.py` |

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
