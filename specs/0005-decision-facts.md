# 0005: What the decision engine reads

**Owner:** Abhi  **Slice:** money path  **Target day:** Tue Nov 3 (ahead of the Nov 4 engine, spec 0006)

## Goal

`decide()` is only as good as its inputs. Today two of them fail open in core:

- **A dropped call loses a "no".** A `failed` call is stored with its answers and counts as "nobody reached" (`0004_skeleton_integration.sql:90`). The case is redialed, and the worker decides on the redial's answers alone (`worker.py:342-364`).
- **A missing contact point counts as eligible.** A report with no `contact_point_id` counts as dialing the ledger's pick (`worker.py:351`).

Three more facts (U01–U03) are never supplied at all. This spec fixes what `CaseFacts` holds and where each fact comes from. Unknown is always `None`, never a guess, so the Nov 4 engine (spec 0006) can fail closed on it (R3, R7).

## Decisions

1. **Facts are tri-state** (`True`, `False`, `None`). The engine tests `is True` or `==`, never truthiness, and validates types and enums itself. `strict` and `source` are hashed with the facts.
2. **Earlier calls count against, never for.** The design says a redial's answers are "never combined" with the dropped call's. So the worker passes `earlier_calls`: for each earlier `call_ended` of the case's current version, its `change_requested` values, `last_four_result`, flags and consent. A "no" or wrong digits on any of them still decides Not verified. Nothing from an earlier call can help verify.
3. **The call order is checked by code**, from each answer's `turn_id`:
   - `consent_first`: consent is the first answer, so a voicemail greeting can't supply a name before consent.
   - `answers_in_order`: the name is not after the change answer, and the change answer is not after the digits. The same turn counts as in order ("Jordan here, and yes, we asked").
   - The consent and change answers cite different turns, so one "yes" can't answer both.
4. **One person, by declared name.** `same_person_yes_and_digits` is:
   - `True` only if exactly one distinct normalized `speaker_name` is recorded, and it is recorded before the change answer;
   - `False` if two or more distinct names are recorded;
   - `None` otherwise.

   This checks that the declared identity stays consistent. It does not prove who someone is (spec 0006 lists this as a limit).
5. **Change context comes from the ledger through a port.** A new core port, `ChangeContext`, works like `NumberResolver` in `ports.py`. It answers three questions:
   - `speaker_in_change_thread`
   - `offered_number_from_request`
   - `speaker_known_before_request` (strict mode)

   It calls `dialback_ledger.change_context(case_id, dataset_id, vendor_id, cutoff, speaker_names, offered_numbers)`, which returns those three keys. Any value that isn't a boolean counts as unknown. It returns `None` until Inesh's ledger ships. Test doubles, labeled as such, stand in for it in core tests and `scripts/dev/stack.py`, and production code never contains one.
6. **Telephony facts come from the voice report, not from core's own bookkeeping:**
   - `dialed_out` (Twilio direction)
   - `dialed_number_is_pick` (the adapter's own check, design step 2)
   - `answered_by` (human or machine)

   Core's `current_call_id` check proves only that this is the call core asked for. It doesn't prove what was dialed.
7. **Repeated answers keep every value.** Today a slot answered twice with different values becomes `None`, which loses a "no". Instead, `recording_consent`, `company_named`, `change_requested` and `speaker_role` become tuples, in the order they were recorded. The engine decides what a contradiction means (spec 0006). Every company value must match, not just the first one recorded.
8. **The facts go into the evidence.** `decision_made` gains an optional `facts` object, so anyone can re-run a decision and check `inputs_hash`. It never contains digits; only the comparison result is stored (ADR-8).

## Contract touched

All proposals are optional fields, in their owners' own PRs; from Nov 2, contracts only grow:
- `internal.yaml` (Sami): `direction`, `dialed_number_is_pick`, `answered_by`, and `contact_point_id` made required.
- `call-answers.schema.json` (Sami): a `speaker_changed` flag.
- `evidence-event.schema.json` (Abhi): `DecisionMadeBody.facts`.

Until each lands, the fact it carries is `None`.

## Acceptance

- [ ] A report without `contact_point_id`, or with one that isn't the current version's pick, gives `dialed_eligible_number = False`.
- [ ] A dropped call that said "no", followed by a redial that says "yes", reaches `decide()` with the "no" in `earlier_calls`.
- [ ] `consent_first`, `answers_in_order` and `same_person_yes_and_digits` follow decisions 3 and 4. Each branch has a unit test.
- [ ] Every new fact is `None` when its source is missing: no ledger, an older voice report, or no names recorded.
- [ ] A slot answered twice keeps both values in order. `company_matches` is `False` if any recorded company value doesn't match.
- [ ] Core tests and the smoke run still pass. The doubles supply the new facts, and the skeleton engine is unchanged.

## Tests (written first)

| Acceptance item | Test |
|---|---|
| contact point | `apps/core/tests/test_worker.py::test_a_report_without_the_picked_contact_point_is_not_eligible` |
| earlier calls | `apps/core/tests/test_worker.py::test_a_dropped_call_that_said_no_still_counts` |
| order and same person | `packages/decision/tests/test_facts.py` (one test per branch) |
| unknown is None | `packages/decision/tests/test_facts.py::test_missing_sources_are_none` |
| company | `apps/core/tests/test_worker.py::test_every_company_answer_must_match` |
| nothing breaks | `cd apps/core && uv run pytest`, `uv run python scripts/dev/stack.py --smoke` |

## Out of scope

The rule table (spec 0006); the manual-answers API; the ledger's change-context implementation (Inesh); the voice service's telephony fields (Sami).

## Open questions

- **Q12 (Sami):** a dropped call.
  - Which `result` does it report, and does it carry the answers so far?
  - Does each redial get a new `call_id`?
- **Q13 (Sami):** a "no" to recording consent.
  - Does it end the call, and with which `result`?
  - SQL counts `declined` as "nobody reached" and redials. The design sends declined consent to Needs review.
- **Q14 (Sami):** when a new person comes on the line, does the agent re-ask their name and record `speaker_changed`?
- **Q15 (Sami):** grounding.
  - Can it bind each answer to the callee turn that answers that slot's question?
  - Are keypad digits checked against the DTMF log?
- **Q16 (Inesh):** the change-context interface.
  - The people and numbers in the change thread, how they are normalized, and the rule for matching names.
  - Who fills `trade_names`.

## Commands

```bash
cd packages/decision && uv run pytest
```

```bash
cd apps/core && uv run pytest
```

```bash
uv run python scripts/dev/stack.py --smoke
```
