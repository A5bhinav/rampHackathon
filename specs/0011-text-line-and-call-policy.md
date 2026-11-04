# 0011: The text phone line and the call policy

**Owner:** Sami  **Slice:** call and adversary  **Target day:** Wed Nov 4

## Goal

A call runs end to end with no Twilio:

1. The core's `POST /calls` reaches the real voice service.
2. `policy.step` drives the script.
3. A scripted vendor answers on the text phone line.
4. The voice service reports `call_ended` to the core as `internal.yaml` says.

This replaces the `ScriptedVoice` test double in `scripts/dev/stack.py`. It is the same `step` the Twilio adapter (next spec) and the attack lab will call (ADR-5; R3, R4, R7, R8, R9).

## Decisions (proposed; Sami to confirm)

1. **Three layers.**
   - **`packages/policy`** is pure, with no I/O:
     - `script.py`: states and fixed text (the disclosure, the consent question, the voicemail message, the fallback line, the close);
     - `tools.py`: the four tools' argument schemas, with strict validation;
     - `guard.py`: the speech guard;
     - `step.py`: `step(state, event) -> Step`.
   - **The model sits outside `step`, behind an `Agent` port.**
     - Input: the current question and the quoted transcript (R9). Output: text to say and tool calls.
     - `step` validates both before anything is spoken or recorded.
     - Tests and the dev stack use `ScriptedAgent`, a labeled test double. Live calls and the lab use Claude.
   - **`apps/voice`:**
     - `POST /calls`, with the pre-dial checks;
     - the text-line transport;
     - the report to the core;
     - `voice.calls` and `voice.call_answers`.
2. **Code owns the script order.**
   1. The disclosure: fixed text, played in full.
   2. Consent.
   3. The company: an open question. The agent never says the vendor's name before the callee has named the company, and the guard blocks it (answers Q22).
   4. The department.
   5. Name and role.
   6. Whether they asked for the change.
   7. The last four digits, spoken or keyed.
   8. A fixed close that reveals nothing.
3. **How a call ends:**
   - **Consent "no":** a fixed goodbye. Report `answers_recorded` with `recording_consent: "no"`, and decide sends it to review (NR02) (answers Q13). `declined` is used only when the callee won't talk before answering consent.
   - **Dropped mid-call:** `failed`, with the answers so far and `retry_at` five minutes later. A "no" on it still counts (spec 0005, D2). Each redial is a new `POST /calls` and a new `call_id` (answers Q12).
   - **`schedule_callback(window)`:** a fixed closing line, then `callback_requested` with `retry_at` at the start of the window.
     - If the callee names no time, the agent asks once more.
     - If there's still no time: the next business day at 10:00 vendor local time (answers Q24). This replaces the earlier proposal of `declined`, because "call me later" isn't a refusal.
   - **Voicemail:** one fixed message, then `voicemail`.
   - **No answer or busy:** that result.
   - **Phone menu:** `machine`, with `answered_by: "machine"`.
   - **A second tool violation, an agent error, the turn cap or repeated silence:** `failed`. The checks say why, and the hold stays (R7).
4. **New speaker.** When someone else comes on the line, or a second name is given, the agent re-asks name and role and records the flag `speaker_changed` (answers Q14).
5. **Code checks grounding** (answers Q15):
   - Each answer cites a callee turn that comes after the agent asked that slot's question and before the next slot's question.
   - Text values and spoken digits must appear in that turn.
   - Keypad digits come only from keypad events, never from the model.
   - Any miss sets `answers_grounded: false`.
6. **Pressure.** Code flags `pressure_to_approve` from a fixed phrase list over the callee's turns: urgency, "approve it", "wire it today", claims of authority. The model may add the flag too, grounded like any answer; code's flag is the minimum (answers Q22).
7. **Telephony facts on the text line:**
   - `direction: "outbound"`;
   - `dialed_number_is_pick`: the number dialed compared with the pick read at dial time;
   - `answered_by`: from the line behavior.
8. **The number.** The voice service reads the case's pick from `ledger.number_selections` → `ledger.contact_points.e164` (the ledger README). With no row, it refuses with 409 `number_not_selected`.
9. **No digits in `voice.call_answers`.** `account_last4` is stored as `{digit_count}`, `dont_know` or `refused`. The hashed form lives only in the evidence chain, because the core holds the key (spec 0002, D7).

