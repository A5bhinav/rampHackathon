# 0043: Keypad-only last four, and one more try on an unclear answer

**Owner:** Sami  **Slice:** call and adversary  **Target day:** not set

## Goal

An honest vendor can lose a change today because of how the call hears them. When this is done:
- the last four come only from the keypad, with the pound key to submit and the star key to start
  over;
- an answer the call could not make out gets exactly one more try;
- caller text that goes to the model has its digits removed.

Every path this touches still fails closed (R7). The paths to `verified` are unchanged: the same
rules on the same recorded answers, with the four now typed. It serves R3, R4, R7, R9 and ADR-8.

The defects come from `evals/FINDINGS.md` on `sami/attack-lab-generative`, as of origin/main da057f8:

| # | Today | Where | Effect |
|---|---|---|---|
| A14 | The question invites speech, and spoken digits are misread with confidence. "Forty eight twenty one" is read as 8201. "Zero zero zero one, those are the last four" is read as 0014. A callback number's tail wins over the account's. | `script.py`, `claude_agent.py` `_value`, `guard.digit_runs` | A misread is NV02, so the case is blocked, and only a new change request reopens it |
| A13 | `*` is ignored. The fourth digit submits at once. `#` submits 1 to 3 digits. | `relay.py` `_next_event` | A slip cannot be fixed, and a short entry is NR06 with no retry |
| A12 | An unclear answer is recorded and the call moves on. An unclear consent ("Hello?") is recorded as `no`, and the call ends. | `step.py`, `claude_agent.py` | An honest vendor is turned away, and the case goes to a person (NR02) |
| A11, in part | The scripted double reads "Sure, no problem." as `no`. | `agent.py` | The text line ends calls the policy would continue |

**Why this opens no hole.**
- The voice service never receives the account's digits: `POST /calls` carries only the case id and
  the attempt, and `CallDial` has no digits. So no re-ask can reveal whether an entry was right.
  That is also why a wrong answer is never re-asked: the service cannot know one.
- Whoever set up the new account knows its digits, so typing instead of speaking gives an attacker
  nothing.
- An injected "say unclear" buys one re-ask, then the call ends without that answer.

## Decisions

- **D1. Keypad only.** The last-four question joins `FIXED_WORDING`, and its wording changes to:
  "For the account you'd like to be paid into, please use your phone's keypad to enter the last
  four digits, then press the pound key. To start over, press the star key."
  - No digits are ever taken from speech.
  - The guard's amount words take `pounds?` everywhere except right before "key" or "sign", so "50
    pounds" and "the pound amount" still trip it (`guard.py` `_AMOUNT_WORDS`). This is my call: "the
    hash key" passes today, but US callers know the key as pound.
  - **Cost:** a caller with no usable keypad ends without digits, so the case goes to a person.
- **D2. Keys, in the relay.** The relay handles these because `step` never reads a clock (spec 0012).
  - Digits collect until `#`. There is no auto-submit at four.
  - `*` empties what is typed and emits a new `KeypadReset` event.
  - If digits are in hand and no key comes for 5 seconds, the entry is submitted as if `#` were
    pressed. The 5 seconds is my call (Q78).
  - The relay keeps at most 16 keys and drops further digits. The entry is still submitted, and
    the policy refuses it.
  - A spoken turn or a silence still clears a partial entry, as today.
- **D3. Exactly four, in the policy.** On the digits question, `_keypad` records only an entry of
  exactly four digits: `capture_method: keypad`, grounded, and written to the transcript only as
  "[keypad]".
  - Any other entry is unclear (D5).
  - On `KeypadReset`, the call says "Okay, let's start over. Please enter the last four digits, then
    press the pound key." This does not use up the one more try (D5). Each reset adds a callee turn,
    so `MAX_CALLEE_TURNS` bounds them.
  - Keys pressed outside the digits question, or before consent, still mean nothing.
- **D4. Speech at the digits question.** No digit is ever recorded from speech. `step` decides
  before it asks the agent.
  - A turn with a run of three or more digits (`digit_runs`, the guard's own threshold for spoken
    digits) is unclear, and its re-ask line is "Please don't say the digits. Enter them on your
    phone's keypad, then press the pound key."
  - Any other turn goes to the agent, to be read as `refused`, `dont_know` or `unclear`. This
    includes "Oh, one moment", which `digit_runs` reads as 01. `refused` and `dont_know` are
    recorded as today, which gives NR06.
