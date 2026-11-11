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
- [ ] Only approvals made while the case is blocked count (definition below). An approval given
      in `awaiting_release`, or by an earlier dismissal whose release failed, does not, even at the
      same version: after a reject (path A), a stale Approve click (path B) or a failed release
      that a reviewer blocked (path C), the first approval made while blocked always answers
      `awaiting_second_approver`, and a second, different approver is still needed.
- [ ] The same approver twice answers `already_applied`, with no second approval row. A dismissal
      is its own request: an earlier approval of the release never answers it.
- [ ] Refused (the case stays `blocked`, the hold stays): a non-approver; no reason; `reject`; a
      case a Ramp change opened or joined; a Ramp change since the case opened; a changed or
      ambiguous account on file; the changer; the reviewer who cleared the case; the first
      dismisser acting as the second, also straight through `apply_case_event`.
- [ ] `release_case` on every other state behaves exactly as before, except the backstop below.
- [ ] Backstop (final review finding C1, `0016_email_ramp_mix.sql`): `first_approver` and
      `four_eyes` also refuse when the case's current change came in by email and
      `app.case_email_only(case)` is false
      (`app.email_release_refused(case)`). A case verifies its current change, so such a case could
      otherwise be released while Ramp pays an account the call never verified. The email door never
      makes one (spec 0087, `ramp_case_open`); this catches a bug or another writer. It also refuses an
      email case whose vendor had no account on file (rule A), which keeps that hold on (fail closed).
      Every other `eval_guard` branch is exactly 0015's, `reject` is unchanged, and no transition changes.
- [ ] `docs/state-machine.md` shows the two new `blocked` moves.
- [ ] (Task 14) `permissions.can_dismiss` is true only for an approver on a blocked email-only case
      who is not in `app.blocked_approvers(case)`; the timeline reads "Dismissed by {name}: Ramp never changed,
      so the hold can be lifted." and "Second person dismissed: the hold is being lifted."; the case
      page has a "Dismiss (Ramp never changed)" button that needs a reason. After a first dismissal
      (final review, ledger rows 254 and 283) the page says "The case is blocked, so your approval was
      recorded as a dismissal. A second, different approver must also dismiss it before the hold is
      lifted." (also when a stale Approve click on a case that is now blocked became one), and the
      first dismisser's reason reads "You have dismissed this case. A second, different approver must
      also dismiss it before the hold is lifted." `can_dismiss` excludes the changer and the reviewer.

### An approval made while blocked

- The **entry** is the case's latest receipt in `app.case_event_receipts` whose result moved it
  into `blocked` from another state (`to_state = 'blocked'`, `from_state` not `'blocked'`);
  `app.blocked_entry_seq(case_id)` returns its `evidence_seq`.
- An **approval made while blocked** is a `release_co_approved` or `release_approved` receipt for
  the case at its current version whose `evidence_seq` is greater than the entry's.
  `app.blocked_approvers(case)` returns their actors, each read from the evidence row whose
  `event_id` the receipt names.
- Why receipts and `evidence_seq`, not `app.releases.created_at`: only `apply_case_event` writes
  receipts, and `evidence_seq` is assigned under the chain's lock in commit order. A receipt's
  `applied_at` is the start of its transaction on the real clock while `created_at` is the dataset
  clock, so times cannot order them (and in one transaction all `applied_at` are equal). An
  evidence row is read only through a receipt's `event_id`, which is unique, so a forged evidence
  row is never counted.
- Dismiss requests have their own replay keys, `{case}:dismiss_first:v{n}:b{entry}:{user}` and
  `{case}:dismiss:v{n}:b{entry}:{user}`. `b{entry}` starts each blocked spell afresh.

## Tests (written first)

| Acceptance item | Test |
|---|---|
| 1 to 5 | `supabase/tests/017_email_dismiss.sql` (blocks 13 to 15: paths A, B, C; block 16: a forged evidence row) |
| 6 | `supabase/tests/002_case_machine.sql`, `008_release_replay.sql`, `014_review_needs_approver.sql` (unchanged, still pass) |
| 7 | `supabase/tests/018_email_ramp_mix.sql` (blocks 1 to 5 refused, including 4b where only `four_eyes`'s new check refuses; blocks 6 to 9 are controls that still release or reject) |
| 8 | `scripts/db/test.sh` redraws it; the diff is in the PR |
| 9 | `apps/core/tests/test_email_door.py::test_a_not_verified_email_case_is_dismissed_by_two_people_and_the_hold_lifts`, `::test_the_changer_and_the_reviewer_can_never_dismiss`, `::test_the_first_dismisser_is_told_a_second_person_must_dismiss`, `apps/web/tests/states.test.ts` |

## Stated limits (for Abhi)

- **A second release at the same version queues no command (pre-existing, new way in).**
  `release_hold` is keyed `{case}:release:v{n}` with `on conflict do nothing`. After a release fails
  (`command_failed`, then review, then `blocked` at the same version), a completed dismissal moves
  the case to `releasing` but queues no new `release_hold`, so it waits there with the hold on
  (fails closed). The same was already true for a re-release from `awaiting_release`.
  `017_email_dismiss.sql` block 15 asserts today's behaviour and says to change it when fixed.
- **The hold is lifted without a fresh look at Ramp.** `release_hold` lifts the hold using what
  Dialback's mirror says. A Ramp change not yet detected (no webhook or poll yet) is invisible to
  `case_email_only`. Running `sync_vendor` before lifting a dismissed case's hold would close that
  window.
- `evidence_seq` is per chain. If the service role moved a dataset to another chain mid-case,
  the order across the two chains would be meaningless.

## Out of scope

- The verified ending (getting a confirmed new account into Ramp).
- Dismissing a case that is not email only; those keep today's way out (another change).

## Open questions

1. **A vendor with no account on file.** An email case for a vendor with no current account
   (`old_account_id` null) is never email only, so it stays blocked. Lifting the hold there pays
   nothing anyway; refusing is the fail-closed choice. Since the C1 backstop, a Verified one also
   stays in `awaiting_release` with the hold on, because its approvals are refused. Confirm.
2. (Settled) Approvals made before the case was blocked do not count; see "An approval made while
   blocked".

## Commands

```bash
scripts/db/test.sh
cd apps/core && uv run pytest
cd apps/web && pnpm check:api && pnpm typecheck && pnpm build
python3 scripts/ci/boundaries.py
```
