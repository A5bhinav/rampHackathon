# 0063: Front door and the two apps

**Owner:** Abhi  **Slice:** money path (presentation only)  **Target day:** Nov 10

Plan v3 (`docs/ui-build/dialback-ui-build-plan.pdf`, sections 2 and 3) numbers this spec 0049. That
number and 0050–0052 were already taken (Sami's 0049, 0051, 0052; 0050 on main), so the whole plan
shifts: phase 1 is **0063** (this), 0064 seven stations, 0065 the phone demo, 0066 the guided demo
bar and density pass, 0067 the System map; phase 2 is 0068–0075; phase 3 is 0076.

## Goal

The dashboard becomes the product the wireframes describe: a one-screen front door with three ways
in, and two apps that share the Signature A design system (spec 0041). **AP workspace** is for clerks
and approvers (Inbox, Cases, Vendors). **Control** is for controllers and auditors (Overview, Trust
Ledger, Evidence, Assurance, then Settings: Rules & policy, People, Integrations). A switch at the top
of the sidebar moves between them. Web only, on today's API, with no contract change.

## Contract touched

None.

## Acceptance

Front door
- [ ] `/` is one screen: the brand, How it works and Sign in in the header, the headline, one sentence, three buttons ("Watch the demo" → `/tour/1`, "AP workspace" → `/app`, "Control" → `/app/control`), and the seven stations as icons with one word each.
- [ ] The long page is gone: the scam walkthrough, the comparison table, the station drawings, "what is real today" and the demo-story cards, with `components/showcase.tsx`, `lib/showcase.ts`, `STORY_STATIONS`, `ACROSS` and their tests. Public `/corpus` stays.
- [ ] `/app/control` opens the newest dataset's Control overview, signing in first and coming back. Until the System map lands (0067), "How it works" goes there too.

Two apps
- [ ] The dataset shell splits into an AP shell and a Control shell (one `Shell` component, two layouts). Same data (`core.me`, `core.datasets`, `core.alerts`) and the same not-found behaviour.
- [ ] AP sidebar: Inbox (`/d/{id}/cases`), Cases (`/d/{id}/cases/all`), Vendors (`/d/{id}/vendors`). The utility bar keeps breadcrumbs and "Viewing as".
- [ ] Control sidebar, with a "Control" badge by the brand: Overview (`/d/{id}/control`), Trust Ledger (`/control/trust-ledger`), Evidence (`/control/evidence`), Assurance (`/control/assurance`), then a Settings group: Rules & policy (`/control/rules`), People (`/control/people`), Integrations (`/control/integrations`).
- [ ] Both sidebars open with an "AP workspace | Control" switch that keeps the dataset. Control is offered to everyone on demo datasets, and to approvers and auditors elsewhere (a display choice; the API decides what each person can read).
- [ ] `/d/{id}/overview`, `/risk` and `/evidence` redirect (temporary, query kept, so `?tour=N` survives) to their Control routes. Links inside the app point at the new routes.
- [ ] Cases: every case from `core.allCases`, with state tabs (All, Needs you, In progress, Released) as links that filter on the server.
- [ ] Vendors: every vendor from `core.vendorRisk`, with its number on file, its Trust Ledger status and its open case (from `core.cases`), linked.
- [ ] Assurance: the corpus table and the scorecard empty state inside the Control shell (shared components with the public pages).
- [ ] Rules & policy: R1 to R9 with where each is enforced (fixed product copy), the call's disclosure and questions from the newest case's `call_script`, and the selection rule's version and settings from the newest case's selection; "not reported yet" for the full rule table.
- [ ] People: the signed-in person and their roles, the two demo accounts behind "Viewing as" when demo sign-in is on; "not reported yet" for everyone else.
- [ ] Integrations: Ramp (the dataset's kind), the evidence chain and its last check, open alerts; webhooks and the voice service say "not reported yet".

Never
- [ ] No sentence explaining a decision, refusal or status is written by the dashboard; no data the API didn't send; no full account numbers or spoken digits. Every anchor, `aria-*`, `role` and `sr-only` stays.
- [ ] Failure paths: a Settings page with no cases, no selection or no call script still renders and says what isn't reported yet; a selection or case fetch that fails there is treated as missing, never an error page.

## Tests (written first)

| Acceptance item | Test |
|---|---|
| The two navs list exactly the board's items, with the switch and its targets | `apps/web/tests/apps.test.ts` |
| The redirects (old route → new route, every dataset) | `apps/web/tests/apps.test.ts` |
| Front door: three doors, seven one-word stations, the removed sections and files gone | `apps/web/tests/front-door.test.ts` |
| Settings content with no case data says what isn't reported yet | `apps/web/tests/settings.test.ts` |
| Every guided-demo link resolves to a page or a redirect | `apps/web/tests/tour.test.ts` (added test; existing ones unchanged) |
| `/app/control` survives sign-in | `apps/web/tests/story.test.ts` |

## Out of scope

The seven-station model (0064), the phone demo (0065), the guided demo bar and density pass (0066),
the System map (0067), and every phase 2 field. The guided demo keeps its current eight steps and
links; the old routes redirect, so they keep working.

## Open questions

1. Should clerks see Control? Default taken: everyone on demo datasets, approvers and auditors elsewhere.
2. The plan's spec numbers collided; confirm the shift to 0063–0076 and update the plan's PDF.
3. "How it works" in the sidebars: left out until the System map (0067) exists, rather than linking to a page that isn't an explanation.

## Commands

```bash
cd apps/web && pnpm check:api && pnpm typecheck && pnpm test && pnpm build
python3 scripts/ci/boundaries.py
```
