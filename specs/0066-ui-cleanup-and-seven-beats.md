# 0066: UI cleanup and the 7-beat demo

**Owner:** Abhi  **Slice:** money path (presentation only)  **Target day:** Nov 10

Replaces the unbuilt 0066 of spec 0063's renumbering (the guided-demo bar). Sami's unmerged branch
`sami/redteam-reader-spec` also holds a `specs/0066-…` file; see open question 5.

## Goal

A review of main (`e9bc6dd`), signed in as the clerk and as the approver, found the product working but
the UI busy and the guided demo out of date. On a 900 px screen:

- Stacked alert banners and the ring hero take about 300 px before any content. The inbox's first case row starts at 890 px.
- The case page runs 3,728 px for Acme and 4,780 px for Delta, with the decision 2,437 to 3,246 px down.
- The clerk's inbox says "Needs you 4" and offers "Review and release" on cases the clerk can't act on.
- "Approved 2" on the station strip means two cases are waiting for approval.
- The 8-step tour opens on an empty page, tells people to run CLI commands and to scroll, and says the attack lab isn't built. The lab is on main: `lab run --split voice` passes 29 of 31, with both failures on `evals/known-failures.json`.

This spec makes the screens compact and replaces the tour with the 7 demo beats. Layout, copy placement and
navigation only: no page decides or computes anything new, and no new routes.

## Contract touched

None.

## Acceptance

PR 1: chrome
- [ ] AP workspace and Control replace the stacked alert banners with one compact chip in the top bar ("2 critical"), which opens the list. A full banner stays only on the case an alert belongs to. Control › Overview keeps its Open alerts card.
- [ ] Inner pages drop the ring hero: one title line and one subtitle line. The rings stay on the front door and sign-in.
- [ ] On the front door, the station strip no longer overlaps the ring art.
- [ ] At 1440×900, no AP or Control page shows more than one alert element above its content (except the case an alert belongs to), and every inner page's header block is at most 96 px tall.

PR 2: the case page
- [ ] The decision's headline ("Verified · 11 of 11 conditions passed") and the release (or lock) panel are above the fold, beside or under the station rail.
- [ ] Change, Hold, Number chosen, Call, Decision and Evidence trail each show a one-line summary and collapse (`<details>`). The current station's card starts open and the rest start closed. `#decision-h` and every `#rule-…` anchor still work.
- [ ] In the Call card, only the attempt the decision read stays expanded. Earlier attempts are one line each ("Attempt 1 · callback requested · Nov 11, 00:53 UTC").
- [ ] The per-answer rule chips and the checklist sit behind "Show the checks".
- [ ] The live call view: once the call has ended, the timer shows the call's length (not 00:00), and the phone frame shows the call's status and that the number is the Trust Ledger's pick.
- [ ] "Watch the call" (case header and the Ramp mock watch panel) appears only when the timeline has a placed call.
- [ ] Acme at 1440×900: the decision and the release or lock panel are visible without scrolling; the page is at most 1,600 px tall with everything collapsed; Delta's first attempt is one line.

PR 3: inbox and labels
- [ ] A role-aware inbox. "Needs you" counts only cases the signed-in person can act on. For a clerk that excludes any case they entered, and those rows say "Open", not "Review and release". It uses the same rule as the release panel's lock card (R5), not a second copy.
- [ ] The station strip is labelled "Cases now at each step", and the Approved count is named "Waiting for approval".
- [ ] Assurance and `/corpus` show the K01 known limit as "1 known limit" in amber, not a red "1".
- [ ] As Pat Clerk, "Needs you" is 0 and no row offers a release. As Riley Approver it is unchanged.

