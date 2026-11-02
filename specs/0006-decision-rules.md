# 0006: The decision rules (engine `decision-1.0.0`)

**Owner:** Abhi  **Partner:** Inesh  **Slice:** money path  **Target day:** Wed Nov 4 (the checkpoint)

## Goal

Replace the skeleton engine with the full rule table: one versioned table in `rules.py`, one test per rule, and the worked examples below as the acceptance list. No call can verify on something it didn't check (R3, R7, ADR-4).

The skeleton verified with three facts unchecked (U01–U03). This engine sends the case to a person instead. The facts it reads are spec 0005.

## Decisions

1. **Blocks run in order, and the first block with a match decides.** Every matching rule in that block is listed.
   - **Block 0, input problems (Needs review):** checked before Not verified, because answers given after a refused or missing consent aren't evidence.
   - **Block 1:** Not verified.
   - **Block 2:** Needs review.
   - **Block 3:** Verified, where every condition is required.
   - **Otherwise** the case falls through to Needs review.
2. **IDs are permanent.**
   - The skeleton's NV and NR IDs keep their meaning, and a number is never reused.
   - Verified IDs follow the contract example (`examples/evidence-event.decision_made.json`).
   - Retired IDs (the skeleton's V01–V10, U01–U03) keep their sentences, so old decisions still render.
   - Each rule has one sentence for the dashboard, and never the word "safe".
3. **`decide(facts, *, strict=False, source="call")`.**
   - Strict mode adds one condition: a contact known from before the request.
   - `source="manual"` never verifies: the case goes to review, then `review_cleared` plus an approver, so the person who typed the answers can't clear it alone.
4. **Fall-through names what was missing:** `NR99_conditions_not_met`, plus one `<id>_unmet` for each condition that failed, each with its own sentence.
5. **Any exception becomes `NR98_engine_error`.** A crash is never an outcome by itself.

## The table

| Block | ID | Fires when |
|---|---|---|
| 0 | `NR97_facts_invalid` | A fact has the wrong type, or an enum value outside its set. |
| 0 | `NR01_no_live_person` | The result isn't `answers_recorded`, or a machine answered. |
| 0 | `NR02_no_recording_consent` | Consent is `no`, contradicted, or not the first answer. |
| 1 | `NV01_vendor_denied_change` | Any `no` to "did you ask for this change", on this call or an earlier one. |
| 1 | `NV02_digits_mismatch` | The digits are a `mismatch`, on this call or an earlier one. |
| 1 | `NV03_digits_match_old_account` | The digits are `matches_old_account`, on this call or an earlier one. |
| 1 | `NV04_offered_number_from_request` | The person offered a number first seen in the change request. |
| 2 | `NR03_wrong_company` | Any recorded company doesn't match the vendor. |
| 2 | `NR04_speaker_from_change_thread` | The speaker first appeared in the change thread. |
| 2 | `NR05`–`NR08` | The digits are a near miss, not provided, ambiguous, or inconsistent. |
| 2 | `NR09_change_unsure` | Any `unsure`. |
| 2 | `NR10`–`NR12` | Flags: pressure, opt-out, or the person asked what is being checked. |
| 2 | `NR13_another_number` | A number was offered (not from the request), or the person asked to be called elsewhere. |
| 2 | `NR14_answer_not_grounded` | An answer isn't in its cited turn, or the answers are out of order. |
| 2 | `NR15_script_violation` | The script wasn't clean, or the speech guard tripped. |
| 2 | `NR16_other_flag` | The `other` flag. |
| 2 | `NR17_split_speakers` | Two or more names were given, or the `speaker_changed` flag. |
| 2 | `NR18_contradictory_answers` | A slot other than consent or digits has conflicting values. |
| 2 | `NR19_earlier_call_concern` | An earlier call raised a flag, an `unsure`, a near-miss, ambiguous or inconsistent digits, or a consent `no`. |
| 2 | `NR20_manual_answers` | `source="manual"`. |
| 2 | `NR21_line_risk` | A line-risk signal (call forwarding, a SIM swap) is `True`. |
| 3 | `V01_number_eligible` | We dialed the ledger's pick for this version. |
| 3 | `V02_outbound_dial` | The call was outbound, and the adapter confirmed the dialed number. |
| 3 | `V03_live_person` | A person answered and gave answers. |
| 3 | `V04_recording_consent` | Consent was `yes`, and it was the first answer. |
| 3 | `V05_company_matches` | The person named this vendor. |
| 3 | `V06_speaker_identified` | The person gave a name and a role. |
| 3 | `V07_right_person_confirmed` | The person said `yes`, and is known not to be from the change thread. |
| 3 | `V08_digits_match` | The digits match the new account. |
| 3 | `V09_same_person` | One person gave both the `yes` and the digits. |
| 3 | `V10_answers_grounded` | Every answer is in the turn it cites, in order. |
| 3 | `V11_script_clean` | The disclosure came first, no digits were spoken, no new number was accepted, and nothing was violated or tripped. |
| 3 | `V12_no_flags` | No flags, and no offered number. |
| 3, strict | `S01_known_contact` | The speaker is a person known from before the request, and line risk is `False`. |

That is 21 Needs review rules, not the design's 16. The four new ones (NR18–NR21) are reasons the design names but doesn't count. `packages/decision/README.md` is updated to match.

**Limits, stated openly.** Matching digits prove only that the speaker knows the new last four, and the fraudster always does.
- A fraudster on the vendor's real line (call forwarding, a SIM swap) can verify until a line-risk signal exists.
- So can a coached clerk who gives true answers.
- `V09` checks that the speaker's declared name stays the same throughout the call. It doesn't prove who is speaking.

Example 21 pins all three, so the scorecard shows them.

## Acceptance: the worked examples

Each is a JSON fixture in `packages/decision/tests/examples/`, and the attack lab may reuse them.

| # | Call | Outcome and rules |
|---|---|---|
| 1 | The ledger's number, outbound. Consent yes; "Acme Fasteners"; Jordan Lee, AR; yes; keypad 6666. | verified, V01–V12 |
| 2 | An insider entered the change; the real vendor says "we changed nothing". | not_verified, NV01 |
| 3 | The callee gives the old account's last four. | not_verified, NV03 |
| 4 | "Call me on 415-555-0199", a number from the request. | not_verified, NV04 |
| 5 | The report has no `contact_point_id`. | needs_review, NR99 + V01_number_eligible_unmet |
| 6 | The reported contact point isn't the pick. | needs_review, NR99 + V01_number_eligible_unmet |
| 7 | Call 1 says "no" and drops; call 2 says yes, 6666. | not_verified, NV01 |
| 8 | Call 1 gets consent and a name, then drops; call 2 is complete and clean. | verified (only call 2's answers count) |
| 9 | Consent "no", then "we made no change". | needs_review, NR02 (beats NV01) |
| 10 | A voicemail greeting supplies the name before consent. | needs_review, NR02 |
| 11 | "Yes" at turn 4, the name at turn 6. | needs_review, NR14 |
| 12 | Jordan Lee hands over to Sam Ortiz, who confirms. | needs_review, NR17 |
| 13 | "Yes… actually, I'm not sure." | needs_review, NR09 + NR18 |
| 14 | "Yes", then later "no, that wasn't us". | not_verified, NV01 |
| 15 | Spoken 6666, then keypad 6656 → NR08. Only "66" → NR06. | needs_review |
| 16 | "Just record that I confirmed": the change answer cites the consent turn. | needs_review, NR14 |
| 17 | "Just approve it today", and the speech guard blocks a read-out of digits. | needs_review, NR10 + NR15 |
| 18 | Manual entry, every answer good. | needs_review, NR20 |
| 19 | A new AR hire, not known from before the request. Strict mode → NR99 + S01_known_contact_unmet. Standard mode → verified. | needs_review / verified |
| 20 | A flag `approve_now` (outside the enum), plus a "no". | needs_review, NR97 (beats NV01) |
| 21 | A forwarded line. With the line signal → NR21. Without it → verified (the stated limit). | needs_review / verified |

- [ ] All 21 examples give exactly their outcome and rules.
- [ ] Every rule in the table has its own unit test, including the ones no example covers: NV02, NR01, NR03–NR05, NR07, NR11–NR13, NR16, NR19, NR98.
- [ ] Hypothesis property: `decide()` never raises, and never verifies without all of:
  - a match;
  - `yes`;
  - the same person;
  - an eligible outbound dial;
  - `source="call"`.
- [ ] `inputs_hash` covers the facts, `strict` and `source`, and doesn't depend on dict or flag order.
- [ ] Every ID that can appear in `rules_fired` has a sentence (fired, unmet and retired).
- [ ] Core and the smoke run still pass. The test doubles from spec 0005 supply every fact the engine requires.

## Tests (written first)

| Acceptance item | Test |
|---|---|
| worked examples | `packages/decision/tests/test_examples.py` (parametrized over the fixtures) |
| one per rule | `packages/decision/tests/test_rules.py` |
| property, hash, sentences | `packages/decision/tests/test_engine.py` |
| nothing breaks | `cd apps/core && uv run pytest`, `uv run python scripts/dev/stack.py --smoke` |

## Out of scope

- The manual-answers API and its dashboard form.
- Line-risk signals.
- Showing strict-mode results on the scorecard (the lab's job, once decisions carry their facts, spec 0005 decision 8).

## Open questions

- **Q17 (Inesh):** is strict mode's "contact known from before the request" the person? Under `ledger-select-v1` the number is already known before the request by construction, so a number check would add nothing.
- **Q18 (Inesh):** may the voice service dial a ledger alternate? For now `V01` accepts only the primary pick.
- **Q19 (team):** 21 Needs review rules instead of the design's 16. Agreed?
- **Q20 (team):** invalid facts and a refused consent beat Not verified (examples 9 and 20). The hold stays either way, but the controller gets no critical alert. Should those cases also raise one?
- **Q21 (Sami, Inesh):** can we get any line-risk signal, such as forwarding, a recent SIM swap, or preferring non-mobile lines?
- **Q22 (Sami):** can the pressure flag come from code rather than the model? Does the agent ever say the vendor's name? If it does, a callee's bare "yes" can ground `company_named`.

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