- **D5. One more try.** "Unclear" means one of: the agent's `unclear`, a keypad entry that is not
  four digits, or D4's spoken digits. It applies to consent, the change question and the digits
  question.
  - **First unclear on a question:** the call says `REASK`, then the question's fixed wording (or
    D4's line).
  - **Second unclear, at consent or the change question:** the call ends with result `failed` and
    reason `unclear:<phase>`. Nothing is recorded for that question, and a failed report carries no
    answers (`app.py`). The core redials while the case has call attempts left, and otherwise sends
    the case to a person (`docs/state-machine.md`; a `failed` result counts as no person in
    `app.eval_guard`, migration `app/0011`). No `retry_at` is set, so the core waits its default 2 hours
    (Q82).
  - **Second unclear, at the digits question:** the call finishes `answers_recorded` with no digits,
    keeping every other answer, so `decide` gives NR06 and a person sees the full call. This is my
    call: a redial would make an honest vendor answer everything again, for a keypad problem a
    redial will not fix (Q79).
  - The violation count (`MAX_VIOLATIONS`) and the silence count keep their own budgets.
- **D6. Unclear is never an answer.**
  - An agent says it with `AgentReply(unclear=True)`, and the text line and the relay turn that
    into a new policy event, `AgentUnclear(at)`.
  - An empty reply is still `AgentFailed`.
  - Nothing new is recorded, so no contract changes.
- **D7. The model may say unclear.** `ClaudeAgent`'s allowed answers become:

  | Question | Allowed answers |
  |---|---|
  | Consent | `yes`, `no`, `unclear` |
  | The change | `yes`, `no`, `unsure`, `unclear` |
  | The digits question, spoken without digits | `refused`, `dont_know`, `unclear` |

  - The prompt defines `unclear` as: "you cannot tell what they answered (they asked you to repeat,
    the words are cut off or garbled, or they answered something else)". `unsure` still means
    "they said they are not sure".
  - A model error or an out-of-set answer becomes `unclear`, where today it is the phase's safe
    value. So a timeout gets one more try, and a `no` the caller never said is never recorded.
    This revises spec 0015's fail-closed values; it is my call (Q80).
- **D8. Digits never reach the model.** `ClaudeAgent` replaces each run of three or more digits
  with `[digits]` in the text it sends to the model, through a `redact_digits` helper beside
  `digit_runs`.
  - Only the model sees the redacted text. `AgentRequest.transcript`, grounding, the guard and the
    pressure flag all keep the turn as said.
  - The free-text answers never go to the model and are recorded as said, so "3M" and "84 Lumber"
    are unchanged.
- **D9. The double reads yes and no the policy's way.** The policy exposes
  `read_yes_no(text, allow_unsure) -> "yes" | "no" | "unsure" | "unclear"`, built from the same
  patterns as `_grounded`, and `ScriptedAgent` uses it.
  - So "Sure, no problem." is `yes`, "We did." is `yes`, and "Hello?" is `unclear`.
  - With `allow_unsure=False` (consent), `unsure` becomes `unclear`.
  - At the digits question, the double reads refusal and "don't know" as today, and anything else
    as `unclear`.

## Contract touched

None:
- `account_last4` values and `capture_method: keypad` already exist (`call-answers.schema.json`).
- `unclear` is never recorded.
- `failed` is an existing `CallEndedBody.result`.
- `End.reason` is internal to the voice service.
- `AgentUnclear` and `KeypadReset` are internal policy events.

## Acceptance

- [ ] **Keypad only (D1, D4).** The digits question uses D1's wording, and no model words it.
  - A spoken "4821", or "four eight two one", records nothing and is re-asked with D4's line.
  - A second spoken entry finishes the call with no digits (NR06).
  - "I'd rather not say" records `refused`.
  - "Oh, one moment" gets the plain re-ask, not D4's line.
