# 0079: Refuse, don't redirect: the change context can't change who is called

**Owner:** Sami (implementing in `packages/ledger`, Inesh's slice; Inesh reviews as its owner)  **Partner:** Inesh  **Slice:** evidence  **Target day:** not set

## Goal

One change to the number-selection rule, `select()`, under one principle: **the change context may
cancel a call, but may never move it to a different number.**

The change context is the attacker's territory: the change request, its thread, and everything after
the cutoff. Today a label there can veto the vendor's real number and hand the call to a planted one
(FINDINGS A1). An old hijacked thread can do the same through R2 (A3). And the obvious fix, ignoring
the change context's labels, opens a lookalike hole (A6).

This change closes all three by comparing two runs of today's rule:
- one on the case as it is;
- one with the change context removed.

If they disagree about who to call, nobody is called and a person reviews the case.

**The safety property: the new call list is always a prefix of today's.** The call list is the pick,
then up to two backups. The new rule keeps the same numbers in the same order, possibly cut short or
empty. So it never calls a number today's rule wouldn't. Its cost is more cases sent to a person.

It serves R2 (never call a number first seen in the change context), R7 (fail closed) and R1 (never
verify an attack).

**Left out on purpose:** an age limit on trust, and a time limit on a lookalike email's pull (A23).
Spec 0079's first draft had an age limit. Its builder check showed two things:
- at 365 days it drops two demo vendors' picks and one corpus control's;
- one lookalike email undoes it.

The two need one design together, later.

## Contract touched

`packages/contracts/schemas/selection.schema.json`, in its own PR, first (AGENTS.md):
- `outcome` gains `withheld`. Like `no_provable_number`, it requires `reason`, and forbids
  `contact_point_id`, `e164` and `alternates`.
- An example of a `withheld` selection.

`rule_version` takes the next free name when this merges. Spec 0047 has already claimed
`ledger-select-v2` (Q130).

## Decisions

1. **Today's rule is the baseline, unchanged.** It becomes `_select_v1`, and the new rule runs it
   twice.
2. **The pre-context run.**
   - Run `_select_v1` on the same case with every change-context document and its sightings removed:
     the documents `change_context_documents` returns.
   - The change request's record stays, so R7 (nothing is picked with no request) behaves as
     before, but its document's sightings go.
   - The pre-context run keeps today's run's cutoff, as the builder check's prototype did. So the
     comparison isolates what the change context's documents do (their labels and R2), and the
     lookalike pull is left to the later design.
3. **What gets called: the longest common prefix of the two call lists.**
   - **They agree on the pick:** the outcome is `picked`, and the backups are cut to the prefix.
     Nothing dials backups today; this affects only what is stored and shown.
   - **They disagree on the pick:** the outcome is `withheld`, with the reason "The change request
     would change which number is called, so a person must verify."
   - **Today's rule picks nothing:** the outcome stays `no_provable_number`, as today.
4. **Candidates keep today's eligibility.**
   - A `withheld` selection changes no candidate's `eligible`, `rank` or reasons. So `_store` keeps
     each contact point's vendor-wide status as today.
   - So a case withheld here never makes the voice service treat that number as opted out on
     another case.
5. **The core needs no change.** It treats any selection without a `contact_point_id` as "no
   pick", emits `no_provable_number` with the selection's `reason`, and a person reviews. Abhi
   confirms (Q131).
6. **Spec 0047 must not fire on `withheld`.** Its onboarding fallback fires "only when today's rule
   picks nothing". It must read `no_provable_number` only; firing on `withheld` would move the call
   at the system level.
7. **A kill switch.** The ledger's flag `DIALBACK_CONTEXT_GUARD`, read in `flags.py`, is on by
   default. Any unknown value means on. Off runs today's rule alone, for rollback only.

## The measurement (fixed before any run)

It runs on:
- the 20-scenario corpus;
- the 3 demo vendors (the seed run through `ingest_all`; no database needed);
- spec 0033's frozen worlds;
- 2000 generated worlds at seed 0, with spec 0048's pinned draws;
- the frontier and over-block grids.

**Structural, and gated.** These hold by construction, so they catch bugs in the new code and drift
in today's rule, not design mistakes.
- **The prefix property.** The new rule's call list is a prefix of today's on every world above.
  "Today's" means a frozen copy of `select()` as it is on main when this branch starts, kept as a
  test fixture. So a later change to `_select_v1` can't hide behind its own baseline.
- **I12 holds on every generated world.**

**The fixes, gated.** For each case below, the lab's known failure stops firing, and the planted
number is never called:
- A1's three frozen cases (`planted-number-picked-after-*`);
- A3's over-block rows (`in_thread_copy` at 365 and 730 days, planted family);
- the A6 lookalike-hole world, with each of the lab's lookalike probes (30, 100 and 400 days).

**No change where nothing is wrong, gated:**
- all 20 corpus outcomes are unchanged;
- every demo vendor keeps its pick.

**The cost, reported with no bar:** the share of worlds where today's rule calls and the new rule
withholds, on the generated worlds and on the over-block sweep's honest rows.
- The generated worlds' dates are fixtures, so their share is not a real-world rate.
- The A6 world with the 30- and 100-day probes is a known cost: today's rule calls the real number
  there, and the new rule withholds.

**From the builder check's prototype** (exploratory, to be confirmed by the real code): the change
alone changed no demo pick and none of the 20 corpus outcomes.

## What this changes elsewhere

- **The contract:** the `withheld` outcome and its example, in their own PR first. If the core's
  OpenAPI exposes a selection's outcome, it grows too, and `packages/contracts/generated/ts` is
  regenerated.
- **The web:** the lineage panel and the corpus page show `withheld` and its reason.
  `apps/web/src/data/corpus-results.json` is regenerated (unchanged, per the prototype).
- **The risk list** (`risk.py`) runs `select()`. A vendor whose latest case is withheld reads as
  "needs a person", not as unproven.
- **The lab** (follow-ups on the lab branches):
  - A1's cases leave the known-failures list;
  - I12's frozen cases pass;
  - the lab's selection invariants learn the new outcome.
- **Spec 0047:** its fallback reads `no_provable_number` only (decision 6).
- **Manual reviews:** more of them, by the share the measurement reports.

## Acceptance

- [ ] The new rule's call list is a prefix of the frozen copy's on every world in the measurement,
  and a version that skips the comparison fails that test.
- [ ] A1's three cases, A3's rows and the A6 world end as decision 3 says, and their known failures
  stop firing.
- [ ] All 20 corpus outcomes and the 3 demo picks are unchanged.
- [ ] A `withheld` selection validates against the new schema, changes no candidate's eligibility,
  and `_store` writes each contact point's status as today.
- [ ] The core turns a `withheld` selection into `no_provable_number` with its reason, and a person
  reviews.
- [ ] The flag set off gives today's rule exactly. Its default, and any unknown value, give the new
  rule.
- [ ] The measurement's reported numbers are written into a results section of this spec.

## Tests (written first)

| Item | Test |
|---|---|
| The prefix property | `packages/ledger/tests/test_select_properties.py::test_new_rule_is_a_prefix_of_frozen_v1` (with `packages/ledger/tests/fixtures/select_v1_frozen.py`) |
| The fixes | `packages/ledger/tests/test_select_guard.py::test_change_context_label_is_withheld` (A1), `::test_old_thread_copy_is_withheld` (A3), `::test_lookalike_hole_never_calls_the_plant` (A6), `::test_context_that_moves_nothing_changes_nothing` |
| Nothing else changes | `packages/ledger/tests/test_select_corpus.py` (unchanged outcomes), `packages/ledger/tests/test_demo_picks.py` |
| Storage | `packages/ledger/tests/test_select_guard.py::test_withheld_keeps_contact_point_status` |
| Core | `apps/core/tests/test_worker.py::test_withheld_selection_goes_to_review` |
| Flag | `packages/ledger/tests/test_flags.py::test_context_guard_defaults_on` |
| Contract | `python3 packages/contracts/scripts/validate.py` with the new example |

## Out of scope

- An age limit on trust, and a time limit on a lookalike email's pull (A23). One later design.
- A2 (honest mentions refused). The LLM reader (spec 0049) addresses most of it.
- Any change to ingestion, the reader or the call.

## Open questions

- **Q130.** `rule_version` takes the next free name when this merges. Spec 0047 has claimed
  `ledger-select-v2`, so whichever lands second takes the next one (my call).
- **Q131 (Inesh and Abhi).** Inesh reviews the rule as its owner. Abhi confirms the core needs no
  change for `withheld` (decision 5).
- **Q132.** The age limit and the lookalike time limit are deferred, together (my call).

## Commands

```bash
cd packages/ledger && uv run pytest -q
cd apps/core && uv run pytest -q
uv run --with jsonschema --with rfc8785 --with pyyaml --with openapi-spec-validator python packages/contracts/scripts/validate.py
uv run lab fuzz --examples 2000 --seed 0
uv run lab overblock --out evals/out/overblock.json
```
