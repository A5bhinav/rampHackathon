# 0079: Refuse, don't redirect

**Owner:** Sami (implementing in `packages/ledger`, Inesh's slice; Inesh reviews as its owner)  **Partner:** Inesh  **Slice:** evidence  **Target day:** not set

## Goal

Two changes to the number-selection rule, `select()`, under one principle: **a new check may cancel
a call, but may never move it to a different number.**

1. **The change context can't change who is called.**
   - It fixes FINDINGS A1: a label in the change email vetoes the real number, and the call moves
     to a planted one.
   - It fixes A3: an old hijacked thread makes R2 refuse the real number.
   - It fixes the lookalike hole in A6.
2. **A number must have been seen recently to be called.** This fixes A23's first point: a planted
   number otherwise sleeps, trusted forever, until the fraudster needs it.

**The safety property: v2's call list is always a prefix of v1's.** The call list is the pick, then
up to two backups. v2 keeps the same numbers in the same order, possibly cut short or empty. So v2
never calls a number today's rule wouldn't. The cost is more cases sent to a person, and this spec
measures it.

**Not included: a time limit on a lookalike email's pull** (A23's second point). Any simple version
either keeps the over-block, by sending old lookalikes to a person, or opens a hole: numbers planted
after an old lookalike email would start counting as clean. It needs its own design.

It serves R2 (never call a number first seen in the change context), R7 (fail closed) and R1 (never
verify an attack).

## Contract touched

`packages/contracts/schemas/selection.schema.json`, in its own PR, first (AGENTS.md):
- **Three new `Reason` values:** `change_context_moves_call`, `not_seen_recently` and
  `backup_withheld`.
- **A new `rule_settings` field:** `recent_days`.
- **Examples:** one example per new reason.

`rule_version` becomes `ledger-select-v2`.

## Decisions

1. **Today's rule is the baseline, unchanged.** It becomes `_select_v1`. v2 runs it, then only cuts
   its call list.
2. **C1: the change context can't change who is called.**
   - **The pre-context run:** run v1 again on the same world with every change-context document and
     its sightings removed (the documents `change_context_documents` returns). The change request's
     record stays, so the cutoff and R7 behave as before, but its document's sightings go.
   - **What v2 keeps:** the longest common prefix of v1's call list and the pre-context call list.
   - **Every number v1 called that v2 drops** gets `change_context_moves_call`. If nothing is left,
     the outcome is `no_provable_number`, and a person reviews the case.
3. **C2: recent evidence.**
   - **The new setting:** `recent_days`, 365 by default (Q130). A number in the call list with no
     clean sighting within `recent_days` before the cutoff gets `not_seen_recently`.
   - **The cut:** the call list is cut at the first such number, and every number after it gets
     `backup_withheld`. So a stale pick never hands the call to a fresher backup.
   - **What it doesn't stop:** a freshly planted number (K01 inside the window) still qualifies.
     C2 only defeats sleepers.
4. **The order:** C1, then C2 on what C1 kept. The final list is a prefix of v1's.
5. **Wording.**
   - The ledger's reason sentences (`reasons.py`) and the web's reason chips cover the three new
     reasons.
   - The timeline still says no provable number was found and a person must verify.

## The measurement (fixed before any run)

These run on the lab's existing, deterministic tools:
- the 20-scenario corpus;
- spec 0033's frozen worlds;
- 2000 generated worlds at seed 0, with spec 0048's pinned draws;
- the frontier and over-block grids.

**Gated, with 0 violations allowed:**
- **Safety:** on every world above, v2's call list is a prefix of v1's.
- **The fixes:** each of these makes v2 call nobody:
  - A1's frozen cases;
  - A3's over-block rows (`in_thread_copy` at 365 and 730 days);
  - the hand-built lookalike-hole world from A6.
- **I12:** it holds on every generated world under v2.
- **Controls:** corpus controls C01 to C05 keep their picks.
- **The demo:** every demo vendor keeps its pick. If one doesn't at 365 days, Sami decides the
  default with Inesh (Q130). The bar doesn't move silently.

**Reported, with no bar:**
- **The cost:** the share of worlds where v1 calls and v2 doesn't, split by C1 and C2. It covers
  the generated worlds, the over-block sweep's honest rows, and the corpus.
- **The attacker's cost:** the frontier's minimum effort under v2 next to v1's. It can only rise.

## What this changes elsewhere (the full list)

- **The contract:** three new reasons and one setting, in their own PR first.
- **The corpus's frozen truth:** any scenario whose expected outcome changes is listed. A person
  updates `FROZEN_TRUTH_SHA256` on purpose (spec 0020), with Inesh's review.
- **The lab (follow-ups on the lab branches):**
  - A1's frozen cases leave the known-failures list.
  - L1, the over-blocking floor, adds recency to its premise, since a clean world must now also
    have a recent sighting.
- **The web:** the three new reason chips.
- **Manual reviews:** more of them, by the amount the measurement reports.

## Acceptance

- [ ] `select()` returns v1's call list cut short, or no pick. A property test over the ledger's
  own world strategy (`test_select_properties.py`) shows this, and fails on a version that skips
  the cut.
- [ ] Each of decision 2's and decision 3's reasons is set exactly where the spec says, and a
  backup is never called when the pick was cut.
- [ ] Every gated bar in the measurement passes. Every reported number is written into this spec's
  results section.
- [ ] The contract PR lands first. Its examples validate, and `rule_version` is
  `ledger-select-v2`.
- [ ] The corpus truth changes, if any, are listed and re-frozen with Inesh's review.

## Tests (written first)

| Item | Test |
|---|---|
| The prefix property | `packages/ledger/tests/test_select_properties.py::test_v2_is_a_prefix_of_v1` |
| C1 | `packages/ledger/tests/test_select_v2.py::test_change_context_label_cancels_the_call` (A1's shape), `::test_old_thread_copy_cancels_the_call` (A3's), `::test_lookalike_hole_cancels_the_call` (A6's), `::test_context_that_moves_nothing_changes_nothing` |
| C2 | `test_select_v2.py::test_stale_pick_is_not_replaced_by_a_backup`, `::test_stale_backup_is_dropped`, `::test_fresh_plant_still_qualifies` (K01 stays a stated limit) |
| Corpus and demo | `packages/ledger/tests/test_select_corpus.py` (controls keep their picks), `packages/ledger/tests/test_demo_picks.py` (every demo vendor keeps its pick) |
| Contract | `python3 packages/contracts/scripts/validate.py` with the new examples |

## Out of scope

- A time limit on a lookalike email's pull (A23's second point). Measure it first; it needs its own
  design.
- Fixing A2 (honest mentions refused). The LLM reader (spec 0049) addresses most of it.
- Any change to ingestion or the reader.

## Open questions

- **Q130 (Sami, with Inesh).** The default `recent_days` is 365. If a demo vendor or a corpus control
  loses its pick at 365, which wins: a longer default, or refreshed demo documents? Decided before
  the default ships.
- **Q131 (Inesh).** Sami implements this in `packages/ledger`. Inesh reviews it as the owner, and
  approves the corpus truth change.
- **Q132.** The lookalike time limit is deferred (my call). The lab's sweeps measure it first.

## Commands

```bash
cd packages/ledger && uv run pytest -q
uv run --with jsonschema --with rfc8785 --with pyyaml --with openapi-spec-validator python packages/contracts/scripts/validate.py
uv run lab fuzz --examples 2000 --seed 0
uv run lab overblock --out evals/out/overblock.json
uv run lab frontier --out evals/out/frontier.json
```
