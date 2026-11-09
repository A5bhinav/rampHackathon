# 0044: Don't call a line that looks taken over

**Owner:** Sami  **Slice:** call and adversary  **Target day:** not set

## Goal

Right before dialing, the voice service looks the number up with Twilio Lookup. If the number
shows signs it was moved to someone else, it refuses to dial with a new pre-dial code,
`line_risk`. The core already sends any pre-dial refusal to a person, with the hold kept on.

This only adds a reason not to call. It never lets a case through. Today nothing checks this
(FINDINGS A7; spec 0006: someone on the vendor's real line "can verify until a line-risk signal
exists").

## The checks

One lookup before each dial. Any one of these refuses:

1. **Carrier changed.** The line's `type` or `carrier_name` differs from the first lookup
   Dialback stored for this contact point. This covers a port. Coverage is worldwide, but Canada
   needs Twilio's approval.
2. **Reassigned.** `is_number_reassigned` is `"yes"`, with `LastVerifiedDate` set to the contact
   point's `first_seen_at` (`YYYYMMDD`). This is US only, and needs an access request.
3. **SIM swapped.** `last_sim_swap.swapped_in_period` is `true`. This is a private beta that needs
   carrier approval. The period is set when the account is set up with Twilio.

Settings and storage:
- `DIALBACK_LINE_CHECKS` lists which checks run. A live deployment defaults to `line_type`. The
  text line never looks anything up.
- The first lookup of a contact point is stored in `voice.line_checks`: `contact_point_id`, `type`
  and `carrier_name`, never the number. It is never overwritten.

What does not refuse:
- A lookup that errors, times out (3 seconds), returns an `error_code`, or returns no data for a
  check counts as no signal. The call goes ahead as today.

## Small things the code must do

- Add `line_risk` to `PreDialRefusal.code` in `internal.yaml`, in its own PR, together with the
  `LINE_RISK` constant in `guards.ALL_CODES` and its `REFUSAL_MESSAGE`.
  `test_guards.py::test_the_codes_are_exactly_the_contract_enum` requires the code and the enum to
  match.
- Run the check last, after every existing pre-dial check passes.
- Add one entry to core's `timeline.PRE_DIAL_REASON`: "the line shows signs of a recent port, SIM
  swap or reassignment". The banner reads that table.
- Set the `httpx` logger to WARNING in the voice entrypoint, because the lookup URL contains the
  number.
- Add `supabase/migrations/voice/0002_line_checks.sql`, with the same row-level security and grants
  as `voice.calls`. Apply it before the voice code ships.

## Acceptance

- [ ] The first call to a contact point stores its line and dials.
- [ ] A later call whose carrier or type differs from that first lookup returns 409 `line_risk`,
  and no call is placed. So does a retry.
- [ ] Reassigned `"yes"` refuses. `"no"` and `"no_data_available"` dial.
- [ ] SIM swap `true` refuses. `false`, and no data, dial.
- [ ] A lookup error or timeout dials.
- [ ] The text line, and an empty setting, make no lookup. A call refused by an earlier check makes
  no lookup.
- [ ] The phone number is in no stored column and no log line.
- [ ] The case goes to `needs_review` with the hold on, and the timeline shows the wording above.

## Tests (written first)

| Acceptance item | Test |
|---|---|
| Carrier changed, retry, order | `apps/voice/tests/test_calls_endpoint.py::test_line_risk` |
| Reassigned, SIM swap, errors, logs | `apps/voice/tests/test_line_check.py` (httpx `MockTransport`, Twilio's documented response shapes) |
| Storage | `apps/voice/tests/test_pg_store.py::test_line_checks_store_no_number` |
| Settings | `apps/voice/tests/test_wiring.py::test_line_check_follows_settings` |
| Timeline wording | `apps/core/tests/test_timeline.py::test_line_risk_reason` (a new file) |

## Out of scope

- **Call forwarding itself.** Twilio's check is a UK-only private beta.
- **Forwarding set inside the vendor's own phone system.** No carrier data shows it.
- **A port made before our first lookup of the number** (Q85).

## Open questions

These are my calls; Sami can overrule them.

- **Q83.** A lookup error dials as today, rather than refusing.
- **Q84.** A vendor that really changes carrier goes to a person on every later case. A person can
  verify it by hand, and the stored first lookup is never replaced automatically.
- **Q85.** Should numbers be looked up when the ledger first trusts them, so the first call already
  has something to compare against? That is a follow-up.
- **Q86.** Which SIM-swap period to ask Twilio for. Ask for the longest it offers, since days can
  pass between a swap and our call.

## Commands

```bash
uv run pytest apps/voice apps/core -q
uv run ruff check apps packages
uv run --with jsonschema --with rfc8785 --with pyyaml --with openapi-spec-validator python packages/contracts/scripts/validate.py
```
