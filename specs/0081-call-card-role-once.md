# 0081: The call card shows the role once, under its own question

**Owner:** Sami  **Slice:** money path (dashboard)  **Target day:** Nov 10, 2026  **Builds on:** 0036
(the call as a conversation), 0034 D2 (name and role are two questions)

## Goal

Since spec 0034 D2 (Nov 8) the call asks "May I have your name?" and then "And what's your role
there?", and the core's `call_script()` lists both questions. The dashboard's `conversation()`
(`apps/web/src/lib/call.ts`) was written for the older script, where one question asked for both
and the name completed it, so it still files the role under the name question. With today's script
the case page's call card shows the role twice: once under the name question and again under its
own. In a demo that reads as if the call asked for both at once. This spec makes each question list
only its own slot's answers whenever the script asks the role on its own, and keeps the fold for an
older script that has no role question. Display only (R3): nothing is compared or decided.

## Contract touched

None. No change to the OpenAPI contract, the core, the call policy or the voice agent.

## Acceptance

- [ ] With a script that has separate `speaker_name` and `speaker_role` questions, `conversation()`
      lists the name answer only under the name question and the role answer only under the role
      question. No answer appears under two questions.
- [ ] With an older script that has a `speaker_name` question and no `speaker_role` question, the
      role answer is still listed under the name question, as before.
- [ ] The phone demo (`apps/web/src/lib/live-call.ts`) gets the same per-question answers from
      `conversation()` and no longer needs its own filter for the role; its tests pass unedited.
- [ ] Failure path: no answer is dropped. With no script (an older core) every answer is still
      listed as an extra, and an answer whose slot no question asks for (a flag) is still an extra.

## Tests (written first)

| Acceptance item | Test |
|---|---|
| 1 | `apps/web/tests/call.test.ts`: "a script that asks the role on its own lists each answer once, under its own question" (new) |
| 2 | `apps/web/tests/call.test.ts`: "the conversation follows the script; answers sit under their question, extras after" (unchanged, expects `speaker_name, [4, 5]`) |
| 3 | `apps/web/tests/live-call.test.ts`: "ended with answers: ..." and "a script that asks the role on its own keeps it out of the name's slot" (both unchanged) |
| 4 | `apps/web/tests/call.test.ts`: the new test's extras check, and "without a script every answer is still listed" (unchanged) |

## Out of scope

- The page's wording, labels and layout. `SLOT_LABEL` and the card markup do not change.
- Which script a past call heard. See the open question.

## Open questions

- `call_script()` returns today's script, not the script a given call used. A call recorded before
  Nov 8 (one combined question) will now show its role under "And what's your role there?", a
  question that call never asked. This was already true of every other wording change and is not
  introduced here; recording the script version per call would need a contract change. For Sami
  to decide whether that is worth a spec.

## Commands

```bash
cd apps/web && pnpm test && pnpm check:api && pnpm typecheck && pnpm build
```
