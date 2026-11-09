# 0036: The call and the decision, legible on the case page

**Owner:** Abhi  **Slice:** money path (dashboard)  **Target day:** Nov 8  **Builds on:** 0035

## Goal

A judge opening a case should see the call as a conversation and the decision as a checklist,
not a list of rule ids. The page renders what spec 0035 sends and decides nothing (R3): every
status word and sentence comes from the core.

## Decisions

1. **The decision card** leads with the outcome and the core's `effect` sentence ("the hold
   stays on; an approver must review"), then the rules that fired (as today), then the full
   Verified checklist: each condition with its sentence, its id and a status word (passed, not
   met, not checked), with the core's `checklist_note` above it. Each row has an anchor
   (`#rule-<id>`). An older core without the checklist shows the card as today.
2. **The call** is a new card per attempt, oldest first:
   - a header with the attempt, its result as a word, when, who answered (person or machine),
     and the provider;
   - the conversation: the agent's fixed disclosure word for word, then each scripted question
     (labelled "scripted wording", because the agent words it itself) and the callee's recorded
     answer, with the turn it cites and how it was captured;
   - for the last four digits, only "4 digits given" or "refused" / "didn't know": the page never
     has digits to show (ADR-8);
   - under each answer, a chip per rule it fed that this decision reports on (its Verified
     condition, and any rule that fired), linking to that rule's row, with the core's status word;
   - the script checks: disclosure first, every answer in its cited turn, the agent never said
     account digits, no new number accepted, policy violations and speech-guard trips, each as a
     word (a count of 0 reads "none");
   - the core's note that no transcript is kept.
   A call that didn't connect shows its result sentence and no conversation. No call shows why
   (the timeline's last step) instead of an empty card.

## Contract touched

None (0035 added the fields).

## Acceptance

- [ ] Verified shows twelve conditions passed; Not verified shows them "not checked" with the
      core's note; the not-met path marks exactly the conditions the core says.
- [ ] Every answer shows its slot, value, turn and capture method; account_last4 shows a count or
      "refused" / "didn't know" and nothing else.
- [ ] Each answer's rule chips link to rows that exist on the page and carry the core's status.
- [ ] The script checks read as words; a failed check is marked bad with a word, never colour alone.
- [ ] Without `calls` or `checklist` (an older core) the page renders as before.
- [ ] Works at 390px.

## Tests (written first)

| Acceptance item | Test |
|---|---|
| 2, 3, 4 | `apps/web/tests/call.test.ts` (answer text, chips, conversation order, checks) |
| 1, 5, 6 | Checked by eye at 1440 and 390 on the local stack (Acme verified, Brightline refused, Cobalt denied) |

## Out of scope

A transcript (none is stored; see 0035's open question); the recording.

## Open questions

None.

## Commands

```bash
cd apps/web && pnpm test && pnpm check:api && pnpm typecheck && pnpm build
python3 scripts/ci/boundaries.py
```
