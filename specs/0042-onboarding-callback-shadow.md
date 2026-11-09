# 0042: Onboarding callback, in shadow mode

**Owner:** Sami  **Slice:** call and adversary  **Target day:** after submission, for the Nov 19 final  **Builds on:** 0041

## Goal

Spec 0041 finds a new vendor's anchor from sources the vendor did not supply. This spec calls
that anchor once to confirm the company and that it is starting to work with the customer, and
records the result next to the anchor. A "no" from the anchor's line is a strong sign the vendor
entry is not what it claims; a "yes" is weaker, since a fake vendor owns its own line. Shadow mode
still: no case, no hold, and nothing outside Sami's slices reads the result. The case call path,
its guard and its contracts do not change. It serves R4 (the speech guard), R8 (consent first)
and the team dial allowlist.

## Contract touched

None. Onboarding answers are stored in the voice schema, not in `call-answers.schema.json`, and
`POST /calls` (`internal.yaml`) is unchanged: only Sami's code starts an onboarding call.

## How it works

- **Trigger:** `dialback-voice onboarding-call --report <0041 report> --vendor <id>`.
- **Guard,** separate from the case guard, which stays as it is: the vendor's 0041 outcome is
  `anchored`; the dialed number is that anchor; the number has not opted out; the vendor is inside
  its calling window; the AI-call cap per number holds; and, for the demo, the number is on the
  team allowlist. Any check that fails or can't be read refuses the call.
- **Script** (`packages/policy`, its own state machine): the fixed disclosure, then consent (a "no"
  ends the call); the right company; the right department; the person's name and role; "are you
  starting to work with <customer>?"; a close that reveals nothing. No bank questions and no
  digits; the speech guard is unchanged. The model words the questions and has the same four
  tools; `record_answer` slots come from a fixed onboarding list.
- **Store:** a new table `voice.onboarding_calls` in `supabase/migrations/voice/` (vendor, number,
  times, outcome, and the answers with their transcript turn ids, as `voice.call_answers` stores
  them today; never a recording or a raw transcript). `voice.calls` (which requires a case) is
  unchanged.
- **Outcome,** decided by code from the recorded answers: `confirmed` (consent, the right company,
  a named person, and "yes"), `denied` (consent, then the wrong company or "no"), otherwise
  `inconclusive`.

## Acceptance

- [ ] The case call path and its guard behave as before: the existing voice and policy tests pass unchanged.
- [ ] The onboarding guard refuses each of: no anchor, a number other than the anchor, an opted-out number, outside the window, the cap reached, not on the allowlist.
- [ ] `confirmed` needs consent, the right company, a named person and a "yes"; a consenting "no" or the wrong company gives `denied`; everything else gives `inconclusive`.
- [ ] No answer, busy, a Twilio error or a dropped call gives `inconclusive`, with no retry beyond the cap (fail closed).
- [ ] The speech guard blocks digits, amounts and bank names on onboarding calls exactly as on case calls.

## Tests (written first)

| Acceptance item | Test |
|---|---|
| 1 | `apps/voice/tests` and `packages/policy/tests`, unchanged and green |
| 2 | `apps/voice/tests/test_onboarding_guard.py::test_refuses_each_unsafe_dial` |
| 3 | `packages/policy/tests/test_onboarding_script.py::test_outcome_from_answers` |
| 4 | `apps/voice/tests/test_onboarding_call.py::test_non_connect_is_inconclusive` |
| 5 | `packages/policy/tests/test_onboarding_script.py::test_speech_guard_applies` |

## Out of scope

Using the result anywhere else: a case, the ledger's rule, `decide()`, the dashboard. Checking
who owns a bank account. Inbound calls. Changing an anchor.

## Open questions

- Q73: will customers accept Dialback calling their new vendors at onboarding? Should it be a customer setting?
- Q74: do onboarding calls share the case calls' cap (3 AI calls per number per 30 days) or have their own?
- Q75: which name does the script use for the customer: the dataset's business name in Ramp?

## Commands

```bash
cd apps/voice && uv run pytest -q
cd packages/policy && uv run pytest -q
python3 scripts/ci/boundaries.py
```