- [ ] **Keys (D2, D3).** Through the relay:

  | Keys | Result |
  |---|---|
  | `4 8 2 1 #` | records 4821 |
  | `4 8 * 4 8 2 1 #` | records 4821, the start-over line plays once, and no re-ask is used |
  | `4 8 2 1`, then 5 seconds with no key | records 4821 |
  | `4 8 #` | re-asked once |
  | `4 8 #` then `4 8 #` | finishes with no digits (NR06) |
  | `4 8 2 1 5 #` | re-asked once |
  | 20 digits | refused, and the relay holds at most 16 |

  - Typed digits never appear in any transcript turn.
- [ ] **Unclear consent and change (D5, D7).**
  - "Hello?" at consent is re-asked, and a following "Yes." continues the call.
  - "Hello?" twice ends `failed`, reason `unclear:consent`. The report has no `answers` and no
    `retry_at`.
  - The change question works the same way, with reason `unclear:change`.
  - A model timeout at consent is re-asked; it never records `no`.
- [ ] **Redaction (D8).** At the change question, "Yes, the account ending four eight two one"
  reaches the model as "Yes, the account ending [digits]", and the recorded `yes` is still
  grounded. A company answer of "84 Lumber" is recorded and grounded as said.
- [ ] **Double (D9).** `ScriptedAgent` reads "Sure, no problem." as consent `yes`, "We did." as
  change `yes`, and "Hello?" as `unclear`.
- [ ] **Fail closed, R4.**
  - A typed four that is wrong is still NV02.
  - A clean keypad call still reaches `verified` through `decide`.
  - No unclear or reset path records an answer.
  - Every new fixed line, and every `QUESTIONS` wording, passes the speech guard.
  - "50 pounds" and "the pound amount" still fail the guard.
- [ ] **Docs.** `docs/live-call.md` replaces "the digits trap" with the keypad steps, and its
  consent row says an unclear answer is asked once more.

## Tests (written first)

| Acceptance item | Test |
|---|---|
| Keypad only | `packages/policy/tests/test_step.py::test_spoken_digits_are_not_recorded_and_reasked`, `::test_digits_question_is_fixed_wording` |
| Keys | `apps/voice/tests/test_relay.py::test_keypad_sequences` (parametrized over the table), `::test_keypad_timeout_submits` |
| Unclear consent and change | `packages/policy/tests/test_step.py::test_unclear_is_asked_once_more`, `::test_second_unclear_ends_without_the_answer`; `apps/voice/tests/test_claude_agent.py::test_model_error_is_unclear` |
| Redaction | `apps/voice/tests/test_claude_agent.py::test_model_never_sees_spoken_digits`, `packages/policy/tests/test_step.py::test_numbered_company_is_kept_as_said` |
| Double | `apps/voice/tests/test_transport.py::test_double_reads_yes_no_like_the_policy` |
| Fail closed, R4 | `apps/voice/tests/test_transport.py::test_clean_keypad_call_verifies`, `packages/policy/tests/test_script.py` (the existing guard check over every wording), `packages/policy/tests/test_guard.py::test_pound_key_is_not_an_amount` |

## Out of scope

- The company check (A10, A8). Model extraction of the company name is its own spec, and
  `company.py` belongs to `apps/core`.
- The refused role (A9), and unclear answers to the free-text questions.
- Earlier-call evidence never reaching `decide` (FINDINGS A16).
- Removing the guard's exception for reading back spoken digits. It is harmless now, because no
  agent writes text.
- Redacting digits from the in-memory transcript itself (Q81).
- The voice lab (spec 0017, not yet merged). Any frozen scenario whose call flow changes is
  refrozen by whichever of the two lands second.

## Open questions

None open. Sami approved the spec and every call in it as written on Nov 9:

- **Q78, resolved:** submit after 5 seconds with no key.
- **Q79, resolved:** a second unclear on the digits question finishes the call with no digits
  (NR06). Consent and the change question end `failed`, which leads to a redial.
- **Q80, resolved:** a model error becomes `unclear` (D7), revising spec 0015's safe values.
- **Q81, resolved for now:** only what the model gets is redacted. Redacting the transcript itself
  is out of scope.
- **Q82, resolved:** an `unclear:*` failure sets no `retry_at`, so the core waits 2 hours.

## Commands

```bash
uv run pytest packages/policy apps/voice -q
uv run ruff check apps packages
uv run --with jsonschema --with rfc8785 --with pyyaml --with openapi-spec-validator python packages/contracts/scripts/validate.py
```
