# 0039: A guided demo, the scorecard's honest placeholder, and clearer empty states

**Owner:** Abhi  **Slice:** money path (dashboard)  **Target day:** Nov 8  **Builds on:** 0033, 0038

## Goal

A judge should be able to walk spec 0011's beats without a presenter: risk list, a change held,
the number's lineage, the call, the decision, a second person releasing, the tamper, the results.
A toggle walks them through, each step opening the real page on the demo data with one caption.
Pages a judge might hit empty or mid-flight say what happens next. Nothing new is decided or
computed.

## Decisions

1. **The tour is a URL parameter** (`?tour=N`), so it needs no state: a small panel on every
   page shows step N of 8, its caption, Back, Next and End. Each step is a link to `/tour/N`,
   which finds the newest demo dataset and its Acme case (as `/demo/<ref>` does), and redirects
   to the real page with `?tour=N`. Signed out, it signs in first and comes back.
2. **Steps:** vendor risk; the case queue (a change held); Acme's number lineage; Acme's case
   (the call); Acme's decision; sign-off (R5 and the release); the evidence chain (the tamper);
   the corpus results. The tamper and the change are made by the team with `dialback-core demo
   …`; the captions say so rather than pretend the page does it.
3. **`/scorecard`** says the attack lab isn't built, so there is no scorecard, what it would
   show (from `evals/README.md`), and links to the corpus page. It renders a scorecard only when
   one exists; today none does, so the page has no numbers.
4. **Empty and mid-flight states:**
   - the queue lists released changes under "Released", so a change doesn't vanish after release;
   - a hold not yet confirmed says what Dialback is doing and what happens if Ramp won't confirm;
   - no decision yet says when code decides and that the hold stays on meanwhile.

## Contract touched

None.

## Acceptance

- [ ] `/tour/1` … `/tour/8` open the right page with the panel; Back, Next and End work; an
      unknown step is a 404; signed out, sign-in comes back to the step.
- [ ] Captions name what the team does (`demo change`, `demo tamper`) and claim nothing the page
      doesn't show.
- [ ] `/scorecard` shows no number and links to `/corpus`.
- [ ] A released change appears under "Released" on the queue.
- [ ] The hold and decision empty states say what happens next.

## Tests (written first)

| Acceptance item | Test |
|---|---|
| 1, 2 | `apps/web/tests/tour.test.ts` (steps, targets, the next link, `safeNext` for `/tour/N`) |
| 3–5 | Checked by eye on the local stack |

## Out of scope

Recording the video; dashboard buttons for the demo commands (spec 0011, decision 1).

## Open questions

None.

## Commands

```bash
cd apps/web && pnpm test && pnpm typecheck && pnpm check:api && pnpm build
```
