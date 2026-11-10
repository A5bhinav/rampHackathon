# 0041: Dashboard UI refresh ("Signature A: callback rings")

**Owner:** Abhi (shell, inbox, case, evidence, overview, public); Inesh (lineage, Trust Ledger); Sami (call card, corpus, scorecard)
**Slice:** money path (presentation only)  **Target day:** Nov 10

## Goal

Every dashboard and public page matches the wireframes (`Dialback wireframes.pdf`: Inbox, Case detail,
Action panel states, Number lineage, Mobile) and the "Signature A: callback rings" visual language,
following `docs/ui-implementation-prompt.md`. This is a presentation change only: the same core
endpoints, the same server actions, the same permissions, and every explanatory sentence is still
the core's. Serves R5 (the refusal is visible and calm), R6 (the chain is one click away on every
case) and R7 (blocked and not-confirmed states say the hold stays on).

## Contract touched

None. `packages/contracts/**` is unchanged; the gaps the wireframes show are open questions below.

## Acceptance

Tokens, type and components
- [ ] `globals.css :root` carries the tokens of the prompt's section 4 (paper, ink, brand deep/mid/light, status pairs, neutral, fills, radii, shadows, `--display`); no per-component colours outside them.
- [ ] Fonts: Inter (`--font-sans`, optical-size axis; bold and tightly tracked for h1 and key figures) and IBM Plex Mono (`--font-mono`), both on `<html>`. No serif: the team chose Inter over the prompt's Schibsted Grotesk and Instrument Serif after seeing the "Signature A" board.
- [ ] `RingBand` (`components/ring-band.tsx`) draws five `aria-hidden` circles centred 130px from the right and 80px from the top (500/396/288/180/72); compact under 720px; a `dark` variant. `PageHead` renders it, so every page head gets it.
- [ ] Badges are 22px dot-and-word pills; `StateBadge` uses `STATE_LABEL` (10 states, `words()` fallback); `released` is ok and `retry_wait` warn; `OutcomeBadge` says Verified / Not verified / Needs review; `ChainBadge` says "Chain intact" or "Chain broken at #N".
- [ ] Buttons are pills (ink primary, white secondary, white danger); cards have no shadow and no coloured side edge (`edge-*` kept as no-ops); tables have sentence-case heads and a 3px inset stripe on the first cell.
- [ ] Every interactive element shows the focus ring; nothing moves under `prefers-reduced-motion`.

Shell
- [ ] The dataset shell is a 232px paper sidebar (brand, dataset card with kind and switcher, "Accounts payable" → Inbox, "Control" → Overview, Trust Ledger, Evidence, Assurance, then How it works, the connected line, the person block with the ID and sign-out) and a utility bar (breadcrumb; "Viewing as" only when demo sign-in is on). Under 720px it stacks into a compact header with a "Menu" disclosure. Same `notFound` behaviour.
- [ ] "Viewing as" submits the existing `signInAsDemo` action; it never switches roles in the browser.

Screens
- [ ] Inbox: band ("{n} need you. {bills} bills are held across {vendors} vendors."), KPI strip (On hold, Needs you, Due ≤ 3 days, Hold not confirmed), the queue rail, anchor tabs, the queue table with the mini rail and per-state action links, held payments by due date, the latest-evidence feed (fails soft), released list; mobile turns rows into cards.
- [ ] Case: band with vendor, state, chain link, aside with the bill count and next payment; the stepper; main column 01 Change, 02 Hold (schedule table), 03 Number chosen (from `core.selection`, fails soft), 04 Call, 05 Decision (`#decision-h` kept), Record (timeline as a table); the action rail with states A–G driven only by API data. On mobile the action rail comes first.
- [ ] Lineage: "Why we call {phone}", chosen card, the first-seen-by-source chart (`lanePoints`), every-number table, rule footer.
- [ ] Trust Ledger (`/risk`), Evidence (broken / intact states, the "how" figure folded), Overview (new: health strip, state machine counts from `lib/states.ts`, alerts, held bills by vendor).
- [ ] Public: landing hero with large rings, corpus as one table per group with expandable rows (ids kept as anchors), scorecard empty state with no numbers, sign-in/up and empty front doors as the paper card layout, a not-found card for the dataset shell, the tour as a floating card.

