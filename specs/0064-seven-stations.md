# 0064: Seven stations

**Owner:** Abhi  **Slice:** money path (presentation only)  **Target day:** Nov 11

Plan v3 section 6, numbered 0050 in the plan (renumbered; see spec 0063). Stacked on 0063.

## Goal

Every bank change is drawn as the same seven stations (Detected, Held, Number chosen, Called,
Decided, Approved, Released), read from the evidence events of the case's current version. One
function feeds the case stepper, the queue rows' mini rails and the Inbox's station counts; the
live call, flow mode, guided demo and System map (0065–0067) will use it too. This replaces the
five-stage model of spec 0031 (`lib/progress.ts`), which is removed with its tests. The case page
refreshes itself while code is still working.

## Contract touched

None. The case page reads `CaseDetail.timeline[]`; the lists read `GET /api/datasets/{id}/evidence`
rows (`case_id`, `type`, `seq`), grouped by case.

## Acceptance

- [ ] `lib/stations.ts`: `stations(events, state, outcome)` returns seven stations, each `done`, `current`, `todo` or `branched`, with a tone, an optional marker label and the time of its event. Only the current version's events count: those from the case's latest `change_detected` on.
- [ ] The station table (plan section 6):

  | Station | Done when the version has | Current while | Branch marker |
  |---|---|---|---|
  | Detected | `change_detected` (always once a case exists) | — | none |
  | Held | `hold_confirmed` | `open` | `hold_failed`: "Hold not confirmed" (red) |
  | Number chosen | `number_resolved` | `held` | `no_provable_number`: "No provable number" (amber) |
  | Called | `call_ended` that moved to `deciding` | `calling`, `retry_wait` | `call_timeout`, or `call_ended` to `needs_review` (tries used up): "No person reached" (amber) |
  | Decided | `decision_made` | `deciding` | outcome `not_verified`: "Not verified" (red); `needs_review`: "Needs review" (amber) |
  | Approved | `release_approved` | `awaiting_release`, `needs_review` after a decision | `release_co_approved`: "1 of 2" (amber, still current); `release_rejected` or `review_blocked`: "Rejected" (red) |
  | Released | `hold_release_confirmed` | `releasing` | none |

  A `deadline_reached` or `command_failed` puts an amber marker ("Deadline reached", "A step could not finish") on the station that was current.
- [ ] `stationsFromEvidence(rows, case)` does the same from evidence rows, which carry no `to_state`: a call counts as done when a `decision_made` follows it, and a call ending with the case in `needs_review` and no decision is "No person reached". With no rows for a case (the log keeps the newest 500), it falls back to the case's state alone.
- [ ] The case stepper, the queue rows' mini rails (Inbox and Cases) and the Inbox station counts use seven stations. `lib/progress.ts`, `tests/progress.test.ts` and the five-station code in `lib/flow.ts` are removed once nothing imports them.
- [ ] The case page refreshes every 2 seconds while the state is `open`, `held`, `calling`, `retry_wait`, `deciding` or `releasing`, and stops when it settles (and while the tab is hidden). No motion is added; nothing moves under reduced motion.
- [ ] Failure paths: an unknown state never shows as released or approved; events from an earlier version never mark a station done; a failed evidence fetch on the lists falls back to each case's state.

## Tests (written first)

| Acceptance item | Test |
|---|---|
| Every row of the station table, the happy path at each state | `apps/web/tests/stations.test.ts` |
| Each branch: `hold_failed`, `no_provable_number`, tries used up, `call_timeout`, `not_verified`, `needs_review`, `release_co_approved`, `release_rejected`, `review_blocked`, deadline and command failures | `apps/web/tests/stations.test.ts` |
| A second change voids the earlier version | `apps/web/tests/stations.test.ts` |
| Evidence rows grouped by case, ordered by `seq`, and the state-only fallback | `apps/web/tests/stations.test.ts` |
| Station counts for the Inbox | `apps/web/tests/stations.test.ts` |
| The refresh is on only for working states | `apps/web/tests/stations.test.ts` |

## Out of scope

The live call and flow mode (0065), the guided demo bar and folding cards (0066), the System map (0067).

## Open questions

1. The plan's table says Detected is "current while `open`" and Held "current while `held`, before a number", while Number chosen is also "current while `held`, after the hold". Taken here: the current station is the first one not yet done, so `open` (placing the hold) shows Held as current and `held` shows Number chosen as current. Confirm.
2. Called "tries used up" can't be told apart from other `call_ended` → `needs_review` moves in the evidence rows (no `to_state`); the lists infer it from the case's state. The case page reads `to_state` from the timeline and is exact.

## Commands

```bash
cd apps/web && pnpm check:api && pnpm typecheck && pnpm test && pnpm build
python3 scripts/ci/boundaries.py
```