## Contract touched

All additive, in Sami's contracts PR:
- `call-answers.schema.json`: `speaker_changed` in `flag` (`decide()` already expects it).
- `internal.yaml`: the telephony fields after A5bhinav/rampHackathon#13, and the callback rule after A5bhinav/rampHackathon#18.

## Acceptance

- [ ] **The guard** blocks every class:
  - runs of three or more digits;
  - "verified", "approved" and "match";
  - amounts;
  - bank names;
  - the vendor's name before `company_named`.

  It allows a read-back of digits the callee just said.
- [ ] **Tools** reject an unknown tool, an extra field, a bad slot, a bad value and a missing `turn_id`. The first violation re-asks; the second ends the call `failed`.
- [ ] **Script:**
  - The disclosure is the first agent turn, word for word, on every call.
  - Consent "no" ends the call.
  - The order holds.
  - No agent turn speaks account digits or accepts a new number.
- [ ] **`POST /calls`:**
  - It refuses with each `PreDialRefusal` code; the allowlist guard has its own test.
  - A replayed `Idempotency-Key` returns 200 with the same `call_id`.
- [ ] **The report:**
  - It validates against `internal.yaml`, and the core accepts it.
  - A 409 while `call_started` isn't appended yet is retried.
  - If the core is unreachable, the case stays `calling` until the core's `call_timeout` (R7).
- [ ] **`scripts/dev/stack.py --smoke`** gives the same three outcomes with the real voice service in text mode in place of `ScriptedVoice`.
- [ ] **Fail closed:** an agent error, a timeout or a malformed tool call ends the call `failed`. It never becomes a recorded "yes".

## Tests (written first)

| Item | Test |
|---|---|
| 1 | `packages/policy/tests/test_guard.py` |
| 2 | `packages/policy/tests/test_tools.py` |
| 3 | `packages/policy/tests/test_step.py` |
| 4, 5 | `apps/voice/tests/test_calls.py` (the core and the database on a throwaway Postgres) |
| 6 | `scripts/dev/stack.py --smoke` |
| 7 | `apps/voice/tests/test_fail_closed.py` |

## Out of scope

- **Next spec:** the Twilio ConversationRelay adapter, `call_token`, status callbacks, the Claude agent's prompt and the model pin.
- **Lab spec:** the runner, the oracle and the personas.
- **Stretch:** `press_keys` through phone menus.

## Open questions

- **Q36 (Inesh):** are `ledger.number_selections(case_id, contact_point_id)` and `ledger.contact_points(id, e164)` the final names? Can those two tables land first, even empty? Until then, the voice tests create them in a fixture.
- **Q37 (team):** which model words the questions on live calls, Haiku 5.5 or Sonnet 5.5? Proposal: latency on the go/no-go call decides.
- **Q38 (Abhi):** `stack.py` is yours. Shall I swap `ScriptedVoice` for the voice service in text mode, or will you?
- **Q39 (team):** demo-smoke CI has no model key, so it keeps `ScriptedAgent`. OK?
- **Q40 (Sami):** limits. Proposal: 30 callee turns, three silences of 8 seconds each, 6 minutes per call.

## Proposed answers to questions in other specs

- **0002, D6 and D7:** confirmed. `internal.yaml` follows both (PR A5bhinav/rampHackathon#28).
- **0002, Q8:** ConversationRelay gives no per-turn speaker identity. The voice service supplies the declared name and `speaker_changed` (decision 4).
- **0006, Q21:** Twilio Lookup's line type (mobile, landline or VoIP) is a possible stretch fact. Its SIM-swap and call-forwarding checks have limited US coverage and need approval, so those are not for this hackathon (to check).
- **0008, Q26:** `evals/heldout/MANIFEST.json` holds the sealed SHA-256, the count and when it was sealed. The scorecard will copy the hash it ran against, in an optional field added with the lab runner.
- **0008, Q27:** your nine classes are adopted as written and listed in `evals/README.md`.

## Commands

```bash
cd packages/policy && uv run pytest
cd apps/voice && uv run pytest
uv run python scripts/dev/stack.py --smoke
uv run --with jsonschema --with rfc8785 --with pyyaml --with openapi-spec-validator python packages/contracts/scripts/validate.py
```
