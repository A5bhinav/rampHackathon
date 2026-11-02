# packages/decision: the decision engine

**Owner: Abhi. Partner reviewer: Inesh.** If the money path is behind at the Nov 4
checkpoint, this package moves to Inesh, who already owns the other rule engine.

## Job

Turn a finished call into Verified, Not verified or Needs review, the same way every time.

## Interface

`decide(facts: CaseFacts) -> Decision` is a pure function: no database, no network, no
model. A `Decision` holds the outcome, the IDs of the rules that fired, `engine_version`
and a SHA-256 of the inputs. The core worker stores it through
`apply_case_event(case, 'decision_made', system, key, DecisionMadeBody)`; the body schema
is in `packages/contracts/schemas/evidence-event.schema.json`.

### Inputs (`CaseFacts`), from three sources; the AI fills only the second

1. **Telephony, from Twilio:** the number dialed, the call's direction, start and end times, whether a person or a machine answered.
2. **Recorded answers, from the agent's `record_answer` tool:** who answered, recording consent, the company they named, their name and role, whether they asked for the change, the four digits they said. Shape: `call-answers.schema.json` (Sami).
3. **Computed by code after the call:** whether the company matches the vendor's legal or trade names; whether the speaker first appeared in the change thread; whether one person gave both the "yes" and the digits; whether every recorded answer appears in the transcript turn it cites; whether the disclosure came first.

### The last-four check, tested in this order

`inconsistent` (two different answers), `ambiguous` (old and new accounts end in the same
four digits), `match`, `matches_old_account`, `near_miss` (one digit off, or two neighbors
swapped), `mismatch`. Fewer than four digits, "I don't know" or a refusal gives
`not_provided`. The spoken digits are compared in memory; the record keeps only the result
and a salted hash, never the digits.

### Rules: blocks evaluated in order, first match decides

1. **Not verified (4 rules):** the vendor says they asked for no change; the digits don't match; the digits match the old account; the callee offers a number from the request. The hold stays and the controller is alerted.
2. **Needs review (16 rules), for example:** no live person after three tries; recording consent declined; the wrong company; a speaker who first appeared in the change thread; near-miss or missing digits; pressure such as "just approve it"; an opt-out; an answer the transcript doesn't support.
3. **Verified (12 conditions, all required):** from "the number was eligible and we dialed out", through "the right person confirmed the change and gave matching digits", to "no flags and no script violations".
4. **Anything else is Needs review, including any error** (R7).

The rules are a versioned table in code (`rules.py`, `engine_version` bumps with any change),
and each one has a test built from the call protocol's worked examples. A **strict mode**,
which also requires a contact known from before the request, is built as well, so the
scorecard can show what it costs in extra reviews.

**People use the same engine.** When a reviewer phones the vendor themselves, they enter
the same answers in the dashboard and the same `decide()` runs on them with
`source: "manual"`. Clearing a case that the engine sent to review takes two people,
neither of them the changer: the reviewer and the approver.

## Rules that bite here

R3 (the AI collects, code decides), R7 (fail closed), ADR-4. No model, no network, no clock
inside this package; the caller passes everything in.

## Tests you owe

- One unit test per rule and per worked example from the call protocol (`tests/test_rules.py`).
- The last-four comparison, every branch, including the ambiguous and inconsistent cases.
- A Hypothesis property: for random facts, `decide()` never raises, and anything it cannot classify is `needs_review`.
- `inputs_hash` is stable across dict ordering (canonicalize with `rfc8785` before hashing).

## What exists (skeleton engine, `decision-0.1.1-skeleton`)

- `last_four.compare_last_four()`: the full comparison, in the designed order, with property tests. The core runs it when a call report arrives, so the digits never leave memory (spec 0002, D7).
- `decide()`: Not verified (4 rules), Needs review (17 rules), Verified (10 conditions, all required), anything else Needs review, any error Needs review. Each rule carries the templated sentence the dashboard shows.
- `CaseFacts` per spec 0005 (`facts.py`):
  - A repeated answer keeps every value, in order.
  - The call order (`consent_first`, `answers_in_order`) and the single declared speaker (`same_person_yes_and_digits`) are computed from the answers' turns.
  - Earlier calls of the same generation are summarized as `EarlierCall`; they can count against a change, never for one. The skeleton's NV01–NV03 already act on them, so a dropped call's "no" still blocks the case.
  - Telephony and change-context facts are `None` until their slices supply them.
  - The skeleton engine reads a repeated answer as before: its value if every recording agrees, else unknown.
- Still unknown, and recorded as "not checked by this engine version": `U01` (the speaker first appeared in the change thread) and `U03` (an offered number came from the request), until the ledger's `change_context` exists. `U02` (one person) is now computed. The Nov 4 engine (spec 0006) fails closed on all of them.
- `inputs_hash` is SHA-256 over the RFC 8785 form of the facts.

```bash
cd packages/decision && uv run pytest
```

## Start here (for the Nov 4 engine)

1. Write `specs/NNNN-decision-engine.md` with the rule table as the acceptance list.
2. `src/dialback_decision/facts.py` (`CaseFacts`, Pydantic, frozen), `last_four.py`, `rules.py` (the table), `engine.py` (`decide`).
3. Tests first, from the worked examples; then the implementation; `uv run --package dialback-decision pytest`.