PR 4: the 7-beat guided demo
- [ ] Seven steps, each opening a page that exists today, in this order:
  1. The change (the Ramp mock, Acme's payment details)
  2. Which number (Number lineage)
  3. The call (the live call view)
  4. Code decides (`#decision-h`)
  5. A second person (the lock card, then "Viewing as")
  6. Tamper (Control › Evidence)
  7. The attack lab (`/scorecard`)
- [ ] The card buttons: "Open the call", "See the decision", "Try to release as the clerk" and "Show the whole flow".
- [ ] Steps 2 to 5 follow the case the presenter's change opened, found with `latestCase`/`pickedUp`, never hard-coded.
- [ ] `tourStep` accepts 1 to 7. No caption contains `dialback-core`, `scroll` or "isn't built". Every link resolves on the local stack after one change in the Ramp mock. Old `/tour/8` links fall back to step 7.
- [ ] The card stays through the redirects a beat's own action makes (saving the change, Viewing as, a release): the tab remembers its beat until End the tour. The last beat's button is `/tour/flow`, a value of the existing `/tour/[step]` route, not a new route.

PR 5: the scorecard
- [ ] `/scorecard` and Assurance's lab card show the newest committed lab run, read from a scorecard file that validates against `packages/contracts/schemas/scorecard.schema.json`:
  - scenarios run and passed;
  - the hard-gate failures with their FINDINGS ids;
  - results by attack class;
  - the commit and scenarios hash it ran against;
  - when it ran.

  Every number comes from the file; none is typed into the page. With no file present, the page keeps today's empty state.
- [ ] With the committed run from `uv run lab run --split voice`, `/scorecard` shows 31 scenarios and 29 passed, and lists `a02-lookalike-domain` (A28) and `l16-garbage-answers` (A29) as known failures.

Never
- [ ] No page decides or computes anything new. No new routes. No full account number or spoken digit on any page, and no phone number in any new place (the Trust Ledger's pick already shows on Number chosen, the lineage, the live call and `/corpus`).

## Tests (written first)

| Acceptance item | Test |
|---|---|
| The alert chip's counts and which alerts stay a banner on a case | `apps/web/tests/alerts.test.ts` |
| Collapsed cards: which starts open, the one-line summaries, the decision's headline | `apps/web/tests/case-cards.test.ts` |
| The call card's attempts: only the decided one is expanded; one-line summaries | `apps/web/tests/case-cards.test.ts` |
| The live call's length after the call ends; "Watch the call" only with a placed call | `apps/web/tests/live-call.test.ts` |
| "Needs you" by role (the R5 rule shared with the lock card) | `apps/web/tests/queue.test.ts` |
| The 7 steps, their buttons, captions without CLI or scroll, links that resolve | `apps/web/tests/tour.test.ts` |
| The scorecard read: validates against the schema; no file means the empty state | `apps/web/tests/scorecard.test.ts` |
| Page heights and header heights at 1440×900, as Pat Clerk and as Riley Approver | screenshots in each PR, measured with Playwright on the local stack |

## Out of scope

Bill amounts, the disclosure placeholders, flow mode's middle column (open questions 1, 2 and 4), and
the dashboard's demo reset button (it needs a contract change; for now, `scripts/demo/prepare_hosted.sh`).

## Open questions

1. **Money held.** Judges respond to "$X held" more than "8 bills", but the core API has no bill amounts; only the Ramp mock has `amount_cents`. Adding them is a contract change in its own PR, owned by the contract's owner.
2. **The disclosure placeholders.** `apps/core/src/dialback_core/views.py::call_script` uses "[your company]" and "[your AP callback number]" on purpose, so no phone number leaves the core. Proposal: fill the company name from the dataset, and render the number as a labelled chip ("AP callback line"). This needs the core's owner.
3. **Should a clerk see Control at all?** Today `canSeeControl` shows it to everyone on a demo dataset.
4. **Flow mode's empty middle column.** Fill it with the current station's detail (the answer being checked and the rule that fired), or leave flow mode as it is for now?
5. **The spec number.** Sami's unmerged branch also has a `specs/0066-red-team-the-document-reader.md`. Two specs would share the number (the repo already has two 0034s and two 0041s). Renumber one of them?
6. **PR 5 is blocked: the lab's scorecard doesn't match the contract.** `uv run lab run --split voice` writes `evals/out/scorecard.json` with `total`, `passed`, `failed`, `hard_gate_failures` (scenario ids), `by_class`, `declared`, `generated_at`, `config` and `scenarios_hash`. `packages/contracts/schemas/scorecard.schema.json` (`scorecard/v0`) instead requires `run_id`, `git_sha`, `split` (one of smoke, dev, held_out; the run's split is `voice`), `started_at`, `finished_at`, `config_version_ids`, `totals` with family counts, `hard_gates` by kind (false_verified, released_to_fraud, …) and `rates` with Wilson intervals. The run's file has none of these, so it can't validate. Turning one into the other would mean making up values, which the dashboard doesn't do. One of these is needed first:
   - the lab's owner makes `lab run` write `scorecard/v0`;
   - the schema grows a shape for what the lab writes today (a contract change in its own PR);
   - this spec drops the validation requirement and reads the lab's file as it is, with the FINDINGS ids joined from `evals/known-failures.json`.
7. **Blocked cases and "Needs you".** The core offers nobody an action on a blocked case, so the role-aware count drops them for everyone. On the local stack Riley Approver's count goes from 4 to 3, not "unchanged". Keep that (it follows the lock card's rule), or count blocked cases as needing someone?

## Commands

```bash
cd apps/web && pnpm check:api && pnpm typecheck && pnpm test && pnpm build
python3 scripts/ci/boundaries.py
```
