# 0033: A public "How it works" page

**Owner:** Abhi  **Slice:** money path (dashboard)  **Target day:** Nov 8  **Builds on:** 0031, 0032

## Goal

Today the dashboard opens on a sign-in form and never says what problem it solves. A judge
should land on a page that explains the fraud, shows how Dialback changes the old callback,
walks through each station of the pipeline with what it stops, and leads into the demo data.
The page is static: it calls nothing, decides nothing (R3), and states what isn't built.

## Decisions

1. **`/` is the public page; `/app` does what `/` did** (opens the newest dataset, or says
   there is none). Sign-in and sign-up land on `/app`. The shell gets a "How it works" link.
2. **The problem, in plain words**, with no loss figure: the repo's design doc lists "FBI IC3:
   Business email compromise" as a source but quotes no number, so the page quotes none.
3. **Old way vs Dialback**, row by row, each Dialback cell naming the rule (R1–R9) it rests on.
4. **Seven stations** (bank change, detect, hold, pick a provable number, disclosed AI call,
   code decides, a second person releases), each with what happens and what it stops, reusing
   the rail's icons and the same wording as the queue's rail. Then the cross-cutting pieces:
   Postgres as referee, the hash-chained evidence log, fail closed, text is data.
5. **What's real**: Ramp is a Ramp-shaped mock; the attack lab is a README only, so there is
   no scorecard; the number-picking rule has 20 frozen planted cases in `corpus/`.
6. **The demo stories**: the three seeded vendors with what each is set up to show (spec 0011,
   decision 3), and a link per vendor, `/demo/<ref>`, that signs the visitor in if needed and
   opens that vendor's case on the newest demo dataset. The page says that the deployed demo's
   outcomes come from whatever happened on its calls, not from this page. One-click demo
   accounts are spec 0034.

## Contract touched

None. `/demo/<ref>` uses `GET /api/datasets` and `GET /api/cases` with every state.

## Acceptance

- [ ] `/` renders without a session and without calling the core; it links to `/app`.
- [ ] `/app` opens the newest dataset's queue; with no dataset it says what creates one.
- [ ] Signing in or up lands on `/app`; the shell links back to `/`.
- [ ] Seven stations in order, each with a "what it stops" line; from the hold on, each names the rule it rests on
      (the bank change and detection come before any rule applies).
- [ ] `/demo/acme` opens Acme's newest case on the newest demo dataset, signing the visitor in
      first and coming back; with no demo dataset it says so, and with no case for that vendor it
      opens the queue with a sentence saying why; an unknown ref is a 404. After sign-in only a
      known `/demo/<ref>` is followed (no open redirect).
- [ ] No figure the system didn't compute; status only as a word; works at 390px.

## Tests (written first)

| Acceptance item | Test |
|---|---|
| 4 | `apps/web/tests/story.test.ts` (seven stations, each with a rule and a stop line) |
| 5 | `apps/web/tests/story.test.ts` (`newestDemo`, `caseFor`, `safeNext`); the page's fallbacks by eye |
| 1–3, 6 | Checked by eye at 1440 and 390 on the local stack |

## Out of scope

One-click demo accounts (0034); the call view, the checklist, the evidence page (later specs).

## Open questions

- Does the deployed stack run the voice service? If not, every demo call is refused before
  dialing and goes to review; the page doesn't promise otherwise.

## Commands

```bash
cd apps/web && pnpm test && pnpm check:api && pnpm typecheck && pnpm build
python3 scripts/ci/boundaries.py
```
