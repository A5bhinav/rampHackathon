# 0065: The phone demo: live call and flow mode

**Owner:** Abhi (Sami for the call view)  **Slice:** call and adversary (presentation only)  **Target day:** Nov 11–12

Plan v3 section 4, numbered 0051 in the plan (renumbered; see spec 0063).

## Goal

The call is the moment the product is built around, so it gets its own screens. **Live call**
(`/d/{id}/cases/{caseId}/call`) matches the *Live call split view* board: a phone frame with the
number dialed and a running timer, the disclosure, the seven scripted questions filling with the
recorded answers, the script checks and the seven-station rail. **Flow mode**
(`/d/{id}/cases/{caseId}/flow`) matches the *Flow mode, projector* board: a dark 16:9 view where
seven columns light up as evidence events land. Both refresh themselves while code is working,
through the dashboard's own server; the browser never calls the core. Web only, today's API.

## Contract touched

None. The pages read `CaseDetail` (`calls[]`, `call_script`, `decision`, `timeline[]`, `chain`) and
`SelectionView` (`selection.e164` of the pick). Answers still arrive only when the call ends; live
turn-by-turn answers are phase 3 (spec 0076).

## Acceptance

Live call
- [ ] Shows, from the API only: the vendor, the number dialed (the selection's pick), a timer from the current call's `started_at` (frozen at `ended_at`), "attempt n", the disclosure (`call_script.disclosure`), each question's `call_script.questions` wording with its recorded answer, the script checks, and the seven-station rail.
- [ ] The states (plan section 4.1):
  - no call yet: "Choosing a number" while the hold and the number are pending, "Dialing {phone}" once the case is calling; the slots are empty;
  - on the call (`started_at`, no `result`): "On the call" with the timer; the slots say "Answers arrive when the call ends.";
  - ended with `answers_recorded`: the answers fill in turn order with a short fade (none under reduced motion); last four shows "{n} digits given" and the `last_four_result` word; the script-check chips appear;
  - not connected (`no_answer`, `voicemail`, `busy`, `failed`, `machine`, `declined`, `callback_requested`, `timed_out`): the core's `call.reason`, and "Retry scheduled" while the case is in `retry_wait`;
  - decided: the outcome badge, the core's `effect`, and a link to the case's `#decision-h`;
  - no call and nothing pending (review, blocked): the core's last timeline sentence.
- [ ] `.../call/state` is a GET route handler: server-side, with the signed-in person's token, it reads `core.case(caseId)` (and the selection only when asked) and returns only what the page draws. Signed out: 401. Unknown case: 404. The token never reaches the browser.
- [ ] A client component asks it every 2 seconds while the state is `open`, `held`, `calling`, `retry_wait` or `deciding`, and stops once it settles. It skips while the tab is hidden.

Flow mode
- [ ] A dark 16:9 view: the headline "AI collects. Code decides. A person releases.", the vendor and bill count, the chain badge, the dark ring band, and seven columns from `stations()`, each with its time, the core's `TimelineEntry.summary` for that station's latest event, and the short hash. It refreshes like the case page.

Entry points
- [ ] "Watch the call" on the case header and the Call card, once the current version has `number_resolved` (or a call).
- [ ] A "Present" menu in the AP workspace's top bar on demo datasets, with Live call and Flow mode for the newest case.
- [ ] The case header links to flow mode.

Never
- [ ] No digits for `account_last4`, ever; no sentence the dashboard wrote to explain a status (the state lines above are fixed labels; reasons and effects are the core's).

## Tests (written first)

| Acceptance item | Test |
|---|---|
| The page's view for each API shape (no call, dialing, on the call, answers, not connected, retry, decided, no call in review) | `apps/web/tests/live-call.test.ts` |
| No digits for `account_last4`, even if a value carried some | `apps/web/tests/live-call.test.ts` |
| The route handler: 401 signed out, 404 unknown case, selection only when asked, no token in the body | `apps/web/tests/live-call.test.ts` |
| Polling only in working states | `apps/web/tests/live-call.test.ts` |
| Flow mode columns: summary, time and hash per station | `apps/web/tests/live-call.test.ts` |
| Entry points appear only when they should | `apps/web/tests/live-call.test.ts` |

## Out of scope

Live answers turn by turn (phase 3, spec 0076). The guided demo's step 5 pointing at the live call
(0066).

## Open questions

1. "attempt n of 3": the API sends the attempt number but not the maximum, so the page shows "attempt n". Add the cap to `CallAttempt` (phase 2)?
2. The plan says polling stops "once the case leaves `calling`, `retry_wait` and `deciding`". It also polls in `open` and `held` here, so a page opened before dialing sees the call start.
3. The phone frame's "question being asked": with answers only at the end of the call, the frame shows the disclosure while the call is on and the last question answered once it ends.

## Commands

```bash
cd apps/web && pnpm check:api && pnpm typecheck && pnpm test && pnpm build
python3 scripts/ci/boundaries.py
```
