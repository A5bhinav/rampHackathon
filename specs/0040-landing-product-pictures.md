# 0040: The landing page shows the product at each station

**Owner:** Abhi  **Slice:** dashboard (public page)  **Target day:** Nov 8  **Builds on:** 0033

## Goal

The "How it works" page explains each station in words only. A judge should see what the
product looks like at each step without signing in. Give each station a small, finished
picture of the dashboard next to its text, and give the hero a picture of the case queue. The
page keeps its layout, colours and type (specs 0031, 0032). The design brief for this change
sets only a feel: crisp, detailed product views with soft depth, kept orderly and plain.

## Decisions

1. **One idea, used everywhere:** every station is a text-and-picture pair. The picture sits on
   a soft tinted tile and shows one raised product card, so all seven read as one set. The
   pairs follow the existing rail, top to bottom; on a phone the picture goes under the text.
2. **The pictures are drawings, built from the demo seed and the product's own words:** the
   demo company and vendors (`scripts/demo/seed.json`), the ledger's sightings of Acme's
   number (`packages/ledger/.../demo_seed.json`), the call script's lines
   (`packages/policy/.../script.py`), the decision rules' sentences and the core's effect
   sentence. A caption above the stations says they are drawings of fictional demo data, not a
   live case. No figure the system computes (counts, rates, scores) appears in any picture.
3. **ADR-8 holds in the drawings too:** bank accounts show a routing number and the last four
   digits only. The digits the vendor gives on the call show as "4 digits given". Phone
   numbers are the seed's fictional 555-01xx numbers.
4. **Depth without new colours:** the existing tokens only (`--surface`, `--surface-2`,
   `--accent-soft`, `--line`, the status tones) and a softer, layered card shadow. No
   gradients from the references, no glow, no glass.
5. **Motion:** none beyond what the page already has. Pictures are `aria-hidden`; the station
   text already says what each shows.

## Contract touched

None.

## Acceptance

- [ ] Every station has a picture, and the hero shows the three demo cases.
- [ ] No picture holds a digit run longer than four, except a 9-digit routing number labelled
      as one; every phone number is a 555-01xx number.
- [ ] A caption tells the reader the pictures are drawings of demo data.
- [ ] Works at 1440 and 390 with no horizontal scroll.
- [ ] `pnpm test && pnpm check:api && pnpm typecheck && pnpm build` pass.

## Tests (written first)

| Acceptance item | Test |
|---|---|
| 1, 2 | `apps/web/tests/showcase.test.ts` |
| 3, 4 | Checked by eye at 1440 and 390 |

## Out of scope

Pages behind sign-in; the corpus, evidence and tour pages (open PRs #77, #78, #81).

## Open questions

None.

## Commands

```bash
cd apps/web && pnpm test && pnpm check:api && pnpm typecheck && pnpm build
python3 scripts/ci/boundaries.py
```
