# 0090: Email door, dismissing a blocked email case (PR 7 of the email door)

**Owner:** Sami  **Slice:** money path  **Target day:** Nov 10-11
**Needs review from:** Abhi (money-path SQL)

## Goal

An email case never touches Ramp, so when the vendor denies the change the case goes to `blocked`
and, until now, stayed there for good, freezing the real vendor's real account. After this, two
different approvers, each with a written reason, can dismiss a blocked case while it is email only,
which lifts the hold so bills pay the account already on file (design:
`docs/superpowers/specs/2026-11-10-email-door-design.md`, "A way out of `blocked` for email cases").
R1 holds: no payment goes to changed bank details, because the dismiss is refused the moment any
Ramp-detected change is involved. R5 holds: the releasers are people, never the changer or the
reviewer. Task 13 is the SQL; Task 14 is the core permission, the timeline line and the button.

## Contract touched

None in this PR. `permissions.can_dismiss` was added by Task 1's contract PR. The dismiss posts the
existing `POST /api/cases/{case_id}/release` with `action=approve`.

## Acceptance

- [ ] `app.case_email_only(case)` is true only when: the case's current change came in by email
      against an account on file; that account is the vendor's only current account; no
      Ramp-detected change for the vendor has `detected_at >= opened_at`; and every change that
      joined the case (its `change_detected` evidence) is an email change against that same
      account. Anything missing or unclear is false.
- [ ] From `blocked`, `release_case(approve)` on an email-only case: the first approver gets
      `awaiting_second_approver` and the case stays `blocked`; a second, different approver moves it
      to `releasing`, with one `app.releases` row per approver and exactly one `release_hold`
      command. This holds whether or not the changer is known.
- [ ] The same approver twice answers `already_applied`, with no second approval row.
- [ ] Refused (the case stays `blocked`, the hold stays): a non-approver; no reason; `reject`; a
      case a Ramp change opened or joined; a Ramp change since the case opened; a changed or
      ambiguous account on file; the changer; the reviewer who cleared the case; the first
      dismisser acting as the second, also straight through `apply_case_event`.
- [ ] `release_case` on every other state behaves exactly as before.
- [ ] `docs/state-machine.md` shows the two new `blocked` moves.
- [ ] (Task 14) `permissions.can_dismiss` is true only for an approver on a blocked email-only case
      who hasn't approved this version; the timeline reads "Dismissed by {name}: Ramp never changed,
      so the hold can be lifted." and "Second person dismissed: the hold is being lifted."; the case
      page has a "Dismiss (Ramp never changed)" button that needs a reason.

## Tests (written first)

| Acceptance item | Test |
|---|---|
| 1 to 4 | `supabase/tests/017_email_dismiss.sql` |
| 5 | `supabase/tests/002_case_machine.sql`, `008_release_replay.sql`, `014_review_needs_approver.sql` (unchanged, still pass) |
| 6 | `scripts/db/test.sh` redraws it; the diff is in the PR |
| 7 | `apps/core/tests/test_email_door.py::test_a_not_verified_email_case_is_dismissed_by_two_people_and_the_hold_lifts`, `apps/web/tests/states.test.ts` |

## Out of scope

- The verified ending (getting a confirmed new account into Ramp).
- Dismissing a case that is not email only; those keep today's way out (another change).

## Open questions

1. **A vendor with no account on file.** An email case for a vendor with no current account
   (`old_account_id` null) is never email only, so it stays blocked. Lifting the hold there pays
   nothing anyway; refusing is the fail-closed choice. Confirm.
2. **An approval made before the case was blocked counts.** If an unknown-changer case had one
   co-approval in `awaiting_release` and was then rejected to `blocked` at the same version, that
   approval counts as the first dismissal: one more approver then dismisses. Both approvals lift
   the same hold on the same account on file, so this is still two people; say if a fresh first
   dismissal should be required.

## Commands

```bash
scripts/db/test.sh
cd apps/core && uv run pytest
cd apps/web && pnpm check:api && pnpm typecheck && pnpm build
python3 scripts/ci/boundaries.py
```
