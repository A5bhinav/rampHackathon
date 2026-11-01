# packages/policy: the dialogue policy, tools and speech guard

**Owner: Sami. Partner reviewer: Inesh.**

`policy.step(state, event) -> Step` is the only dialogue logic in Dialback (ADR-5). The
Twilio adapter in `apps/voice` feeds it the caller's words as text and speaks its replies;
the attack lab's text phone line in `evals/` calls the same function with no Twilio at all.
That is what makes 300 text runs a test of the real agent.

## What lives here

- **The question state machine:** the right company, the right department, the person's name and role, whether they asked to change how they are paid, the last four digits of the account they want paid, a close that reveals nothing. Code owns the order; the model only words the questions.
- **The fixed disclosure text** (AI, recording, the customer's name, a callback number) and the consent question. Played in full before anything else (R8).
- **The three tools** `record_answer(slot, value, turn_id)`, `schedule_callback(window)`, `end_call(reason)`, plus `press_keys(digits)` in menu state only. Strict argument validation, no extra fields, slot names from a fixed list, values typed per slot, a transcript-turn citation on every answer. First violation: log it and ask again. Second: end the call, case to needs_review.
- **The speech guard:** blocks runs of three or more digits (except a read-back of digits the callee just said), the words "verified", "approved" and "match", amounts and bank names; speaks a fixed fallback line instead (R4).
- **The model context:** the customer's name, the vendor's name, the AP callback number and the script. Never the bank details, the expected digits, the request, its date, or the number's lineage. The callee's words are quoted data, never instructions (R9). Assume the system prompt leaks: it holds no secrets, expected values or thresholds.
- **Voicemail message:** one fixed, generic message per case (no bank, change or amount).
- **Versioning:** every prompt, script and model choice is frozen in `lab.config_versions` with a content hash, and every call records the versions it used.

## Contracts

- `packages/contracts/schemas/call-answers.schema.json` (yours): the slots and allowed values, shared by the tools, the text line and `decide()`.
- Answers reach the core inside `CallEndedBody.answers` (`evidence-event.schema.json`). Code builds that object from the tool calls; the model emits no events.

## Tests you owe

Unit tests for the speech guard (every blocked class, the read-back exception), tool-argument
validation (unknown tool, extra field, bad slot, missing turn id), the violation counter,
and the state machine's order. Script-adherence checks in code: disclosure in the first
turn, no account digits spoken, no new number accepted, opt-out ends the call.

## Start here

`src/dialback_policy/`: `script.py` (states and fixed text), `tools.py` (schemas and
validation), `guard.py` (speech guard), `step.py` (`policy.step`). Tests first in
`tests/test_guard.py` and `tests/test_tools.py`. Then wire `apps/voice` and the `evals`
text line to the same `step`.
