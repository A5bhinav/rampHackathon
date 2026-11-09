# 0035: The call, the decision checklist and the evidence log, on the API

**Owner:** Abhi  **Slice:** money path (core API)  **Target day:** Nov 8  **Feeds:** 0036 (call and decision views), 0037 (evidence page)

## Goal

The dashboard can't show the most convincing parts of a case because the API doesn't send
them: `GET /cases/{id}` has no call data (the answers and script checks sit in the
`call_ended` evidence event), the decision is a list of rule ids with no checklist, and there
is no way to see the evidence chain row by row. This spec adds them, read-only, from what the
system already recorded. Nothing is re-decided (R3): the checklist is read from the rules the
engine recorded, and the per-row chain checks are Postgres's own hash functions.

## Decisions

1. **Calls come from the evidence chain**, the record every environment has: each
   `call_started` opens an attempt, its `call_ended` (or `call_timeout`) closes it.
   `voice.calls` adds the provider when that row exists. How a call ended is a templated sentence
   per result; the voice service's reason codes are for logs, not people.
   - **Answers** are the stored `call_ended` answers. `account_last4` is sent as a digit count
     or `dont_know` / `refused`: the keyed hash is never sent, and there are no digits to send.
   - **Feeds:** each answer names the rules that read it, from tables in the decision package
     (`rules.SLOT_RULES`; `EARLIER_SLOT_RULES` for an earlier attempt, whose answers count only
     against the change), so the page links answers to rules without knowing the rules.
   - **An offered number is not shown** ("A phone number (not shown)"): it could be anyone's.
2. **No transcript is stored** anywhere (`voice.calls.transcript_ref` is a key the voice
   service doesn't fill). Storing redacted turns is the voice slice's call and would need its
   own spec; this one sends the answers with the turn each cites, and a `call_script.note` that
   says so. `call_script` is the call policy's fixed text (`dialback_policy.script`), with the
   customer's name and the AP callback number left as labels, so no phone number is sent.
3. **The checklist is read, not re-run.** For a decision by the current engine version:
   Verified means every condition passed; `NR99_conditions_not_met` means each `<id>_unmet`
   recorded is not met and the rest passed; any other outcome means an earlier block decided
   and every condition is "not checked", with a sentence naming why. A decision recorded by an
   older engine version gets "not checked" throughout. `fired` groups each recorded rule by its
   block. `effect` says what the outcome does to the hold, per outcome.
4. **`GET /datasets/{id}/evidence`**: the live `audit.verify_chain`, the verify job's last run
   (`app.job_runs`), the row count, and up to `limit` rows newest first, each with its hashes
   and two checks computed in SQL with the chain's own functions: `content_ok` (the stored body
   still hashes to `content_hash`) and `link_ok` (it links to the previous row).

## Contract touched

`packages/contracts/openapi/dialback.yaml`, its own PR: optional `calls` and `call_script` on
`CaseDetail`; optional `fired`, `checklist`, `checklist_note`, `effect` on `DecisionInfo`; the
new `getEvidenceLog` operation. Additive only, so the deployed dashboard keeps working.

## Acceptance

- [ ] A case whose call recorded answers returns one attempt with its result, answers, turn ids,
      capture methods, checks and the rules each answer feeds; `account_last4` carries a digit
      count and no other trace of the digits.
- [ ] A refused or timed-out call, and a case with no call, return the right attempts (or none).
- [ ] Verified lists V01–V12 passed; Not verified lists them not checked with the reason; the
      not-met path marks exactly the recorded unmet conditions.
- [ ] The evidence log names a hand-edited row (`content_ok` false) and the chain's first bad seq.
- [ ] Every response matches the OpenAPI document (`tests/contract.py`).
- [ ] An unknown dataset is a 404; the API never sends a hash of the digits.

## Tests (written first)

| Acceptance item | Test |
|---|---|
| 1, 2, 5, 6 | `apps/core/tests/test_api.py::test_the_case_shows_its_calls_answers_and_checks`, `::test_a_refused_call_has_no_attempt` |
| 3 | `apps/core/tests/test_views.py` (checklist, per outcome; unit) |
| 4, 5, 6 | `apps/core/tests/test_api.py::test_the_evidence_log_points_at_a_hand_edited_row` |

## Out of scope

The dashboard pages (0036, 0037); storing transcripts; the lineage's per-sighting timeline.

## Open questions

- Should the voice service store a redacted transcript (digits masked before writing)? It is
  Sami's slice and a privacy decision; until then the call view shows answers only.

## Commands

```bash
uv run --with jsonschema --with rfc8785 --with pyyaml --with openapi-spec-validator python packages/contracts/scripts/validate.py
cd apps/web && pnpm check:api
cd apps/core && uv run pytest
```
