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

### Rules: blocks evaluated in order, first match decides (spec 0006)

First, **an earlier call that had consent and said "no" or gave wrong digits** decides Not verified; this call's own problems can't hide it.

0. **Input problems (Needs review):** facts that fail validation (NR97), no live person (NR01), or consent refused, contradicted or not first (NR02). These come before Not verified, because answers after a refused consent aren't evidence.
1. **Not verified (4 rules):** the vendor says they asked for no change; the digits don't match; the digits match the old account (all three on this call or an earlier one); the callee offers a number from the request. The hold stays and the controller is alerted.
2. **Needs review (21 rules, NR01–NR21, counting the two in block 0):** the skeleton's 17 (the design's 16 plus split speakers) and four reasons the design names without counting: contradictory answers, an earlier call's concern, manual answers and line risk (NR18–NR21). Examples: the wrong company; a speaker from the change thread; near-miss or missing digits; pressure; an opt-out; an answer the transcript doesn't support.
3. **Verified (12 conditions, all required):** V01–V12, from "we dialed the ledger's pick" and "outbound to that number", through "the right person confirmed and gave matching digits", to "no flags and a clean script".
4. **Anything else is Needs review**, naming each unmet condition (`NR99_conditions_not_met` plus `<id>_unmet`); any error is `NR98_engine_error` (R7).

The rules are a versioned table in code (`rules.py`; `engine_version` bumps with any
change), and each one has a test, plus 23 worked examples (`tests/examples/`). **Strict mode**
adds `S01_known_contact` (a person known from before the request), so the scorecard can show
what it costs in extra reviews.

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

## What exists (`decision-1.0.0`)

- `rules.py`: the table above. Every rule carries the sentence the dashboard shows; sentences of retired ids (the skeleton's) are kept in `engine.RETIRED`, so old decisions still render.
- `decide(facts, *, strict=False, source="call")`: pure and total. It validates every fact first (NR97), then runs the blocks. `source="manual"` never verifies (NR20). `inputs_hash` is SHA-256 over the RFC 8785 form of the facts, the mode and the source.
- `CaseFacts` per spec 0005 (`facts.py`):
  - a repeated answer keeps every value, in order;
  - the call order and the single declared speaker are computed from the answers' turns;
  - earlier calls of the generation count against a change, never for it;
  - telephony and change-context facts are `None` until their slices supply them, and the engine treats `None` as not shown.
- `last_four.compare_last_four()`: the full comparison, in the designed order. The core runs it when a call report arrives, so the digits never leave memory (spec 0002, D7).
- Tests: one per rule and per clause (`test_rules.py`), the worked examples (`test_examples.py`), and a Hypothesis property that `decide()` is total and never verifies without a match, a "yes", one person and an eligible outbound dial (`test_engine.py`).

```bash
cd packages/decision && uv run pytest
```
