# 0043: Keypad-only last four, and one more try on an unclear answer

**Owner:** Sami  **Slice:** call and adversary  **Target day:** not set

## Goal

An honest vendor can lose a change today because of how the call hears them. When this is done:
- the last four come only from the keypad, with `#` to submit and `*` to start over;
- an answer the call could not make out gets exactly one more try;
- caller text reaches the model with its digits removed.

Every path this touches still fails closed (R7). The paths to `verified` are unchanged: the same
rules on the same recorded answers, with the four now typed. It serves R3, R4, R7, R9 and ADR-8.

The defects come from `evals/FINDINGS.md` on `sami/attack-lab-generative`, as of origin/main da057f8:

| # | Today | Where | Effect |
|---|---|---|---|
| A14 | The question invites speech, and spoken digits are misread with confidence. "Forty eight twenty one" is read as 8201. "Zero zero zero one, those are the last four" is read as 0014. A callback number's tail wins over the account's. | `script.py`, `claude_agent.py` `_value`, `guard.digit_runs` | A misread is NV02 on this call and on every redial of the generation |
| A13 | `*` is ignored. The fourth digit submits at once. `#` submits 1 to 3 digits. | `relay.py` `_next_event` | A slip cannot be fixed, and a short entry is NR06 with no retry |
| A12 | An unclear answer is recorded and the call moves on. An unclear consent ("Hello?") is recorded as `no`, and the call ends. | `step.py`, `claude_agent.py` | An honest vendor is turned away, and the invented `no` raises NR19 on every redial |
| A11, in part | The scripted double reads "Sure, no problem." as `no`. | `agent.py` | The text line ends calls the policy would continue |

**Why this opens no hole.**
- The voice service never receives the account's digits: `POST /calls` carries only the case id and
  the attempt. So no re-ask can reveal whether an entry was right. That is also why a wrong answer
  is never re-asked: the service cannot know one.
- Whoever set up the new account knows its digits, so typing instead of speaking gives an attacker
  nothing.
- An injected "say unclear" buys one re-ask, then the call ends `failed`.

## Decisions

- **D1. Keypad only.** The last-four question joins `FIXED_WORDING`, and its wording changes to:
  "For the account you'd like to be paid into, please use your phone's keypad to enter the last
  four digits, then press pound. To start over, press star."
  - No digits are ever taken from speech.
  - **Cost:** a caller with no usable keypad ends without digits, so the case goes to a person.
- **D2. Keys, in the relay.** The relay handles these because `step` never reads a clock (spec 0012).
  - Digits collect until `#`. There is no auto-submit at four.
  - `*` empties what is typed and emits a new `KeypadReset` event.
  - If digits are in hand and no key comes for 5 seconds, the entry is submitted as if `#` were
    pressed. The 5 seconds is my call (Q69).
  - The relay keeps at most 16 keys and drops further digits. The entry is still submitted, and
    the policy refuses it.
  - A spoken turn or a silence still clears a partial entry, as today.
- **D3. Exactly four, in the policy.** On the digits question, `_keypad` records only an entry of
  exactly four digits: `capture_method: keypad`, grounded, and written to the transcript only as
  "[keypad]".
  - Any other entry is unclear (D5).
  - On `KeypadReset`, the call says "Okay, let's start over. Please enter the last four digits,
    then press pound." This does not use up the one more try (D5). Each reset adds a callee turn,
    so `MAX_CALLEE_TURNS` bounds them.
  - Keys pressed outside the digits question, or before consent, still mean nothing.
- **D4. Speech at the digits question.** `step` decides before it asks the agent.
  - A turn with any digit run (`digit_runs`, so spoken forms count) is unclear. Its digits are
    dropped, and the re-ask line is "Please don't say the digits. Enter them on your phone's
    keypad, then press pound."
  - Any other turn goes to the agent, redacted (D8), to be read as `refused`, `dont_know` or
    `unclear`. `refused` and `dont_know` are recorded as today, which gives NR06.
- **D5. One more try.** "Unclear" means one of: the agent's `unclear`, a keypad entry that is not
  four digits, or spoken digits on the digits question. It applies to consent, the change
  question and the digits question.
  - **First unclear on a question:** the call says `REASK`, then the question's fixed wording (or
    D4's line).
  - **Second unclear on the same question:** the call ends with result `failed` and reason
    `unclear:<phase>`, and nothing is recorded for that question. A failed call is NR01, so a
    person or the core's redial takes it. Because nothing is invented, no `no` raises NR19 later
    (Q70).
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
  | The digits question, spoken with no digits | `refused`, `dont_know`, `unclear` |

  - The prompt defines `unclear` as: "you cannot tell what they answered (they asked you to repeat,
    the words are cut off or garbled, or they answered something else)". `unsure` still means
    "they said they are not sure".
  - A model error or an out-of-set answer becomes `unclear`, where today it is the phase's safe
    value. So a timeout gets one more try, and a `no` the caller never said is never recorded.
    This revises spec 0015's fail-closed values; it is my call (Q71).
