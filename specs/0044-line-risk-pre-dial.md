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
never called, and the reviewer's timeline says why.

This only adds a reason not to call. It never lets a case through, so no check gets weaker. It
serves R2 and R7.

## Decisions

- **D1. A pre-dial check, not a call fact.** `line_risk` joins the codes in `PreDialRefusal.code`
  (`internal.yaml`), `guards.ALL_CODES` and `REFUSAL_MESSAGE`. It runs last, after every existing
  check has passed, so a lookup is paid for only on a call that would otherwise be placed.
  - The core already turns any refusal into `command_failed`, which sends the case to
    `needs_review`.
  - The only core change is one entry in `timeline.PRE_DIAL_REASON`: "the line shows signs of a
    recent port, SIM swap or reassignment". Without it, the banner shows the raw code.
  - `CaseFacts.line_risk` and NR21 stay as they are. They would fire only after a call, and this
    check stops the call before it happens.
- **D2. The signals.** Twilio Lookup v2 provides all three. They are switched on with a setting,
  `DIALBACK_LINE_CHECKS`, a comma list of `line_type`, `reassigned` and `sim_swap`; a live
  deployment defaults to `line_type`. Any one signal is enough to refuse.

  | Signal | Lookup package | Refuses when | Coverage (Twilio's docs, Nov 9) |
  |---|---|---|---|
  | Ported | Line Type Intelligence | D3 | worldwide, except Canada without special approval |
  | Reassigned | Reassigned Number, with `LastVerifiedDate` = the contact point's `first_seen_at` as `YYYYMMDD` | `is_number_reassigned` is `"yes"` | US only, access by request, data updated monthly on the 16th |
  | SIM swap | SIM Swap | `last_sim_swap.swapped_in_period` is `true` | private beta; North America, Europe and Latin America; carrier approval |

  - For the reassigned check, `first_seen_at` is the receive time of the first clean document that
    showed the number, not the vendor's first real use. Together with Twilio's monthly updates,
    that can miss a recent reassignment. It never causes a wrong refusal.
  - For the SIM swap check, the period is fixed when the account is set up with Twilio, not per
    request (Q86).
- **D3. The port rule, which a retry cannot get around.** Each lookup stores the pair (`type`,
  `carrier_name`), compared exactly as Twilio returns it (Q84).
  - **Refuse when** the current pair was first stored less than 90 days ago, and an earlier row for
    this contact point has a different pair. The 90 days are the ledger's own.
  - **A first lookup raises nothing**, because there is no history yet (Q85).
  - **A refused lookup is still stored.** So an attacker who raises a second bank change straight
    away is refused again. A vendor that really changed carrier is called automatically once the
    new pair has been on record for 90 days; until then, its cases go to a person.
  - **Only rows that returned line-type data count.**
- **D4. No answer is no signal.** For a package, "no answer" means any of: an error or timeout (3
  seconds), an `error_code`, or a null package (an unsupported country or carrier). That package
  raises nothing, and the call goes ahead as today.
  - The stored row records which packages answered.
  - This is my call: these signals cover only some carriers and countries, so an outage is treated
    like no coverage, and the two-person release still guards the money (Q83).
  - A database error follows today's path: a 500, the core retries, then `needs_review`.
    Migration 0002 must be applied before the voice code ships.
- **D5. Stored per contact point, never the number.** Each lookup is stored in a new table,
  `voice.line_checks`, in migration `supabase/migrations/voice/0002_line_checks.sql`. A row holds:
  - `dataset_id`, `contact_point_id`, `checked_at` and `source` (`twilio_lookup`);
  - `line_type`, `carrier_name`, `mobile_country_code` and `mobile_network_code`;
  - `reassigned` (text, as Twilio returns it) and `sim_swapped` (a nullable boolean);
  - `packages_ok`, `risk` and `reasons`.

  It gets the same row-level security and grants as `voice.calls`.
- **D6. Logs carry no number.** A lookup puts the number in the URL path, and httpx 0.28 logs every
  request URL at INFO. So the voice entrypoint sets the `httpx` logger to WARNING.
- **D7. Wiring.** A `LineCheck` protocol, `check(e164, first_seen) -> LineFacts`, has one real
  implementation, `TwilioLookup`. It uses httpx and the `TwilioConfig` credentials, and calls
  `GET https://lookups.twilio.com/v2/PhoneNumbers/{e164}?Fields=...`.
  - `create_app` takes `line_check: LineCheck | None`, separately from `TwilioDeps`, because
    `POST /calls` runs in both modes.
  - `build_app` builds a `TwilioLookup` only when the deployment has a Twilio config and the
    setting is not empty. The text line never looks anything up.
  - Tests inject a fake. No deployment uses one.
  - The pick query (`pg_store.PICK`) also reads `cp.first_seen_at`.

## Contract touched

`packages/contracts/openapi/internal.yaml`: add `line_risk` to the `PreDialRefusal.code` enum.

`test_guards.py::test_the_codes_are_exactly_the_contract_enum` requires the voice codes and the
enum to match. So this contract PR also adds the `LINE_RISK` constant to `guards.ALL_CODES`, and its
`REFUSAL_MESSAGE` entry. Nothing is generated from `internal.yaml`. The check itself ships in the
code PR.

## Acceptance

- [ ] **Ported (D3).**
  - The first `POST /calls` for a contact point stores a row and dials.
  - A later lookup with a different pair returns 409 `line_risk`, places no call, and stores the
    row with the reason.
  - A third lookup within 90 days of the new pair's first row is refused again.
  - Once the new pair has been on record for 90 days, the call dials.
- [ ] **Reassigned.** `LastVerifiedDate` is the `first_seen_at` date as `YYYYMMDD`. `"yes"`
  refuses. `"no"` and `"no_data_available"` dial.
- [ ] **SIM swap.** `swapped_in_period: true` refuses. `false`, and a null package, dial.
- [ ] **No answer (D4).** A timeout, an HTTP error, an `error_code` or a null package dials, and
  `packages_ok` leaves that package out.
- [ ] **Off.** The text line, or an empty `DIALBACK_LINE_CHECKS`, makes no lookup.
- [ ] **Order.** A call refused by an earlier check makes no lookup.
- [ ] **No number kept.** No column of `voice.line_checks` holds the phone number, and no log
  line written during a lookup contains it.
- [ ] **The core.** A 409 `line_risk` lands as `command_failed`, the case goes to `needs_review`
  with the hold on, and the timeline reads D1's wording.
- [ ] **The contract.** The contract validation and the enum test pass after each of the two PRs.

## Tests (written first)

| Acceptance item | Test |
|---|---|
| Ported, order | `apps/voice/tests/test_guards.py::test_line_risk_is_checked_last`, `apps/voice/tests/test_calls_endpoint.py::test_port_rule` (parametrized over the four ported cases) |
| Reassigned, SIM swap, no answer | `apps/voice/tests/test_line_check.py` (httpx `MockTransport` with Twilio's documented response shapes) |
| No number kept | `apps/voice/tests/test_pg_store.py::test_line_checks_store_no_number`, `apps/voice/tests/test_line_check.py::test_lookup_logs_no_number` |
| Off | `apps/voice/tests/test_wiring.py::test_line_check_follows_settings` |
| The core | `apps/core/tests/test_timeline.py::test_line_risk_reason` (a new file; routing is already covered by `test_worker.py::test_a_pre_dial_refusal_sends_the_case_to_a_person`) |
| The contract | the existing `test_guards.py::test_the_codes_are_exactly_the_contract_enum` |

## Out of scope

- **Call forwarding.** Twilio's check is a UK-only private beta.
- **Forwarding set inside the vendor's own phone system or portal.** No carrier lookup can see it.
- **Showing the reviewer which signal fired.** The reasons are in `voice.line_checks`, but no screen
  reads them yet.
- **A demo of the refusal.** Demo resets start fresh contact points, so a port cannot show.
- **Feeding `CaseFacts.line_risk` and NR21**, and reworking the voice lab's a07 (spec 0017).

## Open questions

- **Q83.** On a lookup error, should the call go ahead (D4) or be refused?
- **Q84.** Should the port check compare `carrier_name` exactly (this spec), or normalize it, or use
  `mobile_country_code` and `mobile_network_code` for mobiles? Exact comparison sends a vendor to a
  person when Twilio renames a carrier.
- **Q85.** Should Dialback look each number up when the ledger first trusts it, or on a schedule,
  so the first call already has history?
- **Q86.** Which SIM-swap period should we ask Twilio for when the account is set up? Days can pass
  between a swap and our call: the hold, the selection, the calling window, a weekend.

## Commands

```bash
uv run pytest apps/voice apps/core -q
uv run ruff check apps packages
uv run --with jsonschema --with rfc8785 --with pyyaml --with openapi-spec-validator python packages/contracts/scripts/validate.py
```
