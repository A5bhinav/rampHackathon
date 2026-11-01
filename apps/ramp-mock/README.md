# apps/ramp-mock: the Ramp-shaped mock

**Owner: Abhi. Partner reviewer: Sami.**

A small FastAPI service on Railway that copies Ramp's paths, field names and behavior, so
the whole product runs end to end before sandbox access arrives, and so the attack lab can
push changes through a real door. `apps/core` talks to it through the same `RampPort`
interface it uses for the real API; `DIALBACK_RAMP=mock` selects it.

## What it must copy

- Vendor holds that clear every unpaid bill's payment date, and releases that do not reschedule them.
- Thin, HMAC-signed webhooks (`X-Ramp-Signature` over the raw body) with retries.
- A switch that duplicates and reorders deliveries, and a "silent change" mode that sends no webhook at all, so the poller has to find the change.
- Last-four masking on account numbers.
- 429 responses past the rate limit (200 requests per 10 seconds per IP).
- An audit log whose events name the actor, or no actor for a vendor-supplied or ERP-synced change.

## Where the behavior lives

The rules above are SQL functions in the `mock_ramp` schema
(`supabase/migrations/mock_ramp/0001_mock_ramp.sql`), tested in `supabase/tests/004_mock_ramp.sql`.
The FastAPI layer is thin: it authenticates, rate-limits in memory, maps a route to a query or
a function call, and runs the webhook sender loop.

| Route | Backed by |
|---|---|
| `GET /developer/v1/vendors`, `?from_updated_at=` | `mock_ramp.vendors` |
| `GET /developer/v1/vendors/{id}` | `mock_ramp.vendors` |
| `GET /developer/v1/vendors/{id}/accounts` | `mock_ramp.vendor_accounts_api` (masked view) |
| `GET /developer/v1/vendors/{id}/contacts` | `mock_ramp.vendor_contacts` |
| `GET /developer/v1/bills` (pages of 100) | `mock_ramp.bills` |
| `POST /developer/v1/vendors/{id}/hold` | `mock_ramp.place_hold()` |
| `POST /developer/v1/vendors/{id}/release` | `mock_ramp.release_hold()` |
| bill update for `payment_date` (path to confirm) | `mock_ramp.bills` |
| `GET /developer/v1/audit-logs/events` | `mock_ramp.audit_events` |
| `POST /_mock/vendors/{id}/bank-change` (lab and demo only) | `mock_ramp.change_bank_account()` |
| `PATCH /_mock/settings` (lab only) | `mock_ramp.settings`: duplicate, reorder, silent, delay, fail-next-hold, fail-next-release, read-back lag |

The one-shot switches (`fail_next_hold`, `fail_next_release`) are read with
`mock_ramp.consume_switch(business_id, name)` in the route's own transaction *before* the
hold or release function runs; when it returns true the route commits and answers 500, so
the switch is spent even though the request failed.

The webhook sender claims `mock_ramp.webhook_deliveries` rows that are due, signs the
payload with the subscription secret, POSTs, records the response status, and retries
failures with backoff.

## One mock business per dataset

`mock_ramp.businesses.dataset_id` ties a mock business to an `app.datasets` row, so each
attack-lab scenario and each demo reset gets a fresh business with its own vendors, bills
and chaos settings, and scenarios run in parallel without touching each other.

## Fields to confirm against Ramp's OpenAPI spec

Column comments in the migration mark them: vendor `status`, `type`, `payment_method`;
account `account_type`; bill `status`; user `role` and `status`; the audit event type for a
bank-account change. Commit Ramp's OpenAPI spec to `packages/contracts/openapi/ramp-developer-api.json`
when you have it, and adjust the mock in one PR.

## Start here

1. `scripts/db/test.sh` and read `supabase/tests/004_mock_ramp.sql`: it is the behavior spec.
2. Scaffold `src/dialback_ramp_mock/main.py` with the routes above, a bearer-token check, and an in-memory token bucket per client IP.
3. Write the webhook sender as a background task in the same process.
4. Seed script: one business, three vendors, a few bills with payment dates, one subscription pointing at `apps/core`.
5. Nov 6: chaos mode wired to `/_mock/settings` for the lab.
