# 0009: Queue and case-detail polish

**Owner:** Abhi  **Slice:** money path (dashboard pages)  **Target day:** Sat Nov 7 (feature freeze)

## Goal

An approver sees, at a glance, which cases need them and why, and on a case page reads the decision the way the engine made it (design, "Dashboard": the case queue shows "unfinished cases by state and age, and held bills near their due date"; the case detail shows "the decision with the rules that fired"). Dashboard only: no contract or API change.

## Decisions

1. **The queue is split by who has to act.**
   - "Waiting on a person" (`needs_review`, `awaiting_release`, `blocked`) comes first, then "In progress".
   - Each group is ordered by the soonest saved payment date, then by age.
   - A summary line counts each group, payments due within 3 days, and holds not confirmed.
2. **Times are read against the dataset's clock** (`sim_now` for lab datasets, the real clock otherwise). That covers "due in 2 days", "3 days overdue", how long ago a case opened, and the deadline countdown. A lab scenario's simulated time then reads the same way its deadline is counted.
3. **The decision is grouped by rule id**, the engine's permanent ids from spec 0006:
   - "Not verified because" (NV);
   - "Sent to a person because" (NR);
   - "Not shown on the call" (NR99 and `_unmet`);
   - "Shown on the call" (V, S).

   The sentences are the core's templates, unchanged, and each shows its rule id for auditors. A retired id without a sentence falls back to the plain list.
4. **The case page also shows:**
   - the deadline's countdown, or that it passed;
   - due-soon flags on the saved payment dates;
   - with no known changer, who gave the first approval, and that a second, different approver must release (ADR-10);
   - when there is no decision because the case went to a person before a call finished, that reason instead of "no decision yet".
5. **Alert banners name when, and link to their case.**

## Acceptance

- [x] The queue shows the two groups in that order, sorted by soonest payment, with the summary counts.
- [x] Due and deadline labels follow the dataset's clock.
- [x] The decision renders in rule groups, with ids, for verified, not-verified and needs-review outcomes.
- [x] The first approval of an unknown-changer case is shown, with the second-approver rule.
- [x] Banners link to their case.
- [x] `pnpm check:api`, `pnpm typecheck` and `pnpm build` pass, and the pages render against the dev stack (screenshots checked by hand).

## Tests

The dashboard has no test runner yet. The checks are the type checker and build, plus the pages rendered against `scripts/dev/stack.py --changes` with an approver signed in: the queue, a verified case, a not-verified case, and a case a person must review.

## Out of scope

Supabase sign-in for production (next PR), and Realtime updates.

## Commands

```bash
cd apps/web && pnpm check:api && pnpm typecheck && pnpm build
```
