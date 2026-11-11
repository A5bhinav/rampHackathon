# 0101: The lab's real results on the scorecard page

**Owner:** Sami (`evals/`, `apps/web/`)  **Slice:** call and adversary  **Target day:** Tue Nov 10

## Goal

`/scorecard` is the last stop of the demo tour, and today it says "No lab run on this commit yet".
The lab can already produce results. After this change the page shows two pictures of one real
run:

1. **The call scenarios.** One tile per scripted call: 16 attacks and 15 legitimate-but-odd calls,
   each showing how the call ended.
2. **The fuzz map.** One cell per generated world the fuzz search tried against the number-picking
   rule, colored by whether a check flagged it.

The page keeps its promise: results "never from sample numbers". As with `/corpus` (spec 0038), a
command runs the real lab and writes one JSON file, and the page only lays that file out.

## Decisions

1. **`uv run lab snapshot`** runs the voice scenarios and the archive search (seed 0, 2000
   examples) and writes `apps/web/src/data/lab-results.json`. It imports the code `lab run` and
   `lab fuzz --search archive` run; it does not copy it. It writes no findings and freezes nothing.
2. **The file holds:**
   - `commit`: the short commit it ran on;
   - `voice`: per scenario, its id, class, `attack`, `outcome`, `passed`, the deciding rule, and
     `declared`, the facts its verdict rests on that production doesn't compute yet;
   - `fuzz`: seed, budget, distinct worlds, and for each label the indexes of the worlds it flagged.

   Ids and rule names only: no phone numbers, transcripts or digits.
3. **No CI check that the file is current.** Any rule change would then have to regenerate it, and
   several branches change rules right now. The page states the commit it ran on instead, so a
   stale file says so. Regenerate after a change worth showing.
4. **The tiles.** The tile's color and letter show the ending: V verified, R sent to a person
   (review), B blocked (not verified). A red ring and a "failed" word in its label mark a scenario
   that didn't end as the lab required. Below the grid, each failure is listed in words. A `*`
   marks a call whose verdict rests on a fact the script supplies, because it measures a stand-in
   (spec 0048, A3). The headline stat says how many. Added after the review.
5. **The fuzz map.** Cells run in search order.
   - Clean: no check flagged the world.
   - Red: finding A1, text around the change request could move the number we'd call.
   - Amber: finding A5, a latent defect that can't happen on production data today.
   - An unknown label reads as red, with its raw name.
6. **The page says what the lab is, and what the map is not.**
   - The worlds are randomly drawn and attacker-shaped, and some use non-default settings, so the
     flagged share is not a real-world rate.
   - The calls run the production call flow and decision rules, with the case and number set up
     by the script. The fuzz map runs only the number-picking rule. So the page doesn't say "the
     real pipeline" (corrected after the review).
7. **No client JavaScript.** Static server components, `title` for hover, an `aria-label` per tile
   and one summary for the map. It must fit a phone.

## Contract touched

None. The file is the dashboard's own data, like `corpus-results.json`.

## Acceptance

- [ ] L1. `lab snapshot` writes the file in D2's shape, from a real run. Its counts match `lab run`'s
  scorecard and the archive search's labels.
- [ ] L2. It writes no phone number, digit or transcript.
- [ ] L3. It leaves `evals/corpus-generated` and `evals/out` unchanged.
- [ ] L4. The page groups tiles into attacks and legitimate calls, with the right letter and tone for
  each ending, and rings every failure.
- [ ] L5. Each failure is listed in words below the grid.
- [ ] L6. The fuzz map has one cell per distinct world. A world's cell follows its label, and an
  unknown label reads as red.
- [ ] L7. `/scorecard` and the assurance page show the results and the commit they ran on. The
  "No lab run" empty state is gone, and the page says the flagged share is not a real-world rate.
- [ ] L8. `/scorecard` checked in a browser at desktop and phone width. (The site has no dark theme.
  The assurance page needs a sign-in and was not viewed; it renders the same component.)
- [ ] L9. A call resting on a declared fact is marked on its tile and in its label, and the headline
  stat says how many there are.

## Tests (written first)

| Acceptance item | Test |
|---|---|
| L1, L2, L3 | `evals/tests/test_snapshot.py` |
| L4, L5, L6 | `apps/web/tests/lab.test.ts` |
| L7 | the browser check, by hand |
| L8 | the browser check, by hand |
| L9 | `apps/web/tests/lab.test.ts`, `evals/tests/test_snapshot.py` |

## Out of scope

- The scorecard's other planned numbers (False Verified's upper bound, seed families).
- A live link to CI runs.
- The call-report and document labs. Their numbers could join later.

## Open questions

- **Q217 (Sami):** regenerate the snapshot once spec 0079 lands, since it removes most of the red
  in the fuzz map. Show the before and after, or only the latest?

## Commands

```bash
cd evals && uv run pytest -q tests/test_snapshot.py
cd apps/web && npx pnpm@9.15.0 test && npx pnpm@9.15.0 typecheck && npx pnpm@9.15.0 build
cd evals && uv run lab snapshot
uv run --no-project --with ruff ruff check apps packages scripts corpus evals
python3 scripts/ci/boundaries.py
```