Never
- [ ] No sentence that explains a decision, refusal, reason or status is written by the dashboard; all come from the core (`effect`, `explanation`, checklist `sentence`, `permissions.reasons`, `CoreError.message`, `TimelineEntry.summary`, `status_text`, `reason_text`, `notes`, `Alert.message`, `call.reason`, `call_script.*`).
- [ ] No full account number and no spoken digits anywhere; `ReleaseRecord.reason` is plain text in a blockquote.
- [ ] No data the API didn't send: no amounts, no invoice numbers, no email block, no webhook/voice/Ramp health, no sample scorecard numbers.
- [ ] Every `aria-*`, `role`, `sr-only`, anchor id (`#decision-h`, `#row-{seq}`, `#rule-{id}`, corpus ids) and `?tour=N` behaviour is kept.
- [ ] Failure paths: an evidence or selection fetch that fails on the inbox or case page shows the empty state, never an error page; a refused submit shows the core's message in the "Nothing was changed" callout with a reload link.

## Tests (written first)

| Acceptance item | Test |
|---|---|
| State labels cover all 10 states; unknown falls back to `words()` | `apps/web/tests/badges.test.ts` |
| State machine nodes cover every `CaseState` once; person-only edges match `docs/state-machine.md` | `apps/web/tests/states.test.ts` |
| Lineage chart placement (pick, alternates, refused, cutoff, min-age line, empty) | `apps/web/tests/lineage.test.ts` |
| Overview counts (by state, holds not confirmed, alerts by severity, held bills by vendor) | `apps/web/tests/overview.test.ts` |
| Mini rail segments derive from `stationOf()` only | `apps/web/tests/flow.test.ts` |
| `safeNext` accepts a dataset path for "Viewing as" and nothing else new | `apps/web/tests/story.test.ts` |
| Everything existing stays green | `pnpm test` |

## Out of scope

- The presenter variants (`?present=call`, `?present=flow`): not built until approved (Q5, Q6).
- Seven progress stations: the tested five-stage model in `lib/progress.ts` is unchanged (Q2).
- Evidence filters (optional in the prompt) and export.
- An Inbox count in the sidebar (needs a cases fetch in the shell on every page).

## Notes on decisions taken

- "Trust Ledger" and "Vendors" both meant `/risk`: the sidebar has one entry, "Trust Ledger", under Control.
- The Inbox page keeps its route (`/cases`); the nav says "Inbox".
- The utility bar's breadcrumb is built from the path ("Inbox / Case / Number lineage"); the vendor name is in the band's back link and title, since the shell doesn't fetch the case.
- "Viewing as" returns to the current dataset page, so `safeNext` now also accepts `/d/<uuid>` paths (strict pattern, tested). The active option follows the signed-in person's roles.
- The footer line "Decided by code … The AI only collected answers." is not written by the dashboard; the decision card shows "Rule table {engine_version}" and the core's `checklist_note`.

## Open questions

1. Money: add `amount_cents` to `ScheduleEntry` and `held_cents` to `CaseSummary` (contracts PR)? Until then every "amount" slot shows bill counts.
2. 5 vs 7 stations: extend `progress.ts` to Detected / Held / Number chosen / Called / Decided / Approved / Released (`detected` always done; `number` done when the version visited `calling`, failed on `no_provable_number`)?
3. The change-request email on the case page: expose the ledger request document?
4. Per-sighting lineage data: a `lineage(vendor_id)` endpoint? Today the chart has one dot per candidate per source type at `first_seen_at`.
5. Live call: a Realtime subscription to `voice.call_answers`, and approval for a `?present=call` presenter view.
6. Flow mode `?present=flow` approval.
7. Overview health: an endpoint for webhook signatures, the voice service and the Ramp connection?
8. Trust Ledger vendor documents drill-in: an endpoint?
9. Evidence export and server-side filters?
10. Seed mismatch: the Ramp mock ends ····0001 while the ledger request email says "account ending 4821" (`scripts/demo/seed.json` vs `packages/ledger/src/dialback_ledger/demo_seed.json`).
11. Nav naming: "Inbox" vs "Case queue"; one "Trust Ledger" entry for `/risk` (done as above; confirm).
12. The wireframe's activity rows ("Acme verified, 12 of 12. Decided by code.") are sentences; the feed shows the evidence row's event type in words and the vendor, not composed sentences. Should the core send a summary per evidence row?

## Commands

```bash
cd apps/web && pnpm check:api && pnpm typecheck && pnpm test && pnpm build
python3 scripts/ci/boundaries.py
```
