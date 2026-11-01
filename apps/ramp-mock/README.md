# apps/ramp-mock: the Ramp-shaped mock

**Owner: Abhi. Partner reviewer: Sami.**

A small FastAPI service that copies Ramp's paths, field names and behavior, so the whole
product runs end to end before sandbox access arrives, and so the attack lab can push
changes through a real door. The core talks to it with the same HTTP adapter it will use
for Ramp's sandbox (`DIALBACK_RAMP=mock` only changes the base URL and the credentials;
spec 0002, D1).

## Run it

```bash
DATABASE_URL=postgresql://... MOCK_RAMP_SECRET=$(openssl rand -hex 24) uv run --package dialback-ramp-mock dialback-ramp-mock
```

`PORT` defaults to 8003. `MOCK_RAMP_DELIVER_WEBHOOKS=0` turns off the background webhook
sender (deliveries then go out only through `POST /_mock/webhooks/deliver`).

## One business per dataset, one token per dataset

Every request carries `Authorization: Bearer mock.<dataset_id>.<hex HMAC-SHA256(MOCK_RAMP_SECRET, dataset_id)>`.
The mock serves only that dataset's business, so the lab's parallel scenarios and the demo
never see each other's vendors. Whoever holds the secret (the core, the lab) mints tokens;
nothing is stored. `dialback_ramp_mock.tokens.mint()` is the reference implementation.

## What it must copy, and where that lives

The rules are SQL functions in `supabase/migrations/mock_ramp/`, tested in
`supabase/tests/004_mock_ramp.sql`; this service is the HTTP layer over them.

- Vendor holds clear every unpaid bill's payment date; releases do not reschedule them.
- Thin, HMAC-signed webhooks (`X-Ramp-Signature`, hex, over the raw body) with retries and backoff.
- Switches that duplicate and reorder deliveries, delay them, or send none at all ("silent change"), so the poller has to find the change.
- Account numbers masked to the last four digits. The full number never leaves the mock.
- 429 with `Retry-After` past the rate limit (200 requests per 10 seconds per IP by default).
- An audit log whose bank-change events name the actor, or no actor for a vendor-supplied or ERP-synced change.

## Routes

| Route | What it does |
|---|---|
| `GET /developer/v1/vendors` (`from_updated_at`, `page_size`, `start`) | Vendors, oldest first, with `page.next` |
| `GET /developer/v1/vendors/{id}` | One vendor, with its active `hold` or null |
| `GET /developer/v1/vendors/{id}/accounts` | Accounts, masked: `account_number_last4` only |
| `GET /developer/v1/vendors/{id}/contacts` | Contacts |
| `POST /developer/v1/vendors/{id}/hold` | Place a vendor hold (idempotent); clears unpaid bills' payment dates |
| `POST /developer/v1/vendors/{id}/release` | Lift the hold; dates stay cleared |
| `GET /developer/v1/bills` (`vendor_id`, `page_size`, `start`) | Bills, with `is_on_hold` and `payment_date` |
| `PATCH /developer/v1/bills/{id}` | Set an unpaid bill's `payment_date` (how Dialback restores a schedule) |
| `GET /developer/v1/audit-logs/events` (`target_id`, `event_type`, `from_occurred_at`) | Audit events with the actor |
| `POST /_mock/seed` | Create the token's business if needed, then add users, vendors (accounts, contacts, bills), webhook subscriptions and settings. Every entity can carry a `ref`; the response maps refs to ids. For Inesh's demo seed and Sami's lab. |
| `POST /_mock/vendors/{id}/bank-change` | A bank change (optionally by a seeded user): new active account, audit event, webhook unless silent |
| `PATCH /_mock/settings` | Chaos switches: `duplicate_webhooks`, `reorder_webhooks`, `silent_changes`, `webhook_delay_ms`, `rate_limit_per_10s`, `fail_next_hold`, `fail_next_release`, `hold_readback_lag_ms` |
| `POST /_mock/payment-run` (`as_of`) | Pay every approved, unpaid bill due by `as_of`, unless it or its vendor is held. This is how the demo shows R1. |
| `POST /_mock/webhooks/deliver` | Send this dataset's queued webhooks now, in scheduled order |

The one-shot failure switches are consumed in the request's own transaction and answered
with 500, so a switch is spent even though the request failed.

**Webhook subscriptions point at the core per dataset:** `{core}/webhooks/ramp/{dataset_id}`
(spec 0002, D3), with the secret the core verifies.

## Fields to confirm against Ramp's OpenAPI spec

`hold` on a vendor and `is_on_hold` on a bill (Q4), the webhook signature encoding and
payload shape (Q3), the bill update path, the audit event type for a bank-account change,
and the status and role enums marked "confirm" in the migration. Commit Ramp's spec to
`packages/contracts/openapi/ramp-developer-api.json` when we have it, and adjust the mock in
one PR.

## Tests

```bash
cd apps/ramp-mock && uv run pytest
```

The tests start a throwaway Postgres (or use `DATABASE_URL`), migrate it with the same
scripts as `scripts/db/test.sh`, run the app in process, and capture outgoing webhooks with a
fake receiver. Each test seeds its own dataset.
