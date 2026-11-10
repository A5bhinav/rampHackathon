# 0097: A translation layer for Ramp's sandbox, used only by sandbox datasets

**Owner:** Sami  **Slice:** money path  **Target day:** before the Nov 19 final  **Builds on:** 0096

## Goal

Spec 0096 routes a sandbox dataset to Ramp's sandbox, but the adapter still reads the mock's field
names, so syncing a sandbox dataset fails. When this is done, a separate adapter used only for
sandbox datasets talks to Ramp in Ramp's real shapes and hands the core exactly the objects it gets
from the mock today. The mock, the demo, the attack lab and the pipeline (detection, the worker, the
jobs) don't change, so sandbox support can be removed by deleting this adapter and 0096's routing.

Wherever Ramp's behavior isn't documented, the layer chooses the reading that fails closed (R7):
a wrong guess sends a case to a person, never a payment to a new account (R1).

## Contract touched

None.

## Decisions

- **D1. Where.** `ramp/sandbox.py`: `RampSandboxAdapter`, built on the shared HTTP adapter (rate
  limit, retries, pagination, login). The connector uses it for sandbox datasets only. The shared
  adapter gains one opt-in: a call may accept an empty 202 or 204, as Ramp's hold and release answer.
- **D2. Ramp to the core**, field by field:

  | Core object | From Ramp |
  |---|---|
  | vendor | `id`, `name`; `status` is `ACTIVE` or `INACTIVE` from `is_active`; `updated_at` |
  | bank account | `id`; `last4` from `account_number_last_four`, which must be four digits or the call fails (never invented); `is_active` when `status` is `active`; `routing_number` empty, because Ramp never returns it; `bank_name`, `account_type`, `created_at` empty |
  | bill | `id`; `vendor_id` from `vendor.id`; `payment_date` the date part of `payment.payment_date`; `paid` when `status` is `PAID` or `paid_at` is set; `is_on_hold` (D3) |
  | audit event | `id`; `actor_id`; `occurred_at` from `event_time`; `target_id` from `primary_reference.id`; `payload` from `event_details`; `event_type` (D4) |
  | hold | Ramp answers 202 or 204 with no body, so the hold has no Ramp id (`""`) |

  Paths: `GET /vendors` (`from_updated_at`), `GET /vendors/{id}`, `GET /vendors/{id}/accounts`,
  `GET /bills` (`vendor_id`), `POST /vendors/{id}/hold` (`reason`) and `/release`,
  `PATCH /bills/{id}` (`payment_date`), `GET /audit-logs/events` (`object_id`, `event_types` joined
  with commas). Pages of 100, Ramp's largest, followed through `page.next`.
- **D3. The hold read-back.** The worker confirms a hold only when the vendor shows one and every
  unpaid bill shows held. Ramp's vendor has no hold field, so `hold_id` is the newest of Ramp's
  `Vendor placed on hold` and `Vendor hold released` audit events for the vendor, when it is a
  placement. A bill shows held when `status_summary` is `ON_HOLD`, or when it is unpaid and has no
  payment date, since Ramp says a vendor hold takes its bills out of scheduled payment.
- **D4. Event names.** The core asks for `VENDOR_BANK_ACCOUNT_UPDATED`; the layer asks Ramp for
  `Bill pay bank account updated` and answers under the core's name. A core name with no Ramp
  equivalent is a `RampError`. Ramp doesn't document `event_details` for this event, so the change's
  author stays unknown, and the release needs two approvers (ADR-10).

## Acceptance

- [ ] Each of the eight calls sends Ramp's documented path, method, parameters and body.
- [ ] Each answer becomes the core's object exactly as in D2, D3 and D4, from Ramp-shaped data.
- [ ] An account without four last digits, and an event name with no Ramp equivalent, raise a
      `RampError` that isn't retried.
- [ ] A sandbox dataset reaches the sandbox through this adapter; a mock dataset through the
      unchanged mock adapter.
- [ ] Importing a sandbox dataset's vendors stores them and their accounts, with an empty routing
      number and Ramp's last four.
- [ ] Everything else is unchanged: the core and SQL suites and the smoke run pass.

## Tests (written first)

| Acceptance item | Test |
|---|---|
| 1-3 | `apps/core/tests/test_ramp_sandbox_shapes.py::test_*` |
| 4 | `apps/core/tests/test_ramp_sandbox_shapes.py::test_routing_*` |
| 5 | `apps/core/tests/test_ramp_sandbox_shapes.py::test_import_*` |
| 6 | `cd apps/core && uv run pytest`, `scripts/db/test.sh`, `uv run python scripts/dev/stack.py --smoke` |

## Out of scope

- Changing the mock to Ramp's shapes (option B, not chosen), and the webhook setup.
- Spotting a bank change while it waits for approval in Ramp (`include_pending`).
- Checking these fixtures against Ramp's document with spec 0078's checker, once both are merged.

## Open questions (a sandbox run answers each; a wrong answer fails closed)

- **Q202:** does Ramp file a vendor's hold and release events under the vendor's id (`object_id`)?
  If not, no hold is ever confirmed, and every sandbox case goes to a person.
- **Q203:** do a held vendor's bills show `ON_HOLD`, or lose their payment date? If neither, the same.
- **Q204:** is `Bill pay bank account updated` the event for a vendor's new bank account, and does
  its `event_details` name the new account? Until then, the author is unknown (two approvers).

## Commands

```bash
cd apps/core && uv run pytest -q
scripts/db/test.sh
uv run python scripts/dev/stack.py --smoke
```
