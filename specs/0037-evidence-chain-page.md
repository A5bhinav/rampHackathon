# 0037: The evidence chain page

**Owner:** Abhi  **Slice:** evidence (dashboard)  **Target day:** Nov 8  **Builds on:** 0035

## Goal

Beat 6 of the demo (spec 0011): someone edits one evidence row by hand, and the system points
straight at it. Today the only sign is a badge and a banner. This page shows the dataset's chain
row by row, so the broken row is impossible to miss, and explains in two sentences why an edit
can't hide (R6). It renders `GET /datasets/{id}/evidence` and decides nothing.

## Decisions

1. **Route** `/d/[datasetId]/evidence`, linked from the shell's nav ("Evidence") and from the
   chain badge on every case.
2. **Lead card:** intact (how many rows Postgres just recomputed, the tip hash) or broken (the
   first bad row, the core's detail sentence, a link that jumps to the row), plus when the
   scheduled check last ran and what it found.
3. **How it works:** a three-row drawing of the links (each row's hash is computed from the one
   before), in words beside it.
4. **The rows**, newest first: seq, event, vendor (linking to its case), time, and the three
   hashes shortened (full value on hover), with the per-row result as a word: "intact", "body
   edited" (`content_ok` false), "link broken" (`link_ok` false). The first bad row the chain
   check names gets a red stripe and an anchor. Rows after it read as their own per-row result,
   with a caption saying the scheduled check stops at the first break.
5. Only the newest 500 rows are listed; the page says how many there are in total.

## Contract touched

None (0035 added the endpoint).

## Acceptance

- [ ] An intact chain says so with the row count and tip; every row reads "intact".
- [ ] After `demo tamper`, the lead card is red, names the row, and links to it; that row reads
      "body edited" with a stripe; status is always a word.
- [ ] The nav and the chain badge link here; an unknown dataset is a 404.
- [ ] Works at 390px (the hash columns scroll inside the table).

## Tests (written first)

| Acceptance item | Test |
|---|---|
| 1, 2 | `apps/web/tests/evidence.test.ts` (`rowStatus`, `chainHeadline`) |
| 2–4 | Checked by eye on the local stack before and after a hand edit |

## Out of scope

Paging past 500 rows; the GitHub witness of the chain tip.

## Open questions

None.

## Commands

```bash
cd apps/web && pnpm test && pnpm check:api && pnpm typecheck && pnpm build
python3 scripts/ci/boundaries.py
```
