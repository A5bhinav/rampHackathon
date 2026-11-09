# 0031: Dashboard layout, a second pass

**Owner:** Abhi (continuing Inesh's local restyle)  **Slice:** money path (dashboard)  **Target day:** Nov 8

## Goal

PR #62 gave the dashboard one token system. Every page still reads the same way, though: a
stack of identical cards. This pass carries Inesh's unpushed restyle (IBM Plex, a deep cheque
blue, a progress strip, a two-column case page) into the components and adds structure, so
each page has one lead answer and supporting facts that sit visibly behind it. No data,
wording from the core, or permission changes. Code still decides (R3) and the page still
only shows what the core sends.

Design plan:

- **Colour:** paper `#f3f5f7`, surface `#ffffff`, ink `#131c26`, cheque blue `#1d4f91` (the
  only accent), status colours `#156238` / `#7a5200` / `#ad2a1c` that only ever mean status.
- **Type:** IBM Plex Sans for text, IBM Plex Mono only for real figures (account numbers,
  phone numbers, hashes, rule ids). Sentence case everywhere, no all-caps labels.
- **Layout:** a page head on every page (title, lead line, page actions on the right), then a
  count strip where there is something to count, then the content. The case page is two
  columns: the progress strip, the decision and the console on the left, the change, the hold
  and the approvals as flatter panels on the right (not sticky: a tall rail would hide its
  own end). On one column the panels come before the timeline.
- **The one bold thing:** the case's progress strip (hold, call, decision, person, release)
  fills in stage by stage on load. Nothing else moves unless a person acts.

## Contract touched

None.

## Acceptance

- [ ] Every signed-in page opens with a page head; the queue and the risk list show a count strip.
- [ ] The case page shows five stages: done ones ticked, a skipped one dashed (for example no
      call when no number is provable), the current one ringed in its state's tone.
- [ ] A stage is ticked only when the facts say it finished: the hold when it is confirmed, the
      call when it ended with answers recorded, the decision when one exists. A stage reached
      but not finished shows "not completed", dashed.
- [ ] The progress strip reads only the case state, the hold, the decision and this version's
      timeline, and gates nothing. A failed hold is marked failed (a cross, bad tone, "not
      confirmed"); the current stage still follows the case state, so the strip never
      disagrees with the actions the page offers. An unknown state shows as waiting on a
      person, never as released.
- [ ] The decision card's top edge carries the outcome's colour; with no decision it is neutral.
- [ ] The queue's vendor cell carries a stripe when a payment is due soon or the hold is unconfirmed.
- [ ] The risk list is a table with a stripe by status; the lineage page lines every candidate
      up on one grid.
- [ ] Works at 390px wide and in dark mode; focus rings always show; nothing animates under
      reduced motion.

## Tests (written first)

| Acceptance item | Test |
|---|---|
| 2, 3 | `apps/web/tests/progress.test.ts` |
| 1, 4–7 | Checked by eye at 1440 and 390, light and dark, on the local stack |

## Out of scope

The call view and the scorecard (Sami's pages); any new data on a page.

## Open questions

None.

## Commands

```bash
cd apps/web && pnpm test && pnpm check:api && pnpm typecheck && pnpm build
python3 scripts/ci/boundaries.py
```
