# Prompt: implement the Dialback UI refresh in apps/web, screen by screen

Paste everything below into a Claude Code session opened at the root of the rampHackathon repo, with
the wireframe screenshots attached (System map, Inbox, Case detail, Action panel states, Number lineage,
Mobile, Overview, Trust Ledger, Evidence, Assurance, Live call, Flow mode, and the "Signature A:
callback rings" board).

---

## 0. Your job

Restyle and restructure the existing Next.js dashboard in `apps/web` so every screen matches the
attached wireframes and the "Signature A: callback rings" visual language. This is a presentation-layer
change. You must not change how data is fetched, who may act, what the API returns, or any explanation
text the core produces. Where the wireframe shows something the API does not send, follow the
"data gaps" rules in section 3, never invent data.

Work in plan mode. Write `specs/0041-ui-refresh.md` from `specs/TEMPLATE.md` first, with an acceptance
list built from sections 6–8 of this prompt and every item in section 10 as an open question. Then ship
it as the small pull requests listed in section 11.

## 1. Read these first, in this order

1. `AGENTS.md`, `CLAUDE.md`, `apps/web/AGENTS.md`, `apps/web/README.md`. If they conflict with this prompt, they win.
2. `node_modules/next/dist/docs/` for Next 16.4 (breaking changes vs. your training data; heed deprecations).
3. `docs/Dialback_System_Design.pdf`, `docs/state-machine.md`, `docs/threat-model.md`.
4. Specs 0009, 0023, 0026, 0031, 0032, 0033, 0034, 0036, 0037, 0038, 0039, 0040.
5. Every file you will touch:
   - Root: `src/app/layout.tsx` (fonts, TourPanel), `src/app/globals.css` (641 lines, all tokens and classes), `src/app/landing.css`.
   - Shell: `src/app/d/[datasetId]/layout.tsx`.
   - Pages:
     - `src/app/d/[datasetId]/cases/page.tsx`
     - `src/app/d/[datasetId]/cases/[caseId]/page.tsx` and its `actions.ts`
     - `src/app/d/[datasetId]/cases/[caseId]/lineage/page.tsx`
     - `src/app/d/[datasetId]/risk/page.tsx`
     - `src/app/d/[datasetId]/evidence/page.tsx`
     - `src/app/corpus/page.tsx`, `src/app/scorecard/page.tsx`
     - `src/app/page.tsx` (public "How it works")
     - `src/app/sign-in/page.tsx`, `src/app/sign-up/page.tsx`
     - `src/app/app/page.tsx`, `src/app/demo/[ref]/page.tsx`, `src/app/tour/[step]/page.tsx`
   - Components in `src/components/`: `page.tsx` (PageHead, Stats), `nav.tsx` (NavLinks, BrandMark), `badges.tsx`, `alerts.tsx`, `console.tsx`, `decision.tsx`, `call.tsx`, `flow.tsx` (QueueRail, CaseRail, PaymentLanes), `path.tsx` (PaymentPath), `icons.tsx`, `tour.tsx`, `showcase.tsx`.
   - Libs in `src/lib/`: `progress.ts`, `flow.ts`, `format.ts`, `risk.ts`, `lineage.ts`, `call.ts`, `rules.ts`, `evidence.ts`, `corpus.ts`, `story.ts`, `showcase.ts`, `tour.ts`, `core.ts`, `api-types.ts`.
   - Tests in `apps/web/tests/*.test.ts`. These must keep passing.

## 2. Non-negotiable repo rules

- The browser never calls the core. Pages fetch server-side through `src/lib/core.ts` with the user's token.
  Do not add endpoints to `core.ts` unless the contract already has them. The contract has exactly:
  - `/api/me`, `/api/datasets`
  - `/api/datasets/{id}/chain`, `/api/datasets/{id}/evidence`, `/api/datasets/{id}/alerts`, `/api/datasets/{id}/vendor-risk`
  - `/api/cases`, `/api/cases/{id}`, `/api/cases/{id}/selection`, `/api/cases/{id}/release`, `/api/cases/{id}/review`
- Never edit `packages/contracts/generated/**`. Don't change `packages/contracts/**` in this work (contracts change only in their own PR).
- Every sentence that explains a decision, refusal, reason, status or effect is the core's text:
  - `decision.effect`, `decision.explanation`, checklist `sentence`
  - `permissions.reasons`, `CoreError.message`
  - `TimelineEntry.summary`, `VendorRisk.status_text`
  - `SelectionView.reason_text` / `notes` / `selection.reason`
  - `Alert.message`, `call.reason`, `call_script.*`
  
  The wireframe's sentences are illustrations; render the API's. Say "provable"/"not provable", never "safe".
- `permissions` only hides or shows controls. The database refuses the changer (R5), the reviewer and a stale version. Refusals are explained, inline states; never toasts or generic errors.
- Never render a full account number. Only `routing_number` + `last4`, formatted `026009593 ····0001`. Never show spoken digits: show `digit_count` as "4 digits given" plus `last_four_result`.
- Text people typed (`ReleaseRecord.reason`) renders as plain text in a `<blockquote>`, never as HTML.
- Times stay UTC and labeled (`when()` in `lib/format.ts`). Dataset "now" is `datasetNow(sim_now)`.
- No new runtime dependencies. Fonts come from `next/font/google`. Don't read `.env*`. Never merge; open PRs.
- Keep every existing `aria-*`, `role`, `sr-only` text, anchor id (`#decision-h`, `#row-{seq}`, `#rule-{id}`, case ids in `/corpus`), and `?tour=N` behavior. The guided tour links into these.

## 3. Data reality: what the wireframe shows vs. what the API sends

Implement the "Use" column. Put every "Gap" in the spec's open questions.

| Wireframe element | API reality | Use | Gap |
|---|---|---|---|
| Dollar amounts ($17,300.00 held, per-bill amounts, money on hold by vendor) | No amount field anywhere (`CaseSummary.bills_held` is a count; `HoldInfo.schedule[]` = bill_id + payment_date) | Show bill counts ("2 bills held") and dates; the "big figure" slot shows the bill count, or the next payment date where noted | Add `amount_cents` to ScheduleEntry / CaseSummary in a contracts PR; then swap the figure to money |
| Invoice numbers (ACME-1001) | `bill_id` is a UUID | `bill_id.slice(0,8)` in mono, full id in `title` | Ramp invoice number in schedule |
| Change-request email (subject, sender, thread) | Not in `CaseDetail` | Omit the email block | Expose the ledger request doc |
| "Number chosen" card on the case page | `GET /api/cases/{id}/selection` exists (lineage uses it) | Fetch `core.selection(caseId)` in the case page too | — |
| Live call answers filling turn by turn | Answers have `captured_at`, but the page is server-rendered; no Realtime | Render answers already recorded; refresh on navigation | Realtime subscription to `voice.call_answers` |
| Lineage swimlane with every sighting | Candidates carry `first_seen_at`, `observation_count`, `source_types` only | One dot per candidate per source type at `first_seen_at` | `lineage(vendor_id)` per-sighting dates |
| Overview health row | Chain via `core.chain`/`core.evidence(...).last_check`; alerts via `core.alerts`; no webhook/voice/Ramp health | Show chain status, last check, open alerts, hold-not-confirmed count; label others "not reported" or omit | Health endpoint |
| Inbox search | No search endpoint | Omit the search field | — |
| Inbox activity feed | `core.evidence(datasetId).events` (type, vendor, occurred_at, case_id, actor_kind) | Newest 6 evidence rows, `words(type)` + vendor link | — |
| Inbox "this week" calendar | `PaymentLanes` already plots holds by due date from real data | Restyle `PaymentLanes`; do not build the calendar | — |
| Trust Ledger vendor documents drill-in | Not in API | Omit | Vendor documents endpoint |
| Evidence filters / export | No server filter or export | Optional client-side filter by event type and vendor over the loaded rows; no export | Export endpoint |
| People, Integrations, Rules & policy screens | No endpoints | Do not add nav items | — |
| Attack-lab scorecard tiles | No lab results | Keep `/scorecard` as the empty state; never show sample numbers | Lab results artifact |
| Flow mode (projector), Live call split view | No routes today | Section 7.12–7.13: presenter variants behind `?present=1` only if the spec approves; otherwise restyle `CallCard` and `CaseRail` | Decide in spec |

**Seven stations vs. five.** The wireframe draws 7 stations (Detected, Held, Number chosen, Called, Decided, Approved, Released). The code's tested progress model (`lib/progress.ts`, `lib/flow.ts`, spec 0031) has 5: Hold, Call, Decision, Sign-off, Release. Keep the 5-stage logic and its tests unchanged. Restyle the rail to the wireframe's look. Write "move to 7 stations" as an open question. If the team approves it, it's a separate PR that adds `detected` and `number` stages to `progress.ts`, with tests:
- `detected`: always done once a case exists.
- `number`: done when the version visited `calling`; failed when the timeline has `no_provable_number`.

## 4. Design tokens: replace the values in `globals.css :root`

Change tokens, not per-component colors. Keep the existing variable names so every class follows, and add the new ones.

```css
:root {
  /* surfaces */
  --bg: #ffffff;            /* page (was #f6f8fb) */
  --surface: #ffffff;
  --surface-2: #fafaf9;     /* table heads, quiet fills */
  --paper: #f6f4ee;         /* NEW: sidebar, ring bands, empty-state fills */
  --paper-line: #e9e5da;    /* NEW: border under bands, sidebar edge */
  --paper-active: #eae6da;  /* NEW: active nav item */
  --paper-hover: #efebe1;   /* NEW: nav hover */
  /* ink */
  --ink: #111113;           /* NEW: primary buttons, headings */
  --text: #111113;
  --text-2: #3f3f46;
  --muted: #6b6b72;         /* 5.3:1 on white, keep ≥4.5:1 */
  --marker: #8a877d;        /* NEW: mono section markers only (11px uppercase), never body text */
  --line: #ebebe8;
  --line-soft: #f1f1ef;     /* NEW: row dividers inside cards */
  --line-strong: #dcdcd8;   /* inputs, secondary button borders */
  /* brand (Signature A) */
  --brand-deep: #0b5d4b;    /* NEW */
  --brand-mid: #2f9e78;     /* NEW */
  --brand-light: #bfe8d6;   /* NEW */
  --accent: #0b5d4b;        /* links, active station, focus (was blue) */
  --accent-hover: #084536;
  --accent-ink: #ffffff;
  --accent-soft: #eaf5ee;
  /* status: background / foreground pairs, always with a word */
  --ok-bg: #eaf5ee;   --ok: #146c3e;
  --warn-bg: #fdf3e1; --warn: #8a4b00;
  --bad-bg: #fcebea;  --bad: #b42318;
  --info-bg: #edf1fc; --info: #2843b0;
  --neutral-bg: #f1f1ef; --neutral: #55555c;   /* NEW: replaces tone-muted's colors */
  /* chart fills */
  --fill-ok: #2f9e78; --fill-warn: #c27a10; --fill-bad: #b42318; --fill-muted: #a1a1a6; --fill-brand: #0b5d4b;
  /* shape */
  --radius: 12px; --radius-sm: 8px; --radius-xs: 6px; --radius-pill: 999px;
  --shadow: 0 1px 2px rgba(16,16,20,.04), 0 4px 16px rgba(16,16,20,.05);
  --shadow-lift: 0 8px 32px rgba(16,16,20,.12);
  --ring: 0 0 0 2px var(--surface), 0 0 0 4px var(--accent);
  /* type */
  --sans: var(--font-sans), ui-sans-serif, system-ui, sans-serif;
  --display: var(--font-display), Georgia, serif;   /* NEW */
  --mono: var(--font-mono), ui-monospace, "SF Mono", Menlo, monospace;
}
```

**Fonts.** In `src/app/layout.tsx`:
- Replace `IBM_Plex_Sans` with `Schibsted_Grotesk` (weights 400, 500, 600, 700, `--font-sans`).
- Keep `IBM_Plex_Mono` (400, 500, `--font-mono`).
- Add `Instrument_Serif` (weight 400, normal style, `--font-display`).
- Put all three variables on `<html>`.

**Type scale.** Use these sizes only.

| Role | Font | Size / line height | Weight | Notes |
|---|---|---|---|---|
| Page title (h1, in the ring band) | `--display` | 42/46 | 400 | letter-spacing -0.012em; public pages' hero 64/64 |
| Section title (h2) | `--sans` | 16/22 | 600 | sentence case |
| Sub-head (h3) | `--sans` | 14/20 | 600 | |
| Body | `--sans` | 14/20 (15/22 on public pages) | 400 | |
| Small / meta | `--sans` | 13/18 | 400 | `--muted` |
| Key figure (KPI, amount slot, "Verified") | `--sans` | 26/32, KPI strip 30/36 | 600 | tabular-nums, letter-spacing -0.02em |
| Table emphasis (row figure) | `--sans` | 16/22 | 600 | |
| Data (accounts, phones, ids, hashes, times in tables) | `--mono` | 12.5/18 (14–15 in "big" slots, 22/28 for the chosen phone) | 400/500 | tabular |
| Section marker ("01 CHANGE", "ON HOLD NOW") | `--mono` | 11/14 | 500 | uppercase, letter-spacing .08em, `--marker` |

Serif appears ONLY in h1. Never use the serif for numbers, h2, buttons or table text. No italics.

**Spacing.** 8px grid:
- Page content padding 28px 32px 48px.
- Ring band padding 36px 32px 32px.
- Card header 16px 20px; card body 16px 20px.
- Table cells 12–14px 16px.
- Gap between page sections 24–28px.

**Components (CSS classes in `globals.css`; restyle the existing classes, add new ones as listed):**

- `.badge` (status pill), 22px tall:
  - padding 0 8px, `--radius-xs`, 12px/500, no border.
  - `::before` 6px dot in `currentColor`.
  - `.tone-ok/.tone-warn/.tone-bad/.tone-info` use the pairs above; `.tone-muted` uses `--neutral-bg`/`--neutral`.
  - Every badge keeps its word: color never stands alone.
- Buttons and `.button-link`:
  - Primary: `--ink` background, white text, `--radius-pill`, 36px (40px in the action panel, 48px on mobile), 14px/500, hover #2a2a2e.
  - Secondary: white, 1px `--line-strong`, pill.
  - `.danger`: white with `--bad` text, pill.
  - Disabled: `#f4f4f2` background, `#a1a1a6` text.
  - Focus: `--ring`.
- `.card`: white, 1px `--line`, `--radius`, no shadow.
  - `.card-head`: 16px 20px, bottom border `--line-soft`, flex with a right-aligned meta slot.
  - Remove the colored `edge-*` side borders (they read as an "AI card" trope). Show outcome with a badge in the header instead. Keep the class names as no-ops so the markup still compiles.
- Tables:
  - `th`: 12px/500 `--muted`, sentence case (no uppercase), `--surface-2` background, bottom 1px `--line`.
  - `td`: 12–14px 16px with 1px `--line-soft` dividers; the last row has no divider.
  - `.num`: right-aligned, tabular.
  - Row stripes (`tr.is-bad/is-warn/is-ok`) become a 3px inset left shadow in the tone color on the first cell only, plus a very light row tint for `is-bad` (`#fdf6f5`).
- `.stats` (the KPI strip):
  - One bordered card split into equal cells by 1px `--line` vertical rules.
  - Each cell: section marker label (mono 11px uppercase), then the figure at 30/36 600.
  - A non-zero toned stat colors only its figure (`--bad`, `--warn`, `--ok`).
- `.section-marker` (NEW): the mono 11px uppercase marker, used before card titles: `01`, `02`, … and `RECORD`.
- `.banner` (alerts):
  - Full-width rounded 10px.
  - critical: `--bad-bg` background, 1px `#f3c7c3` border, `#7a1a12` text, triangle icon.
  - warning: `--warn-bg`, 1px `#ebcb8f`.
  - Title 600, then the core's message, then right-aligned time and an "Open the case" link.
- Focus: visible `--ring` on every interactive element. Motion: keep the 150/120/90ms timings; none under `prefers-reduced-motion`.

**Signature: `RingBand`.** New component `src/components/ring-band.tsx`, CSS `.ring-band`.
- Layout: paper background, full content width, `overflow:hidden`, `position:relative`, bottom border 1px `--paper-line`.
- Text: inner content `position:relative` with max-width 820px. Inside it: optional back link (13px, accent, "← Case queue"), section marker eyebrow, h1, and a lead line (15px `--muted`). An optional `aside` sits on the right with a `margin-right` that clears the rings (340px desktop).
- Rings: five absolutely positioned circles, `aria-hidden`, `pointer-events:none`, all centered at 130px from the right edge and 80px from the top:

  | Ring | Diameter | Fill |
  |---|---|---|
  | Outline | 500px | 1px `--brand-deep` border at 25% opacity, no fill |
  | Deep | 396px | `--brand-deep` |
  | Mid | 288px | `--brand-mid` |
  | Light | 180px | `--brand-light` |
  | Center | 72px | `--paper` |

  Each circle uses `right = 130 - d/2` and `top = 80 - d/2`.
- Variants:
  - `default`: as above.
  - `compact`: mobile, all diameters halved, center 50px from the right and 60px from the top.
  - `dark`: on `#121211`. Outline 35% `--brand-mid`; center circle `#121211`.
- Below 720px, use `compact` and drop the aside's margin.
- The rings are the ONLY decoration in the product. No gradients, no glows, no emoji, no extra illustrations.

**Brand mark.** Replace the handset SVG in `BrandMark` (`components/nav.tsx`) with a 22px circle: `--brand-deep` fill and an inset 5px `--brand-mid` ring (`box-shadow: inset 0 0 0 5px`). Followed by "Dialback" in 16px/700, letter-spacing -0.01em.

## 5. Shared components to change (file by file)

1. **`components/page.tsx` → `PageHead`** renders a `RingBand`.
   - Keep the props `title`, `lead`, `back`, `aside`, `children`. Add `eyebrow?: string` and `variant?`.
   - `children` (tags) render under the lead, inside the band.
   - Every page that uses `PageHead` gets the band automatically. Don't create a second header component.
2. **`Stats`** gets the KPI strip styling from section 4. Labels become section markers. Add an optional `sub?: React.ReactNode` per stat, a 13px muted line under the figure.
3. **`badges.tsx`**:
   - Add `STATE_LABEL` and render it instead of `words(state)`:

     | State | Label |
     |---|---|
     | `open` | Placing hold |
     | `held` | On hold |
     | `calling` | Calling vendor |
     | `retry_wait` | Retry scheduled |
     | `deciding` | Deciding |
     | `awaiting_release` | Ready to release |
     | `needs_review` | Needs review |
     | `blocked` | Blocked |
     | `releasing` | Releasing hold |
     | `released` | Released |
     | anything else | `words(state)` |

   - Tones: `released` becomes `ok` (was `muted`), `retry_wait` becomes `warn`.
   - `OutcomeBadge` labels: Verified, Not verified, Needs review.
   - `ChainBadge` text: "Chain intact" (title shows the event count) or "Chain broken at #N".
   - Unit-test the label map in `tests/badges.test.ts` (new).
4. **`nav.tsx`**: the new `BrandMark`. `NavLinks` accepts `{ href, label, icon, count? }` and renders a 16px icon, the label and a right-aligned muted count.
5. **`icons.tsx`**: add `inbox`, `folder`, `building`, `grid`, `shield`, `link`, `check-circle`. Use a 20px viewBox, 1.6 stroke, and the same style as the existing set. Keep every existing icon.
6. **`alerts.tsx`**: restyle per `.banner`. Keep titles, limits, roles and links.
7. **`console.tsx` → `DecisionConsole`**: restyled as the action panel (section 7.3). Same props and same form fields: `case_id`, `expected_version`, `path`, `reason`, and `action` button values. Changes:
   - Label "Reason" with a muted "(required)".
   - Textarea: 3 rows, 14px, `--line-strong` border, radius 8.
   - Buttons in one row: the primary first with flex:1, then the secondary or danger button.
   - `state.error` renders as the "refusal callout" (section 7.3 state D), not a red line.
8. **`decision.tsx` → `DecisionDetail`**:
   - Outcome line: the badge, plus a large figure word ("Verified" in 26/32 600, colored by tone).
   - Then "N of 12 conditions" muted, and the engine version in mono.
   - `decision.effect` at 15/22.
   - Rule groups keep their titles and the core's sentences; rule ids in mono muted, right-aligned.
   - Checklist as a table-like list with three columns: rule id (mono, 44px column), sentence, and the status word right-aligned and colored by status.
   - Keep `#rule-{id}` anchors and the folded `<details>` when nothing ran.
   - Footer line: "Decided by code, rule table {engine_version}. The AI only collected answers." Render this only if the core sends equivalent text in `checklist_note` or `explanation`. Otherwise show just "Rule table {engine_version}".
9. **`call.tsx` → `CallCard`**:
   - Header: h2 "Call", "attempt n of total", and the result badge. The badge "AI agent: collects answers only" (static label, not a decision sentence) goes right-aligned.
   - Meta `dl` as a 4-column inline grid.
   - Disclosure in a `--surface-2` rounded block, labeled "Turn 1 · disclosure".
   - Each Q/A becomes a 3-column grid row: scripted wording; answer (500 weight); then "turn N · keypad/spoken" in mono muted.
   - Rule chips stay as small neutral pills under the answer.
   - Last four: "{digit_count} digits given" plus the `last_four_result` badge. Never digits.
   - Script checks: a row of inline check items, "✓ label" in `--ok` or "✕ label" in `--bad`, with the status word kept.
10. **`flow.tsx`**:
    - `QueueRail`: a horizontal 5-step strip inside one card. Each station: a 28px circle node (icon), label 13/600, meaning 12 muted, and a count at 26/32 600. Zero counts are muted. The sign-off split badges sit under the count.
    - `CaseRail`: a horizontal stepper with connectors.
      - done: deep-green filled 22px circle with a white check, and a 2px deep connector to the next step.
      - current: white circle, 2px deep border, 8px deep inner dot, 5px `--brand-light` halo. Label 600 deep, note "Waiting on you" etc. in deep.
      - failed: `--bad` circle with ✕. skipped: dashed gray circle. todo: 2px `--line-strong` ring, muted label.
      - Notes (times, "Answered", outcome) at 13 muted.
    - `PaymentLanes` is the wireframe's "Held payments by due date". Restyle:
      - Chart head with a section marker "THIS WEEK" and title.
      - Track background `--surface-2`; the today line 1px `--ink`; the overdue side shaded `#fdf6f5`.
      - Pay marks: 12px circles in `--fill-brand`, or `--fill-bad` when soon. Deadline marks: 2px `--warn` ticks.
      - Lane names link, 13/500.
      - Keep the figcaption text.
11. **`path.tsx` → `PaymentPath`**: the wireframe's "previous account → new account" pair.
    - Two side-by-side boxes with an arrow between them: previous account (`--surface-2`, mono 15px, muted) and new account (white, `--line-strong` border, mono 15px 500).
    - The hold gate becomes a status badge row under them: held → info "On hold in Ramp", lifted → ok, placing → neutral, failed → bad.
    - Keep `aria-label` and the gate text from `holdGate()`.
12. **`tour.tsx` → `TourPanel`**: a floating card, fixed bottom-right (24px inset), 340px wide, `--shadow-lift`, radius 12.
    - Top line: "Guided demo" left and "n of 8" right (13 muted).
    - An 8-segment progress bar (3px, deep for done/current, `--line` for the rest).
    - Title 15/600, the caption (13/18), then the actions: Back (secondary pill), Next (primary pill), and "End the tour" text link.
    - On ≤480px it becomes a bottom sheet (full width).

## 6. The shell (`src/app/d/[datasetId]/layout.tsx`)

Replace the top bar with a left sidebar plus a slim utility bar. Same data (`core.me()`, `core.datasets()`, `core.alerts()`) and the same notFound behavior.

- **Sidebar** (`<aside class="side">`):
  - 232px wide, `--paper` background, right border 1px `--paper-line`, padding 16px 12px, vertical flex with 20px gap, full height (`min-height:100vh`). Not sticky.
  - Top to bottom:
    1. Brand link (to the case queue).
    2. Dataset card: white, radius 8, 1px `--line`. Shows `current.label` (500), a neutral badge with `current.kind` (and "simulated time" when `sim_now` is set), and the existing `<details>` "Switch" list when there are several datasets.
    3. Nav group "Accounts payable": Inbox (icon `inbox`, href `/d/{id}/cases`, count = cases waiting on a person if cheaply available, else none) and Vendors (icon `building`, href `/d/{id}/risk`; label it "Vendors" in the AP group).
    4. Nav group "Control": Overview (`grid`, `/d/{id}/overview`, new), Trust Ledger (`shield`, also `/d/{id}/risk`; see the note below), Evidence (`link`, `/d/{id}/evidence`), Assurance (`check-circle`, `/corpus`).
    5. "How it works" (`/`).
    6. Bottom: a "Ramp Bill Pay connected" line (a 6px `--ok` dot and the text; show it only if the dataset exists, never claim live status beyond that).
    7. The person block: 28px initials avatar on `--line`, name 500, roles joined in muted, the ID line in mono 11px muted (keep its title text). The sign-out button sits as a quiet text button.
  - Group headers: 12px `--marker`, padding 12px 10px 4px.
  - Nav items: 7px 10px, radius 6, 14px `--text-2`. Hover `--paper-hover`. `aria-current` gets `--paper-active`, `--ink` and 500.
  - Trust Ledger vs. Vendors: both show `/risk`. Use only ONE nav entry, "Trust Ledger", under Control, and drop "Vendors" from the AP group. Note this in the spec.
- **Utility bar** (top of the main column):
  - 12px 32px padding, bottom border `--line`.
  - Breadcrumb on the left (13px; accent links with "/" separators) on case and lineage pages.
  - "Viewing as" on the right, as a segmented control: 2px padding, radius 8, `--surface-2`; the active option white with a `--line-strong` hairline. Render it only when `DIALBACK_DEMO_SIGN_IN === "on"`. Each option submits the existing demo sign-in server action (spec 0034, `sign-in/actions.ts`) for that role and returns to the current path. Never switch roles client-side.
  - No search field.
- **Alert banners**: under the utility bar, above the page's ring band, with 16px 32px padding.
- **Layout**: `display:flex; flex-wrap:wrap`. The sidebar is `flex:1 1 232px; max-width:232px`; main is `flex:999 1 560px; min-width:0`. At ≤720px the sidebar stacks on top as a compact header: brand plus a horizontally scrollable nav row; hide the dataset card and person block behind a `<details>` "Menu".

## 7. Screen by screen

Each screen: the elements in order, where each element's data comes from, and its empty and edge states. "Marker" means the mono section marker.

### 7.1 Inbox (case queue): `src/app/d/[datasetId]/cases/page.tsx`
- Fetches unchanged: `core.cases`, `core.datasets`, `core.allCases`. Add `core.evidence(datasetId)` for the activity feed, wrapped in `.catch(() => null)` so the queue never fails on it.
- **Elements, in order:**
  1. `PageHead`: eyebrow "{dataset label} · Accounts payable", title "Inbox", lead "{waiting.length} need you. {bills on hold} bills are held across {vendors with a confirmed hold} vendors." (counts computed in the page; numbers only, no explanation sentences).
  2. The demo "missing" note (`?missing=`): keep the content; restyle as a neutral callout (`--surface-2`, radius 8, 13/18).
  3. KPI strip (`Stats`), four cells:

     | Cell | Value | Tone and sub-line |
     |---|---|---|
     | ON HOLD | the bills-on-hold count (amount once the contract has it, see gap) | sub: "{n} vendors" |
     | NEEDS YOU | waiting.length | warn |
     | DUE ≤ 3 DAYS | dueSoon | bad; sub: the soonest vendor names, 13 muted |
     | HOLD NOT CONFIRMED | unconfirmed | bad |

  4. `QueueRail` (restyled), directly under the strip.
  5. Tabs: a `role=tablist` row with "Needs you {n}", "In progress {n}" and "Released {n}". These are anchor links to `#waiting`, `#working` and `#released` on the same page (no client state needed). Active tab: 2px `--ink` underline and 500. Right-aligned marker: "SORTED BY SOONEST PAYMENT".
  6. `QueueTable` "Waiting on a person" and "In progress" (`id` attributes for the tabs). Columns, in order:

     | Column | Content |
     |---|---|
     | Vendor | Name, link, 15/600. Under it, mono 11.5 muted: "v{version} · opened {brief}". The "two approvers" badge if `unknown_changer`. |
     | Status | `StateBadge`, plus a 7-segment mini rail. Segments are the 5 progress stations rendered as 14×4px bars: done = deep, current = deep at 45%, failed = `--fill-bad`, needs-review branch = `--fill-warn`, todo = `--line`. Derive them from `stationOf(c.state)` only. |
     | Decision | `OutcomeBadge`, or "—". |
     | Held | Right-aligned: the bill count at 16/600 with "bills" muted; or the "hold not confirmed" badge. |
     | Next payment | `date()`, with the due badge under it (bad if soon). |
     | Deadline | `brief` plus relative or "passed", as now. |
     | Action | Right-aligned. `awaiting_release` → primary pill "Review and release". `needs_review` → secondary "Review". Otherwise a muted link "Open". All of them link to the case. These only label links; permissions are still enforced on the case page. |

     The row stripe follows section 4.
  7. `PaymentLanes`, restyled; title "Held payments by due date", marker "THIS WEEK".
  8. Activity (new, right column beside `PaymentLanes` on ≥1100px; stacked below otherwise): a card with marker "ACTIVITY" and title "Latest evidence". Up to 6 rows of the newest `events`. Each row is a 2-column grid: the time (`brief(occurred_at)`, mono 12 muted) and "{words(type)} · {vendor link}". A footer link goes to Evidence. If evidence failed or is empty, show "No evidence rows yet." (13 muted).
  9. Released: a card list, as now, restyled (`id="released"`).
- **Empty state:** when there are no open cases, show a card with `--surface-2` fill, the existing sentence, and a small three-ring motif (60/40/20px, rings colors) on the left.
- **Mobile:** the KPI strip becomes a 2×2 grid. The table becomes cards: one card per case with vendor + held on the first line, status badge + due label on the second, and the action button full width. The rails stack.

### 7.2 Case detail: `src/app/d/[datasetId]/cases/[caseId]/page.tsx`
- Fetches: `core.case`, `core.datasets`. Add `core.selection(caseId).catch(() => null)` for the Number card.
- **Order:**
  1. `done` notice (`NOTICES`): an ok callout at the top of the main column, kept as is.
  2. `PageHead` / RingBand:
     - back link "← Inbox", eyebrow "Bank change · {change.detected_via}", title `c.vendor.legal_name`.
     - Tags in the band: `StateBadge`, the `ChainBadge` link (existing href logic), the unknown-changer badge, the deadline badge, "Version {n}" (13 muted).
     - Aside, right side of the band, clearing the rings: marker "HELD", then the bill count at 30/36 600 ("2 bills", or the amount once available), then "next payment {date} · {due label}" (13 muted).
     - Remove the separate "Number lineage" aside button; the link moves to section 03.
  3. `CaseRail`, restyled.
  4. Two columns: main (`flex:999 1 560px`) and the action rail (`flex:1 1 320px; max-width:360px; position:sticky; top:16px`). This swaps the current layout: change and hold move into the main column; the side column becomes the actions.
- **Main column sections** (each a `.card` with a `.card-head` containing a marker and an h2):
  - **01 Change** (from the current "The change" panel):
    - `PaymentPath` restyled.
    - A `dl.facts` as a 150px label / value grid: Detected (`when(change.detected_at)` + "via {detected_via}"), Entered by (`change.changed_by?.display_name` or the existing unknown text).
    - Head meta, right: "Detected via {detected_via}".
  - **02 Hold** (from "The hold"):
    - Head meta: a status badge ("Confirmed {brief(confirmed_at)}" ok; "Not confirmed" bad; "Placing" neutral).
    - Schedule as a table: Bill (mono `bill_id.slice(0,8)`), Saved payment date, Due (badge), Status (info badge "On hold", or ok "Restored" when `released_at` is set).
    - `ramp_ref` in mono under the table if present. `last_error` as a bad callout.
    - Keep the existing placing-hold sentence for a null hold.
  - **03 Number chosen** (new):
    - Pick present: the phone in mono 22/28 (`phone(pick.e164)`), source-type badges (`sourceLabel`), "First seen {seenBefore(...)}", and a "View lineage →" link to `/lineage` in the card head.
    - No selection: render `view.notes.no_selection`. Not provable: a bad badge, `s.reason` and `view.notes.no_provable_number`.
    - Use the same `split()` and helpers as the lineage page; don't duplicate the logic.
  - **04 Call:** one `CallCard` per attempt, restyled. When there are no calls and the state isn't `open`, show the existing empty sentence in a `--surface-2` card.
  - **05 Decision:**
    - `id="decision-h"` stays on its h2.
    - `DecisionDetail` restyled, or the existing "no decision yet" / "last step" text.
    - The card head shows the outcome badge on the right.
  - **RECORD · Evidence trail** (the timeline):
    - Table columns: #, Event (`summary`, the core's text), State (`from → to` via `STATE_LABEL`), By (`actor.display_name`), Time (`brief`, title `when`), Hash (mono, first 12 characters, title full).
    - Head link: "Open in Evidence".
- **Action rail** (top to bottom):
  1. `firstApproval` note: the existing text, as an ok callout with a 2-segment progress bar labeled "1 of 2 approvals".
  2. Release `DecisionConsole`, if `can_approve || can_reject`. Wrapper card with a 1px `--line-strong` border and `--shadow`.
     - Head: h2 "Release payment", then the figure line (the bill count, or the amount when available) and "to {vendor}" (13 muted).
     - Then a compact `dl`: Decision (`OutcomeBadge`), Entered by, Approvals ("{approvals for this version} of {unknown_changer ? 2 : 1}").
     - Then the console form. Keep the hint text from the current code as the 13px muted line under the buttons.
  3. Review `DecisionConsole`, if `can_review`, same styling; title "Review case".
  4. `can.reasons`: the refusal callout (state A in 7.3). Shown when no console is offered.
  5. Approvals list (`detail.releases`), moved here from the old side panel: name 600, "approved/rejected version n", time muted, reason in a `--surface-2` blockquote.
- **Mobile:**
  - The action rail moves directly under the RingBand, before the stepper, so an approver sees release first. It's not sticky, and its buttons are 48px.
  - The stepper becomes a vertical list.
  - Tables become stacked key/value rows.

### 7.3 Action panel states (all on the case page, driven only by API data)
- **A. Refused, the person entered the change** (`can.reasons` non-empty and no console):
  - Callout: `--surface-2` fill, radius 8, a 18px lock icon, title "You can't act on this case" (static UI label) followed by each `permissions.reasons` sentence verbatim.
  - Under it, a disabled primary "Release" button so the moment is visible.
  - This is the R5 demo moment: calm, not red.
- **B. Needs review** (`can_review`): the review console. The case's last-step sentence (`lastStep.summary`) shows above the form in a `--warn-bg` callout.
- **C. Unknown changer, waiting for the second approver:** `firstApproval` callout plus the 1-of-2 bar; the console still shows for a different approver.
- **D. Stale version / any refusal on submit:** `state.error` (the core's message) renders as a `--warn-bg` callout titled "Nothing was changed", with a "Reload" link to the same path.
- **E. Blocked** (`c.state === "blocked"`):
  - No console.
  - A card: title "Case blocked" with the `OutcomeBadge`, then the decision's `effect` (or `lastStep.summary`).
  - Muted static line: "The hold stays on."
- **F. Released** (`c.state === "released"`):
  - A card with an ok badge "Hold lifted" and `when(hold.released_at)`.
  - The schedule dates listed as "Restored".
  - The approving release record (name, time, reason blockquote).
  - A link to Evidence.
- **G. Hold not confirmed:** the `AlertBanners` critical banner at the top, plus the 02 Hold card's bad badge and `last_error` callout.

### 7.4 Number lineage: `src/app/d/[datasetId]/cases/[caseId]/lineage/page.tsx`
1. `PageHead`: back "← {vendor}", eyebrow "Number lineage · {vendor}", title "Why we call {phone(pick)}" (or "Number lineage" when there's no pick), with the existing lead.
2. `Chosen` card:
   - Head marker "CHOSEN", then the phone in mono 22/28, an ok badge "Provable · rank 1".
   - A `dl` grid: Seen in (badges), First seen, Sightings, Alternates (with `view.notes.alternates` muted).
   - The no-pick variant: bad badge "No provable number", `s.reason`, `view.notes.no_provable_number`.
3. Timeline chart (new, display only):
   - A card titled "First seen, by source"; the x axis runs from the earliest `first_seen_at` (rounded down to Jan 1) to `s.cutoff`, with year ticks.
   - One lane per source type present in any candidate: Contract, Vendor portal, Listing, Email ("counts once"), plus a muted "Vendor master" lane ("never counts") for candidates with `on_file`.
   - Dots at each candidate's `first_seen_at` in each of its source types: the pick filled deep, alternates hollow with a 2px `--muted` border, refused filled `--fill-bad`.
   - A dashed vertical line at `cutoff - min_age_days` labeled "{min_age_days}-day minimum"; a `--warn-bg` zone after `cutoff` labeled "Request received".
   - Put the pure placement maths in `lib/lineage.ts` (`lanePoints(selection)`) with tests in `tests/lineage.test.ts`.
   - `role="img"` with an `aria-label` sentence summarizing the pick and the counts.
   - Note in the spec: per-sighting dots wait for the ledger's lineage endpoint.
4. "Every number considered" table, replacing the `ul.candidates` list (keep the class names on the cells):

   | Column | Content |
   |---|---|
   | Number | Mono 13 |
   | Result | chosen (ok), alternate n (info), refused (bad) |
   | Sources | Badges, or "no independent source" |
   | First seen | `seenBefore(...)` |
   | Sightings | `observation_count` |
   | Reasons | `reason_text[r]` sentences, as a plain list |

   On file: a neutral badge "on the old vendor master".
5. Rule footer: one muted 13px row, wrapping: Rule (mono), Cutoff, Minimum, Inputs hash (mono, first 12 characters).

### 7.5 Trust Ledger (vendor risk): `src/app/d/[datasetId]/risk/page.tsx`
1. `PageHead`: eyebrow "Control · posture", title "Trust Ledger", with the existing lead.
2. Count strip, `Stats`-style, four cells in this order: Near miss (bad), Unproven (warn), Missing (neutral), Provable (ok). Values from `counts()`.
3. `RiskMix`: keep the segmented bar and legend; restyle the bar to 10px tall, radius 5, 2px gaps; segment colors from the fills; the percentages appear inside only if ≥10% (existing rule).
4. Table: Vendor (600, with `status_text` under it at 13 muted), Status badge, On file (mono; near-miss digits marked with `<mark class="digit-off">` styled `--bad-bg` / `--bad`, radius 3), Provable (mono). Rows sorted worst first (existing order).
5. Empty state: the existing sentence in a `--surface-2` card.

### 7.6 Evidence: `src/app/d/[datasetId]/evidence/page.tsx`
1. `PageHead`: eyebrow "Control · audit", title "Evidence", lead: the first sentence of the existing lead. Move the rest into the explainer (item 4).
2. Chain status:
   - **Broken:** a full-width card with `--bad-bg` fill, 1px `#f0b8b2` border, radius 12, `role=alert`, and a triangle icon.
     - Title 16/600 `#7a1a12`: "Broken at row #{bad}" / `chainHeadline(chain)`.
     - Then "Postgres says: {detail}" and the existing rows-before sentence.
     - "Jump to row #{bad}" as a secondary pill.
   - **Intact:** a 4-cell strip: Chain (ok badge), Newest row (#tip_seq), Tip hash (mono), Scheduled check (`when(finished_at)` + result).
   - Keep every existing sentence about the recompute and the scheduled check, as 13 muted under the strip.
3. Optional client filter (a small client component, rows passed as props): a select for event type and a select for vendor, filtering visible rows only. Label them visibly ("Event", "Vendor"). Skip it if time is short.
4. The "how the chain works" figure becomes a `<details>` titled "How the chain catches an edit" (closed by default) containing the existing figure and caption.
5. Rows table:
   - Columns: Row (mono, right-aligned), Result badge, Event (`words(type)`), Vendor link, When, prev_hash, content_hash, hash (mono, 10 characters + "…", title full).
   - First bad row: `--bad` inset left bar, `#fdf1f0` tint and "The first bad row" in `--bad` 12px under the badge.
   - Rows after the break: text `#a1a1a6`. Keep the core's per-row words; don't relabel.
   - Keep `id="row-{seq}"`.

### 7.7 Overview (new): `src/app/d/[datasetId]/overview/page.tsx`
- **Data:** `core.allCases`, `core.alerts`, `core.evidence` (for `chain` + `last_check`), `core.datasets`. Nothing else.
- **Elements:**
  1. `PageHead`: eyebrow "Control", title "Overview", lead "{dataset label}. Times in UTC."
  2. Health strip (4 cells):

     | Cell | Content |
     |---|---|
     | Evidence chain | `ChainBadge` |
     | Last chain check | `when(last_check.finished_at)`, or "not run yet" |
     | Holds not confirmed | count of cases with `hold_status === "failed"` (bad if >0) |
     | Open alerts | count by severity |

     Do NOT show webhook, voice or Ramp health (no data).
  3. Cases by state (the wireframe's live state machine):
     - A card with marker "STATE MACHINE".
     - Main row of state nodes connected by "›": Placing hold → On hold → Calling vendor → Deciding → Ready to release → Releasing hold → Released.
     - Second row under the matching nodes: Retry scheduled (dashed border, under Calling), Needs review (warn tint, spans under Deciding/Ready), Blocked (bad tint).
     - Each node: the label (13) and the count (26/32 600, from `allCases` grouped by state).
     - Transition captions in mono 11 muted between nodes (`hold_confirmed`, `number_resolved`, `call_ended`, `decision_made`, `release_approved`, `hold_release_confirmed`). Person-only transitions (`release_approved`, `release_co_approved`, `release_rejected`, `review_cleared`, `review_blocked`) are colored `--accent` with a legend "Only a person can make these moves".
     - Put the node and edge list as a typed constant in `lib/states.ts`, copied from `docs/state-machine.md`, with a test asserting every `CaseState` appears exactly once.
     - Footer sentence (static): "Every failure path ends where the hold stays on. Released is the only finished state."
  4. Alerts (right column): a list of alerts, critical first, each with the `TITLES` label, the core's message, the time and the case link. Reuse the `TITLES` map from `alerts.tsx` (export it).
  5. Held bills by vendor: horizontal bars of `bills_held` per vendor with a confirmed hold. 6px bars on a `--surface-2` track; the `--fill-brand` fill width is proportional; label left, count right.
- Add the nav entry (section 6). Add a link from the queue's KPI strip? No: keep the queue focused.

### 7.8 Assurance: `/corpus` (`src/app/corpus/page.tsx`) and `/scorecard`
These public pages use the public header (7.10), not the dataset shell.
- **`/corpus`:**
  1. A RingBand hero: eyebrow (the existing text), h1 "Which number would Dialback call?", with the existing lead.
  2. The existing note as a neutral callout.
  3. Stats strip: Cases; Matches the frozen truth ("{matched} of {n}", ok); Picked a planted number (bad if >0).
  4. Groups as sections:
     - Group header: h2 + count + `g.lead` muted.
     - Then one compact table PER GROUP replacing the per-case article grid, with columns ID (mono), Pattern (`title`), Vendor, Expected (mono phone, or "no number"), The rule (mono), Result badge ("matches the truth" ok / "differs" bad).
     - The K01 known-limit row: warn tint plus a "Known limit" badge.
     - Clicking a row expands a `<details>` showing the existing per-case detail (planted-number table, the long K01 explanation, problems). Keep `id={c.id}` on the row so anchors still work.
- **`/scorecard`:**
  - RingBand hero with the existing h1 and lead.
  - Below it, a centered empty-state card: a 28px clock icon (muted), "No lab run on this commit yet" (500), and the existing explanation (13 muted).
  - Then the two existing panels ("What the scorecard will show", "What exists today") as plain cards side by side.
  - Never render sample tiles.

### 7.9 Mobile (390px) for every dataset page
- **Shell:** the compact header from section 6.
- **RingBand:** compact variant, h1 32/36.
- **Inbox:** per 7.1. **Case:** per 7.2, with the action panel first and 48px buttons.
- **Tables:** in an `overflow-x:auto` wrapper (`.table-wrap` already exists) except the queue and schedule tables, which become cards.
- **Touch targets:** ≥44px. No horizontal page scroll.

### 7.10 Screens not in the wireframe: design rules they must follow
1. **Public "How it works", `/` (`src/app/page.tsx`, `landing.css`, `components/showcase.tsx`):**
   - **Header:** white, 28px 80px padding; brand, then links (The pipeline, Demo data, Frozen test cases) at 15px `--text-2`; "Open the dashboard" as a primary pill on the right.
   - **Hero, matching the Signature A board:**
     - Full-bleed `--paper` section, 640px tall on desktop.
     - Large rings bleeding off the right: 880/640/400/160px circles in deep/mid/light/paper plus an outline ring 1120px at 25%, sharing one center 180px from the right and 280px from the top.
     - On the left (max-width 720px): eyebrow (13/600 deep), h1 in `--display` at 64/64 (keep the existing headline text), lead 19/28 `--text-2`, two pills ("Open the dashboard" primary, "See the pipeline" secondary).
     - Keep `HeroShot`, but move it below the hero as the first section if it collides with the rings.
   - **Scam steps:** 4 numbered columns. The number in mono 11 marker style (01–04), title 16/600, text 14 muted. No cards; separate them with 1px top rules.
   - **Pipeline stations (`STORY_STATIONS` with `StationShot`):** alternate text and shot left/right. The station marker is the icon in a 28px deep circle. The shots keep the `Frame` structure but get the new card/badge styles.
   - **COMPARE table:** 3 columns (the step, before in muted, with Dialback in `--text`), with rule ids in mono.
   - **Demo stories:** cards with vendor name, a status badge and a link pill.
   - **Footer:** a paper band with the small ring motif and links.
   - No other decoration.
2. **Sign-in and sign-up** (`src/app/sign-in/page.tsx`, `src/app/sign-up/page.tsx`; class `.signin`):
   - A full-height `--paper` page with the hero rings at 50% scale in the top-right corner.
   - A centered white card, 420px, radius 12, `--shadow`, 32px padding: brand, h1 in `--display` 32/36 ("Sign in" / existing text), muted lead.
   - Demo buttons (`.demo-sign-in`, `.demo-buttons`): two full-width pills, approver primary and clerk secondary, each with a 13px muted line of who that role is.
   - Then a hairline divider "or", then the email/password fields: labels 13/500, inputs 40px, radius 8, `--line-strong` border, focus ring.
   - Errors (`.result.error`): a `--bad-bg` callout above the form. Keep every existing message and the `next` handling.
3. **Empty "front door" pages** (`/app` with no datasets, `/demo/[ref]` with no demo): use the sign-in card layout: brand, `--display` h1, the existing text, and links as pills.
4. **Not-found and errors:** if `not-found.tsx` / `error.tsx` exist, give them the same card layout; if not, add `src/app/d/[datasetId]/not-found.tsx` with the card: h1 "Not found", a muted sentence and a link back to `/app`. Server-side errors keep the default handling.
5. **Guided tour** (`TourPanel`, `/tour/[step]`): per section 5.12. The tour must keep landing on the same anchors (`#decision-h`).
6. **Released list, approvals, notes, banners and any other `.note` / `.result` element:** map to the callout pattern:
   - `--surface-2` neutral, `--ok-bg` success, `--warn-bg` warning, `--bad-bg` error.
   - Radius 8, padding 12px 14px, 13/18, an optional 16px icon on the left, an optional 600 title line.
7. **Any page or component not named here** follows the rules:
   - The RingBand at the top (dataset pages and public pages).
   - Cards per section 4, a section marker before every card title, the sans for everything except the h1, mono for data.
   - Status as dot + word badges, pill buttons, no colored side borders.

### 7.11 Live call view (wireframe "Live call split view")
Default: restyle `CallCard` (section 5.9) and stop there.

If the spec approves a presenter variant, render it on the case page behind `?present=call`:
- Full width. The left column (320px) holds a phone frame: a 300×600 white rounded 44px box with a hairline and `--shadow-lift`, showing the AP callback number from `call_script` if provided (else omit it), the call timer from `started_at`, and the current scripted question.
- The right column holds the 7 answer slots from `call_script.questions` as a grid: question / answer box (filled from `answers`, or a dashed empty "Listening…" box).
- RingBand rings centered behind the phone at 900/720/540/360/160px, `z-index:-1`, inside an isolated container.
- Top: brand, vendor, call status badge, timer, and a compact 5-step rail.
- Static only (server-rendered); the Realtime update is an open question.

### 7.12 Flow mode (wireframe "Flow mode, projector")
Only if approved in the spec: render it on the case page behind `?present=flow`.
- A 16:9 dark (`#121211`) full-viewport view.
- The headline "AI collects. Code decides. A person releases." in sans 52/58 600.
- The vendor and bill count in 16 muted, and the chain status top-left.
- The dark RingBand top-right.
- A 5-column station grid (from `progress()`): each a 3px top border (done `--brand-mid`, current white on `#1e1e1c`, todo `#2e2e2b`), the label 22/28 600, a one-line summary from the matching `TimelineEntry.summary`, and the short hash in mono 12.
- No new data.

## 8. Copy and accessibility rules

- **Static labels you may write:** headings, column names, markers, button labels, empty-state titles, the "Hold stays on" line.
- **Never write:** explanation sentences; render them from the API.
- **Sentence case** everywhere except the mono markers. No exclamation marks, no emoji.
- **Contrast:** every text/background pair ≥4.5:1; `--marker` is used only at 11px uppercase for non-essential labels (check it reaches ≥4.5:1 on white and paper; darken to `#77746a` if not).
- **Status:** every status shows a word; color is never the only signal; icons are `aria-hidden` next to words.
- **Keyboard:** all actions reachable, focus visible, `<details>`/`<summary>` for disclosure, real `<button>`, `<a href>`, `<label for>`.
- **Charts:** `role="img"` with a full-sentence `aria-label`; the existing figcaptions stay.
- **Motion:** none under `prefers-reduced-motion`; otherwise only the existing hover/press timings and the rail fill-in.

## 9. Tests and checks

- **Keep green:** every existing test in `apps/web/tests/` (progress, flow, lineage, risk, call, evidence, corpus, tour, story, showcase, lib, auth, vercel-ignore).
- **New tests first, failing, then implement:**
  - `tests/badges.test.ts`: the `STATE_LABEL` map covers all 10 states; an unknown state falls back to `words()`.
  - `tests/states.test.ts`: `lib/states.ts` nodes cover every `CaseState` once; the person-only edges match `docs/state-machine.md`.
  - `tests/lineage.test.ts` (extend): `lanePoints()` places the pick, alternates and refused numbers, the cutoff and min-age line; an empty selection returns no points.
  - `tests/overview.test.ts`: counts by state, holds not confirmed, alerts by severity, from fixture arrays.
  - `tests/activity.test.ts`, if the activity feed derives anything beyond slicing.
- **Commands (paste the output into each PR):**
  - `cd apps/web && pnpm check:api && pnpm typecheck && pnpm test && pnpm build`
  - `python3 scripts/ci/boundaries.py`
  - `uv run python scripts/dev/stack.py --changes`, then `pnpm dev` (environment per `apps/web/README.md`). Walk the demo: `demo reset`, `demo change --vendor acme`, the case as the clerk (refused), as the approver (release), `demo tamper`, and Evidence.
- **Screenshots:** each changed screen at 1440 and 390, next to the matching wireframe, in the PR.

## 10. Open questions to write into the spec (don't guess)

1. Money: add `amount_cents` to ScheduleEntry and `held_cents` to CaseSummary (contracts PR)? Until then the figures show bill counts.
2. 5 vs. 7 stations: extend `progress.ts` to Detected / Held / Number chosen / Called / Decided / Approved / Released?
3. The change-request email on the case page: expose the ledger request document?
4. Per-sighting lineage data: `lineage(vendor_id)` endpoint?
5. Live call: Realtime subscription to `voice.call_answers`, and presenter `?present=call` approval.
6. Flow mode `?present=flow` approval.
7. Overview health: an endpoint for webhook signatures, voice service and Ramp connection?
8. Trust Ledger vendor documents drill-in: endpoint?
9. Evidence export and server-side filters?
10. The seed mismatch: the Ramp mock ends ****0001, the ledger request email says "account ending 4821" (`scripts/demo/seed.json` vs `packages/ledger/src/dialback_ledger/demo_seed.json`).
11. Nav naming: "Inbox" vs "Case queue"; one "Trust Ledger" entry for `/risk`.

## 11. Pull requests, in order

Submission is Nov 10 midday PT. Each PR stays under ~400 changed lines, is reviewed by the path's partner, and gets a `.claude/agents/reviewer.md` review. Never merge.

1. `ui/tokens-shell`: tokens, fonts, `RingBand`, `PageHead`, `Stats`, badges + `STATE_LABEL`, buttons, tables, callouts, `BrandMark`, `NavLinks` and icons, the sidebar shell, `AlertBanners`. (Shell owner: Abhi.)
2. `ui/case-detail`: the case page re-layout, 03 Number card, `CaseRail`, `PaymentPath`, `DecisionConsole`, `DecisionDetail`, the action panel states. (Abhi; call styling with Sami.)
3. `ui/inbox`: the queue, `QueueRail`, mini rail, tabs, `PaymentLanes`, activity feed, mobile cards. (Abhi.)
4. `ui/lineage-risk`: lineage page + `lanePoints` chart, the Trust Ledger page. (Inesh.)
5. `ui/evidence`: the Evidence page, broken/intact states, optional filter. (Abhi.)
6. `ui/overview`: the new Overview route, `lib/states.ts`, tests, nav entry. (Abhi.)
7. `ui/public`: the landing page, sign-in/up, corpus, scorecard, empty front doors, `TourPanel`. (Corpus/scorecard with Sami and Inesh.)
8. Optional, after approval: `ui/presenter` (`?present=call`, `?present=flow`).

Done means:
- The spec's acceptance list is checked.
- All the commands pass.
- The screenshots match the wireframes.
- No explanation sentence is written in the dashboard.
- No full account number or spoken digits appear anywhere.
- No data appears that the API didn't send.
