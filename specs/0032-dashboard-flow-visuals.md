# 0032: Dashboard flow visuals

**Owner:** Abhi  **Slice:** money path (dashboard)  **Target day:** Nov 8  **Builds on:** 0031

## Goal

Someone who opens the dashboard for the first time (a clerk, an approver, a Ramp judge) should
see what Dialback does and where every change stands without reading a paragraph. Taking cues
from fintech dashboards (light, white surfaces, hairline structure, a few precise charts),
this adds visuals built only from data the core already sends: no amounts, nothing new on the
wire, nothing decided on the page (R3).

Design plan:

- **Colour:** light only. Page `#f6f8fb`, surfaces white, hairlines `#e4e9f0`, ink navy
  `#0b2343`, one action blue `#2a5bd7`; status colours only for status, always with a word.
- **Type:** IBM Plex Sans and Mono as in 0031; counts in proportional figures, columns tabular.
- **The rail (the one bold thing):** the money path drawn as a rail with five stations (hold,
  call, decision, sign-off, release), each with an icon and one plain line saying what
  happens there. The queue's rail shows how many changes sit at each station; the case's rail
  shows where this change is. The same picture on both pages teaches the product.
- **Charts, each with its words beside it:** held payments by due date on the queue (one lane
  per change, a today line, the overdue side shaded); the vendor mix on the risk list (one
  segmented bar with a legend); on the case, the payment's path from Ramp to the new account,
  held in between.

## Contract touched

None.

## Acceptance

- [ ] The queue opens with the rail: five stations, each with an icon, a one-line meaning and
      the number of open changes there; sign-off splits into review, approval and blocked.
- [ ] The queue shows held payments by due date: a lane per change whose hold is in place
      (confirmed or releasing; a failed or still-placing hold is left out and counted in the
      caption), soonest first, at most 12, with its next saved payment date, a today line,
      overdue shaded, the change's deadline marked; dates outside the window pin to its edge with
      an arrow. Every mark has a text equivalent (the table below it). "Bills on hold" counts only
      holds in place.
- [ ] The case page's progress is the same rail, with this change's status at each station
      (from spec 0031's progress rules, unchanged).
- [ ] The case's change panel draws the payment path: Ramp, the bills, the hold, the new
      account, and the old account it replaces. The hold says what its status says: held
      (confirmed, releasing), placing, lifted (released); only failed, or an unknown status, is
      "not confirmed" in red. Only routing numbers and last four digits (ADR-8).
- [ ] The risk list leads with one segmented bar of vendors by status, with a legend; a near
      miss row marks the digits where the number on file differs from the provable one, only
      when the two are a near miss of each other (one digit off, or two adjacent swapped).
- [ ] Light only: the page no longer switches to dark with the system setting.
- [ ] Works at 390px; focus rings show; nothing moves under reduced motion.

## Tests (written first)

| Acceptance item | Test |
|---|---|
| 1 | `apps/web/tests/flow.test.ts` (station counts, the split) |
| 2 | `apps/web/tests/flow.test.ts` (which changes are drawn, the day window, positions) |
| 4 | `apps/web/tests/flow.test.ts` (the hold gate, per status) |
| 5 | `apps/web/tests/risk.test.ts` (digit marks, shares) |
| 3, 4, 6, 7 | Checked by eye at 1440 and 390 on the local stack with the demo ledger loaded |

## Out of scope

Amounts (the API sends none), the call view and the scorecard (Sami's), a dark theme.

## Open questions

- Should the dashboard keep a dark theme at all? This spec drops it; the brief asked for light.

## Commands

```bash
cd apps/web && pnpm test && pnpm check:api && pnpm typecheck && pnpm build
python3 scripts/ci/boundaries.py
```
