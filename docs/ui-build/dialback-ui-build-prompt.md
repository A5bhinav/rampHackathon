# Prompt: build the mapped Dialback screens on today's backend

**How to use this:** open a Claude Code session at the root of the rampHackathon repo, on a fresh
worktree from `main` (`claude -w <branch>`). Attach:

- `dialback-ui-build-plan.pdf` (the plan this prompt follows);
- an export or screenshots of the **Dialback wireframes** canvas pages *System map*, *AP app*,
  *Admin console* and *Demo tools*. The canvas opens on *Signature directions*; the screens are on the
  other pages.

Set the line below to the one phase or item you want this session to do, then paste everything
under the rule.

```
DO: Phase 1
```

(Other values: `DO: 0052` through `DO: 0058` for one phase 2 item each. Phase 3 needs a teammate's
yes first.)

---

## 0. Your job

Dialback's dashboard (`apps/web`) was restyled to the wireframes in spec 0041 (PR #95, on main). That
restyle changed how existing pages look. It did not build the pages that map and present the
product, and it left out everything the API doesn't send. Your job is to build those missing screens
on the backend we have, in the order the plan gives, without inventing data.

Do only what the `DO:` line names. Work in plan mode first: read, then write the spec, then show
me the plan before writing code.

## 1. Read these first