- **D8. Digits never reach the model.** In every callee turn of `AgentRequest.transcript`, each
  digit run found by `digit_runs` is replaced by `[digits]`.
  - Grounding, the guard and the pressure flag still read the turn as said, in memory.
  - After D1, no agent needs spoken digits. Neither agent records `offered_number` today.
- **D9. The double reads yes and no the policy's way.** The policy exposes
  `read_yes_no(text, allow_unsure) -> "yes" | "no" | "unsure" | "unclear"`, built from the same
  patterns as `_grounded`, and `ScriptedAgent` uses it.
  - So "Sure, no problem." is `yes`, "We did." is `yes`, and "Hello?" is `unclear`.
  - With `allow_unsure=False` (consent), `unsure` becomes `unclear`.
  - The double's digits path goes away with D1.

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
  - A second spoken entry ends the call `failed`, reason `unclear:last4`.
  - "I'd rather not say" records `refused`.
- [ ] **Keys (D2, D3).** Through the relay:

  | Keys | Result |
  |---|---|
  | `4 8 2 1 #` | records 4821 |
  | `4 8 * 4 8 2 1 #` | records 4821, the start-over line plays once, and no re-ask is used |
  | `4 8 2 1`, then 5 seconds with no key | records 4821 |
  | `4 8 #` | re-asked once |
  | `4 8 #` then `4 8 #` | ends `failed`, reason `unclear:last4` |
  | `4 8 2 1 5 #` | re-asked once |
  | 20 digits | refused, and the relay holds at most 16 |

  - Typed digits never appear in any transcript turn.
- [ ] **Unclear consent and change (D5, D7).**
  - "Hello?" at consent is re-asked, and a following "Yes." continues the call.
  - "Hello?" twice ends `failed`, reason `unclear:consent`, with no consent recorded. `decide` gives
    NR01, not NR02 or NR19.
  - The change question works the same way, with reason `unclear:change`.
  - A model timeout at consent is re-asked; it never records `no`.
- [ ] **Redaction (D8).** At the change question, "Yes, the account ending four eight two one"
  reaches the model as "Yes, the account ending [digits]", and the recorded answer is still
  grounded.
- [ ] **Double (D9).** `ScriptedAgent` reads "Sure, no problem." as consent `yes`, "We did." as
  change `yes`, and "Hello?" as `unclear`.
- [ ] **Fail closed, R4.**
  - A typed four that is wrong is still NV02.
  - A clean keypad call still reaches `verified` through `decide`.
  - No unclear or reset path records an answer.
  - Every new fixed line passes the speech guard and contains no digits.
- [ ] **Docs.** `docs/live-call.md` replaces "the digits trap" with the keypad steps, and its
  consent row says an unclear answer is asked once more.

## Tests (written first)

| Acceptance item | Test |
|---|---|
| Keypad only | `packages/policy/tests/test_step.py::test_spoken_digits_are_not_recorded_and_reasked`, `::test_digits_question_is_fixed_wording` |
| Keys | `apps/voice/tests/test_relay.py::test_keypad_sequences` (parametrized over the table), `::test_keypad_timeout_submits` |
| Unclear consent and change | `packages/policy/tests/test_step.py::test_unclear_is_asked_once_more`, `::test_second_unclear_ends_failed_without_an_answer`; `apps/voice/tests/test_claude_agent.py::test_model_error_is_unclear` |
| Redaction | `packages/policy/tests/test_step.py::test_agent_never_sees_spoken_digits` |
| Double | `apps/voice/tests/test_transport.py::test_double_reads_yes_no_like_the_policy` |
| Fail closed, R4 | `apps/voice/tests/test_transport.py::test_clean_keypad_call_verifies`, `packages/policy/tests/test_script.py::test_new_lines_pass_the_guard` |

## Out of scope

- The company check (A10, A8). Model extraction of the company name is its own spec, and
  `company.py` belongs to `apps/core`.
- The refused role (A9), and unclear answers to the free-text questions.
- Removing the guard's exception for reading back spoken digits. It is harmless now, because no
  agent writes text.
- Redacting digits from the in-memory transcript itself (Q72).
- The voice lab (spec 0017, not yet merged). Any frozen scenario whose call flow changes is
  refrozen by whichever of the two lands second.

## Open questions

- **Q69.** How long to wait after the last key before submitting? This spec uses 5 seconds.
- **Q70.** After a second unclear on the digits question, should the call end `failed` (NR01,
  maybe a redial), or finish the call with no digits (NR06)? This spec uses `failed` for all three
  questions.
- **Q71.** Should a model error become `unclear` (D7), or keep spec 0015's safe value?
- **Q72.** Should spoken digits be redacted from the transcript as soon as they are heard, not only
  in what the agent gets?

## Commands

```bash
uv run pytest packages/policy apps/voice -q
uv run ruff check apps packages
uv run --with jsonschema --with rfc8785 --with pyyaml --with openapi-spec-validator python packages/contracts/scripts/validate.py
```
