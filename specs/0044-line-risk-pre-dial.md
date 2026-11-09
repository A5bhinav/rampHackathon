# 0044: Don't call a line that looks taken over

**Owner:** Sami  **Slice:** call and adversary  **Target day:** not set

## Goal

Dialback calls a number the change request could not have planted, but whoever controls where
that number rings controls the call. Three takeovers move a vendor's number and are visible in
carrier data:
- a port to another carrier;
- a SIM swap;
- the number being reassigned to someone else.

Today nothing looks for them. `CaseFacts.line_risk` is never set, and spec 0006 says it plainly:
someone on the vendor's real line "can verify until a line-risk signal exists" (FINDINGS A7).

When this is done, the voice service looks the number up with Twilio Lookup right before it dials.
If the line shows one of these signs, it refuses to dial with a new pre-dial code, `line_risk`. The
core already sends any pre-dial refusal to `needs_review`, with the hold kept on. The attacker is
never called, and a person sees why.

This only adds a reason not to call. It never lets a case through, so no check gets weaker. It
serves R2 and R7.

## Decisions

- **D1. A pre-dial check, not a call fact.** `line_risk` joins the codes in
  `PreDialRefusal.code` (`internal.yaml`) and `guards.py`. It runs last, after every existing check
  has passed, so a lookup is paid for only on a call that would otherwise be placed.
  - The core passes any refusal code through (`ports.py`, `PreDialRefused`) to `command_failed`, so
    no core or decision code changes.
  - `CaseFacts.line_risk` and NR21 stay as they are. They would fire only after a call, and this
    check stops the call before it happens.