1. `AGENTS.md`, `CLAUDE.md`, `apps/web/AGENTS.md`, `apps/web/README.md`. They outrank this prompt.
2. `node_modules/next/dist/docs/` for this Next.js version (it differs from your training data).
3. The attached plan PDF, all of it. Sections 2, 4 and 5 are the specification you are building.
4. `specs/0041-ui-refresh.md` and `docs/ui-implementation-prompt.md` (what the restyle did, its
   design tokens and components; reuse them, don't restyle again).
5. `packages/contracts/openapi/dialback.yaml`, `docs/state-machine.md`, `docs/sequences/README.md`.
6. The files you will touch. For phase 1 that is at least: `src/lib/core.ts`, `progress.ts`,
   `flow.ts`, `states.ts`, `tour.ts`, `call.ts`, `src/components/flow.tsx`, `call.tsx`, `tour.tsx`,
   `page.tsx`, `src/app/d/[datasetId]/layout.tsx`, `cases/page.tsx`, `cases/[caseId]/page.tsx`,
   `overview/page.tsx`, and every test in `apps/web/tests/`.

## 2. Rules you never break

- **The browser never calls the core.** Server components and route handlers fetch through
  `src/lib/core.ts` with the signed-in person's token. Polling goes through a route handler in the
  app, never straight to the core or to Supabase from the browser.
- **The dashboard writes no sentence that explains a decision, refusal, reason, status or effect.**
  Render the core's: `effect`, `explanation`, checklist `sentence`, `checklist_note`,
  `permissions.reasons`, `CoreError.message`, `TimelineEntry.summary`, `status_text`, `reason_text`,
  `notes`, `Alert.message`, `call.reason`, `call_script.*`. Fixed product copy that describes the
  stations (the system map's table) is fine. Say "provable" or "not provable", never "safe".
- **No data the API didn't send.** No invented amounts, invoice numbers, health, counts or scores.
  Until a phase 2 field lands, show what exists (bill counts, for example).
- **ADR-8:** routing number and last four only (`026009593 ····0001`); spoken digits only as
  "4 digits given" plus the `last_four_result`. Typed reasons render as plain text.
- **Permissions only hide buttons**; SQL refuses the changer, the reviewer and a stale version.
  "Viewing as" signs in as the demo accounts (spec 0034); never fake a role in the browser.
- **Contracts:** never edit `packages/contracts/generated/**`. Contract changes are additive
  (optional fields, new endpoints), each in its own PR that both other teammates approve.
- **Money path:** never write `app.cases`, `app.decisions`, `app.releases` or
  `app.case_event_receipts` directly. New reads never write anything. Plan mode for any SQL.
- **Keep** every anchor (`#decision-h`, `#row-{seq}`, `#rule-{id}`, corpus ids), `?tour=N`,
  every `aria-*`, `role` and `sr-only`, the focus ring, and stillness under
  `prefers-reduced-motion`.
- No new runtime dependencies. Don't read `.env*`. Open PRs; never merge.

## 3. What already exists on main (don't rebuild it)

Inbox (`/d/[id]/cases`), Case detail with the action panel states A to G, Number lineage (approximate
chart), Mobile layouts, Overview (`/d/[id]/overview`, partial health), Trust Ledger (`/d/[id]/risk`),
Evidence (`/d/[id]/evidence`), Assurance (`/corpus`, `/scorecard` empty state), the public pages, the
guided tour (`/tour/N`), and the Signature A tokens and components (`RingBand`, `PageHead`, badges,
`STATE_LABEL`).

Spec numbers 0042 to 0048 are taken on Sami's branches. Start at **0049**, and check
`git branch -r` and `ls specs` before claiming a number.

## 4. Phase 1: web only, no contract change

Three specs, three PRs, in this order. Each PR stays under about 400 changed lines.

### 0049: Seven stations

- New `src/lib/stations.ts`: `stations(timeline, state, outcome)` returns the seven stations
  (Detected, Held, Number chosen, Called, Decided, Approved, Released), each `done`, `current`,
  `todo` or `branched` with the label from the plan's section 2 table. Read only the current
  version's events: the ones after the case's latest `change_detected`.
- `stationsFromEvidence(rows, caseSummary)` does the same for the inbox from
  `core.evidence(datasetId).events`, grouped by `case_id` and ordered by `seq`. If a case has no rows
  (the log is capped at 500), fall back to its state alone.
- The case page stepper and the inbox mini rail use seven stations. Leave `progress.ts` and its tests
  in place until nothing imports it, then remove both in the same PR.
- The case page refreshes itself every 2 seconds while the state is `open`, `held`, `calling`,
  `retry_wait`, `deciding` or `releasing`. Use a small client component that calls
  `router.refresh()`, and stop when the state settles. The refresh still runs under reduced motion;
  only animation stops.
- Tests first, in `apps/web/tests/stations.test.ts`: every row of the plan's station table, including
  each branch (`hold_failed`, `no_provable_number`, tries used up, `not_verified`, `needs_review`,
  `release_co_approved`, `release_rejected`, `review_blocked`), a second change voiding the earlier
  version, and the evidence-row grouping.

### 0050: System map page

- Route `/d/[datasetId]/map`, the first item under Control in the sidebar, matching the *System map*
  board.
- Band with the title and the chain badge. The 7-station rail with the number of cases at each
  station now (from `core.allCases` and `stationsFromEvidence`) and a link to the newest one.
- The station table: what a person must see, the data source, and the screen, linked to the newest
  demo case's anchors when one exists (`/demo/[ref]` already finds demo cases by ref).
- Exception paths, each linked to a live case when one exists (Brightline: no provable number;
  Cobalt: not verified). Surfaces: AP app, Control, Presenter tools, as links.
- Empty state with no cases: zeros, and a line that the team starts a change with
  `dialback-core demo change`.
- The guided tour gets System map as step 1. Update `tour.ts` and its tests in this PR.
- Tests: station counts from fixture cases and rows; links resolve to routes that exist.

### 0051: Presenter views (live call and flow mode)

- `src/app/d/[datasetId]/cases/[caseId]/call/page.tsx` matches the *Live call split view* board:
  phone frame (number dialed from `selection.e164`, timer from the current call's `started_at`, the
  question being asked), the disclosure from `call_script.disclosure`, the seven slots with
  `call_script.questions` wording and the recorded answers, the script-check chips, attempt "n of 3",
  and the 7-station rail.
- States (plan section 5.2): no call yet; on the call with no `result` ("Answers arrive when the call
  ends."); ended with answers (fill in turn order with a short fade, none under reduced motion);
  not connected (the core's `call.reason`, retry); decided (outcome, the core's `effect`, a link to
  `#decision-h`).
- `.../call/state/route.ts`: a GET route handler, server-side, that calls `core.case(caseId)` (and
  `core.selection(caseId)` once) with the person's token and returns only what the page draws. A
  client component asks it every 2 seconds and stops once the state leaves `calling`, `retry_wait`
  and `deciding`. The route returns 401 when signed out, and never passes the token to the browser.
- `src/app/d/[datasetId]/cases/[caseId]/flow/page.tsx` matches the *Flow mode, projector* board:
  16:9 dark, the headline, seven columns that light up as events land, each with its time, the
  core's `TimelineEntry.summary` and the short hash, and the chain badge. Same refresh.
- The case page's Call card links to the live call page; its header links to flow mode. Retarget
  tour step "The call" to the live call page.
- Tests: the page's state for each API shape above (fixtures from
  `packages/contracts/examples/`), no digits rendered for `account_last4`, the route handler refuses
  without a session, and polling stops on a settled state.

## 5. Phase 2: additive reads (one item per session)

Each item is one spec and three PRs: **contracts** (schema, examples, regenerated types), then the
**core or ledger** read with tests, then **web**. Every new field is optional, and the page must
still render without it. Take the shapes below as the starting proposal; put open choices in the
spec as questions, not guesses.

- **0052 Money and invoice numbers (G1, Abhi).** The mock already serves
  `invoice_number` and `amount: {amount, currency_code}` on bills; `ramp/http.py` drops them. Add
  `amount_cents`, `currency_code` and `invoice_number` to `RampBill`. Save them with the schedule
  at hold time in a new nullable `app.holds` column (next migration in `supabase/migrations/app/`),
  outside the hashed evidence body. API: optional `HoldInfo.bills[]` (`bill_id`, `invoice_number`,
  `amount_cents`, `currency_code`, `payment_date`) and `CaseSummary.held_cents`. Web: money becomes
  the largest number on the case and the inbox KPI. Open question for the spec: confirm Ramp's Bill
  API field names.
- **0053 The change request on the case page (G2, Inesh and Abhi).** From `ledger.change_requests`
  to `ledger.documents` and `ledger.observations`. Optional `CaseDetail.request`: `received_at`,
  `channel`, `sender`, `subject`, `thread_id`, `sha256`, `numbers[]` (`e164`, `quote`, and `eligible`
  joined from the case's selection candidates). No body text. Also fix the seed so each vendor's
  request email matches its change in `scripts/demo/seed.json` (`corpus/generator/demo.py`, then
  `python3 corpus/generator/build.py`, then `--check`).
- **0054 Lineage sightings (G3, Inesh).** Optional `SelectionView.sightings[]`: `e164`,
  `source_type`, `seen_at`, `role`, `visibility`, `in_change_context`. Only rows recorded before the
  case opened, read the way `vendor_rows()` reads them. Web: `lanePoints` draws every sighting.
- **0055 Control health (G5, Abhi).** `GET /api/datasets/{id}/health`: `app.job_runs` (poll,
  full_sweep, reconcile: last finished, ok, error), `app.inbound_webhooks` (received and bad
  signatures in the last 24 hours, last received), the `DIALBACK_*` modes, and the voice service
  (configured, plus reachable from a server-side `/healthz` call with a short timeout). Web: the
  Overview health row.
- **0056 Evidence row summaries (G6, Abhi).** Optional `EvidenceRow.summary` from the same templates
  as `TimelineEntry.summary`. Web: the inbox activity feed and the Evidence table use it; add
  client-side filters by event type and vendor over the loaded rows.
- **0057 Rules & policy (G7, Abhi and Sami).** A script writes `apps/web/src/data/rules.json` from
  `packages/decision` (rule ids, blocks, sentences, engine version), the ledger's selection settings,
  the call policy (`apps/voice/.../guards.py` caps, the calling window) and R1 to R9 from
  `AGENTS.md`. Add a `--check` mode to CI, as `corpus/generator/build.py` has. Web: `/d/[id]/rules`.
- **0058 Vendor documents (G4, Inesh).** `GET /api/datasets/{id}/vendors/{vendor_id}/documents`:
  each document's kind, channel, received_at, claimed_date, sender, subject, sha256, a hidden-text
  flag, and the phone numbers found with role, quote and visibility. Web: the Trust Ledger
  drill-in.

## 6. Phase 3: only after a teammate says yes

- **0059 Live answers turn by turn (G9, Sami):** the voice service writes each recorded answer to
  `voice.call_answers` as it lands (count only for the last four) and sets `voice.calls.status` to
  `in_progress` on connect. Add optional `CallAttempt.live`. The decision still reads only
  `call_ended`. The live call page swaps its waiting state for real answers, with no layout change.
- **Scorecard (G10, Sami):** after spec 0048 lands the lab on main.
- **People (G8):** optional.

## 7. How every PR is made

1. Spec from `specs/TEMPLATE.md` with an acceptance list and open questions.
2. Failing tests first, then the code, until they pass.
3. Review the diff against the spec with `.claude/agents/reviewer.md` in a fresh session.
4. Run and paste the output into the PR:
   - web: `cd apps/web && pnpm check:api && pnpm typecheck && pnpm test && pnpm build`
   - always: `python3 scripts/ci/boundaries.py`
   - contracts: the validate command in `AGENTS.md`
   - core, ledger, voice: that package's `uv run pytest`; SQL: `scripts/db/test.sh`
5. Screenshots at 1440 and 390 px (flow mode at 16:9) beside the matching board.
6. Open the PR with `.github/pull_request_template.md`. Never merge.

## 8. Stop and ask me when

- the board shows something the API doesn't send and the plan doesn't cover it;
- a change would touch `packages/contracts` outside a phase 2 item, or any money-path SQL;
- a test written by someone else would have to change to make yours pass;
- the spec number you wanted is taken.

## 9. Report back

At the end, give me: the spec numbers and PR links, what each PR changed in one line, the command
output summary, anything you left as an open question, and what the next session should pick up.
