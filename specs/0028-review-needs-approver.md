# 0028: Only an approver clears or blocks a review

**Owner:** Abhi  **Slice:** money path (case machine)  **Target day:** Sun Nov 8

## Goal

A case in `needs_review` is a person's call (R7). Today any signed-in account can make it: the
guards on `review_cleared` and `review_blocked` check a written reason and that the person isn't
the changer, but no role. Since spec 0027 anyone can sign up, so anyone could clear a flagged case
(leaving one approver to release it) or block a legitimate payment. After this, both need the
approver role, the same role that releases. R5 is unchanged: the changer never clears.

## Plan

1. **Migration `app/0011_review_needs_approver.sql`:**
   - Redefine `app.eval_guard` in full, as 0008 did, with two changes:
     - `reason_given` becomes `approver_reason` (approver and a reason);
     - `reason_given_not_changer` becomes `approver_reason_not_changer` (approver, a reason, and not the changer).
   - The old keys are gone, so anything still naming them raises "unknown guard" and refuses (R7).
   - Update the two `app.case_transitions` rows to the new keys and descriptions.
   - `create or replace` keeps the existing grants (0004's note).
2. **Core `api.py`:**
   - `permissions().can_review` also needs the approver role, and the page says why when it doesn't.
   - `_refusal()` answers 403 "Reviewing needs the approver role" when a person without it is refused on a case in review, not the misleading 409.
3. **Redraw `docs/state-machine.md`** from the database (`scripts/db/test.sh` does it).

## Contract touched

None. `POST /api/cases/{case_id}/review` already documents 403 `forbidden`.

## Acceptance

- [ ] A signed-in account without the approver role (a clerk, an auditor, or no role at all) can neither clear nor block a review. The case stays `needs_review` and the hold stays.
- [ ] An approver who isn't the changer clears, and an approver blocks, exactly as before. The changer still can't clear (R5).
- [ ] The API answers a non-approver's review with 403 and a message naming the approver role. The case view's `can_review` is false for them, with that reason.
- [ ] The state-machine doc shows the new guards.

## Tests (written first)

| Acceptance item | Test |
|---|---|
| non-approvers refused, approvers unchanged | `supabase/tests/014_review_needs_approver.sql` |
| API answer and `can_review` | `apps/core/tests/test_api.py::test_only_an_approver_reviews` |
| doc | `scripts/db/test.sh` redraws it; the diff is in the PR |

## Stated limits

- **A reviewed case with an unknown changer needs three approvers.** The reviewer is now an
  approver, and neither release approval may come from the reviewer (ADR-10), so such a case
  needs the reviewer plus two others. Keep at least three approvers on the hosted project and
  in demo rehearsals that use `demo change --unknown`. It fails closed: the hold stays.
- **An approver who is the changer can still block** (unchanged; blocking keeps the hold, and
  the page hides it from them).

## Out of scope

- A separate reviewer role (approver does both; the reviewer of a case still can't also release it).
- Narrowing reads (spec 0027, Q54).

## Open questions

None.

## Commands

```bash
scripts/db/test.sh
cd apps/core && uv run pytest
cd apps/web && pnpm check:api && pnpm typecheck && pnpm build
```