- **D2. The signals.** Twilio Lookup v2 provides all three. Each is turned on by a setting,
  `DIALBACK_LINE_CHECKS`, a comma list of `line_type`, `reassigned` and `sim_swap`; live mode
  defaults to `line_type`. Any one signal is enough to refuse.

  | Signal | Lookup package | Fires when | Coverage (Twilio's docs, Nov 9) |
  |---|---|---|---|
  | Ported | Line Type Intelligence | `type` or `carrier_name` differs from the last lookup Dialback stored for this contact point | worldwide, self-service |
  | Reassigned | Reassigned Number, with `LastVerifiedDate` = the contact point's `first_seen_at` | `is_number_reassigned` is true | US only, access by request |
  | SIM swap | SIM Swap | `last_sim_swap.swapped_in_period` is true | private beta; North America, Europe and Latin America; carrier approval |

  The first lookup of a contact point only stores a baseline, so a port before that lookup goes
  unseen (Q85). The reassigned check needs no baseline, because the ledger's `first_seen_at` is the
  date the vendor's own documents first showed the number.
- **D3. No answer is no signal.** A lookup that errors or times out (3 seconds), or a package that
  returns an `error_code`, raises nothing for that package, and the call goes ahead as today. The
  stored row says which packages ran. This is my call: these signals cover only some carriers and
  countries, so an outage is treated like no coverage, and the two-person release still guards the
  money (Q83).
- **D4. Stored per contact point, never the number.** Each lookup is stored in a new table,
  `voice.line_checks`, in migration `supabase/migrations/voice/0002_line_checks.sql`. A row holds:
  - `dataset_id`, `contact_point_id` and `checked_at`;
  - `line_type`, `carrier_name`, `mobile_country_code` and `mobile_network_code`;
  - `reassigned` and `sim_swapped`, each a nullable boolean;
  - `packages_ok` (which packages returned data), `risk` (a boolean) and `reasons` (a text array).

  The phone number is never stored. It goes to Twilio for the lookup, as it already does for the
  dial. The table gets the same row-level security and grants as `voice.calls`.
- **D5. Ports and wiring.** A `LineCheck` protocol, `check(e164, first_seen) -> LineFacts`, has two
  implementations:
  - `TwilioLookup`: httpx, the existing `TwilioConfig` credentials,
    `GET https://lookups.twilio.com/v2/PhoneNumbers/{e164}?Fields=...`.
  - `FakeLineCheck`: for tests, the text line and the demo. It can be set to report a port.

  `wire.py` picks between them the way it picks the agent. The pick query (`pg_store.PICK`) also
  reads `cp.first_seen_at`.
- **D6. What the reviewer sees.** `REFUSAL_MESSAGE["line_risk"]` is "The phone line shows signs of
  a recent port, SIM swap or reassignment, so it was not called." It names no number, carrier or
  case data.

## Contract touched

`packages/contracts/openapi/internal.yaml`: add `line_risk` to the `PreDialRefusal.code` enum, and
regenerate any generated types. This goes in its own PR, before the code PR.

## Acceptance

- [ ] **Ported.** The first `POST /calls` for a contact point stores a baseline row and dials. A
  later call where `type` or `carrier_name` differs returns 409 `line_risk`, places no call, and
  stores a row with `risk` true and the reason.
- [ ] **Reassigned.** With `reassigned` on, the lookup sends `LastVerifiedDate` as the
  `first_seen_at` date. `is_number_reassigned: true` refuses, and `false` dials.
- [ ] **SIM swap.** With `sim_swap` on, `swapped_in_period: true` refuses, and `false` dials.
- [ ] **No answer (D3).** A timeout, an HTTP error, or a package's `error_code` dials as today, and
  the row's `packages_ok` leaves that package out.
- [ ] **Off.** With `DIALBACK_LINE_CHECKS` empty, no lookup is made.
- [ ] **Order.** A call refused by an earlier check (outside the window, cap reached and so on)
  makes no lookup.
- [ ] **No number stored.** No column of `voice.line_checks` holds the phone number.
- [ ] **The core.** A 409 `line_risk` from the voice service lands as `command_failed` with "pre-dial
  check refused: line_risk", and the case goes to `needs_review` with the hold on.
- [ ] **The contract.** The contract validation passes with the new code.

## Tests (written first)

| Acceptance item | Test |
|---|---|
| Ported, reassigned, SIM swap, order | `apps/voice/tests/test_guards.py::test_line_risk_is_checked_last`, `apps/voice/tests/test_calls_endpoint.py::test_line_risk_refuses_without_dialing` |
| The Twilio request and parsing, no answer | `apps/voice/tests/test_line_check.py` (httpx `MockTransport` with Twilio's documented response shapes) |
| Storage, no number stored | `apps/voice/tests/test_pg_store.py::test_line_checks_store_no_number` |
| Off, wiring | `apps/voice/tests/test_wiring.py::test_line_check_follows_settings` |
| The core | `apps/core/tests/test_worker.py::test_line_risk_refusal_goes_to_review` (if the existing pre-dial test is per code) |

## Out of scope

- **Call forwarding.** Twilio's check is a UK-only private beta.
- **Forwarding set inside the vendor's own phone system or portal.** No carrier lookup can see it.
- **Feeding `CaseFacts.line_risk` and NR21**, which would need a call-report contract change.
- **Reworking the voice lab's a07** (spec 0017) to reach this refusal instead of declaring
  `line_risk`.

## Open questions

- **Q83.** On a lookup error, should the call go ahead (D3) or be refused?
- **Q84.** Should the port check compare `carrier_name` as Twilio returns it, or normalize it, or
  use `mobile_country_code` and `mobile_network_code` for mobiles? Exact comparison may send a
  vendor to a person when Twilio renames a carrier.
- **Q85.** Should Dialback also look up each trusted number when the ledger first trusts it, or on a
  schedule, so the first call already has a baseline?

## Commands

```bash
uv run pytest apps/voice -q
uv run ruff check apps packages
uv run --with jsonschema --with rfc8785 --with pyyaml --with openapi-spec-validator python packages/contracts/scripts/validate.py
```
