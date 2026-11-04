# 0009: Queue and case-detail polish

**Owner:** Abhi  **Slice:** money path (dashboard pages)  **Target day:** Sat Nov 7 (feature freeze)

## Goal

An approver sees, at a glance, which cases need them and why, and on a case page reads the decision the way the engine made it (design, "Dashboard": the case queue shows "unfinished cases by state and age, and held bills near their due date"; the case detail shows "the decision with the rules that fired"). Dashboard only: no contract or API change.

## Decisions

1. **The queue is split by who has to act.**
   - "Waiting on a person" (`needs_review`, `awaiting_release`, `blocked`) comes first, then "In progress".
   - Each group is ordered by the soonest saved payment date, then by age.
   - A summary line counts each group, payments overdue or due within 3 days, and holds not confirmed.
2. **Times are read against the dataset's clock** (`sim_now` for lab datasets, the real clock otherwise). That covers "due in 2 days", "3 days overdue", how long ago a case opened, and the deadline countdown. A lab scenario's simulated time then reads the same way its deadline is counted.
3. **The decision is grouped by rule id**, the engine's permanent ids from spec 0006:
   - "Not verified because" (NV);
   - "Sent to a person because" (NR);
   - "Conditions not met" (NR99 and `_unmet`);
   - "Not checked by this engine version" (U, which today's skeleton engine adds to every verified decision), in warning tone;
   - "Conditions met" (V, S);
   - any other id under "Recorded by an earlier engine".

   The titles say what a rule is, not where its fact came from: V01 is a Trust Ledger fact, and V08 and V09 are checks run after the call. The sentences are the core's templates, unchanged, and each shows its rule id for auditors. If a sentence is missing, the page shows the plain list of sentences and every rule id that fired.
4. **The case page also shows:**
   - the deadline's countdown, or that it passed, except once a release is under way (the queue follows the same rule);
   - due-soon flags on the saved payment dates;
   - with no known author of the change, who gave the first approval, and that a second, different approver must release (ADR-10);
   - when a person has the case and code made no decision for this version, the last timeline entry's summary. The core writes that sentence from its template, so the page never guesses the reason: a hold that failed, no provable number, calls nobody answered, or a deadline.

   A time that should be in the past but is after the dataset's clock shows nothing, rather than "in 300 days". That happens when a lab clock is set back.
5. **Alert banners name when, and link to their case.**

## Acceptance

- [x] The queue shows the two groups in that order, sorted by soonest payment, with the summary counts.
- [x] Due and deadline labels follow the dataset's clock.
- [x] The decision renders in rule groups, with ids, for verified, not-verified and needs-review outcomes.
- [x] The first approval of an unknown-changer case is shown, with the second-approver rule.
- [x] Banners link to their case.
- [x] `pnpm check:api`, `pnpm typecheck` and `pnpm build` pass, and the pages render against the dev stack (screenshots checked by hand).

## Tests

- **`apps/web/tests/lib.test.ts`** runs on Node's own test runner (`pnpm test`, which is in the ts CI job). It covers:
  - `dueLabel` across UTC days, overdue and the 3-day edge;
  - `relative`, and `ago` with a clock set back;
  - `groupRules` for every group, the U ids included, with sentences passed through unchanged and the fallback when a sentence is missing.
- **The pages** are checked by the type checker and build, and rendered by hand against `scripts/dev/stack.py --changes` with an approver signed in: the queue, a verified case, a not-verified case, and a case a person must review.

## Open questions

- **Q30 (Inesh, Abhi):** spec 0006 retires U01–U03 in `decision-1.0.0`. Once it ships, U ids appear only on old decisions, still under "Not checked by this engine version". Should they move to "Recorded by an earlier engine" then? This spec keeps them in warning tone, because the facts were never checked.

## Out of scope

Supabase sign-in for production (next PR), and Realtime updates.

## Commands

```bash
cd apps/web && pnpm check:api && pnpm typecheck && pnpm test && pnpm build
```
