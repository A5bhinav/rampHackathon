# 0033: The attack lab, generative selection pressure

**Owner:** Sami  **Slice:** call and adversary  **Target day:** after the voice slice (0017); the first generative cut, grown before the full lab's statistics.

## Goal

The attack lab (`evals/README.md`) is meant to measure how often Dialback is fooled, but today the only adversarial pressure on number selection (`ledger-select-v1`, spec 0021) is the frozen corpus (`corpus/`, spec 0020): 20 hand-authored cases scored by `test_select_corpus.py`. A green run there proves the rule matches those 20 examples, not that it is robust. Each case stresses one defense, and nothing searches for the input that breaks it. Selection is where the fraud decision actually lives (which number Dialback will trust and call, R2), so it is both the highest-value target and the one that can be tested exhaustively rather than by example.

This spec adds a generative layer to the lab that puts `select()` under adversarial search, in three parts. First, a world generator and an invariant oracle: thousands of generated adversarial worlds (documents, sightings, a change request) run through `select()`, and a set of properties that must hold for every world are asserted; any counterexample is shrunk to a minimal world and frozen as a named case, so the lab grows its own regressions from real findings. Second, a known-limit frontier sweep: the one K01 "known limit" (a number pre-seeded across channels, spec 0020) is generalized into a parametric sweep that measures the minimum attacker effort (channels, lead time, labels) that defeats the rule, turning a single accepted anecdote into a quantified residual-risk curve. Third, an over-block sweep, the cost side of the same rule: one real, established vendor number plus exactly one piece of attacker noise, swept across noise type and age, measuring when that noise alone makes the genuine number unprovable. The frontier says what an attack costs the fraudster; the over-block sweep says what each defense costs a legitimate vendor (a manual review instead of a verification call).

It imports only the pure `dialback_ledger.select` function (no database, core, network, model or live account), so it runs today and can gate selection-rule changes. It changes no rule and no contract: a property that fails is a finding handed to the rule's owner (`packages/ledger`, Inesh), never an edit from the lab.

## Contract touched

None. The generator, invariants, and the frontier and over-block reports are lab-internal (`evals`). The selection output shape the lab reads is already frozen in `packages/contracts/schemas/selection.schema.json` (owned elsewhere); this spec consumes it and changes nothing. Promoting any generated-corpus, frontier or over-block shape to `packages/contracts/schemas` is a later, separate contract PR.

## Why this is worth adding over more corpus cases

- `select()` is pure (spec 0021): sightings and their documents' custody in, a `selection.schema.json` document out. No database, clock, network or model. So it can be called thousands of times in-process, which is what property-based search needs.
- The rule's whole contract is a short list of provability tests (`first_seen_in_change_context`, `too_few_source_types`, `younger_than_minimum_age`, `near_miss_of_older_number`, `hidden_text_only`, `not_vendor_party`, `fax_line`). Each is a universally-quantified claim ("no number that is X is ever eligible"), which is a property, not an example. A frozen corpus can only sample these; a generator plus the property closes the gap.
- The one documented hole (K01) is a single point on a curve the corpus never draws. The same generator that fuzzes the rule can sweep that curve.

## Decisions (confirmed)

1. **The generator emits `select()` inputs, nothing more.** A Hypothesis strategy builds a world: a vendor (domains); an optional change request (a missing one means the change context is unknown); the rule's settings (the v1 defaults, or other values inside the schema's ranges: `min_source_types` 1 to 3, `min_age_days` 0 to 180, `near_miss_distance` 0 to 2, `same_subject_window_days` 0 to 90); and a set of candidate numbers. Each number has one or more sightings, one per document, and each sighting carries its own attacker knobs, so one number can be visible in one document and hidden in another, labeled the vendor's in one and a bank's in another, or seen long ago and again last week. The knobs: channel, lead time before the request, visibility (`visible` / `white` / `tiny` / `off_page`), role, party, sender (the vendor's own domain, a vendor subdomain, a homoglyph or combosquat lookalike, an unrelated domain), thread (in or out of the request's), subject (unrelated, the request's, or a reply or forward of it with `Re:` / `Fwd:` / `[EXTERNAL]` prefixes), whether the document was recorded after the case opened, whether a second, different document shares its id, and whether that second document sorts first (so the rule reads it instead). Some numbers are drawn as deliberate near-miss siblings of another number in the world (one or both of the last two digits changed, or those two swapped). Every number is NPA-555-0100 to 0199, the block reserved for fiction, and unrelated numbers take area codes that differ in all three digits, so they are never near misses by accident (review C1). It builds the public dataclasses `dialback_ledger.select` exposes (`DocumentRef`, `Sighting`, `ChangeRequest`) directly, the same shapes the corpus harness builds, so a generated world and a corpus world are indistinguishable to the rule. No import from another slice's test helpers. A second strategy draws clean worlds (no attacker knob set at all) for the liveness floor L1.

2. **The oracle is a set of invariants, asserted on every generated world.** These are the rule's own guarantees restated as universals (each holds for all worlds):
   - I1 (R2): a number first seen in the change context (its earliest visible sighting is in context, ties counting as context) is never eligible, even when clean sightings follow.
   - I2: a number with no visible sighting (hidden-text-only) is never eligible.
   - I3: a number with any visible non-vendor party, or a fax role, is never eligible.
   - I4: a number seen in fewer than `min_source_types` counting channels is never eligible.
   - I5: a number whose first clean sighting is younger than `min_age_days` before the cutoff is never eligible.
   - I6: the picked number (if any) has an empty reason set, and the output validates against `selection.schema.json`.
   - I7 (the adversarial core): no eligible number is one the near-miss rule would flag (an older or same-time cleanly seen number within the near-miss distance, unless both are past the established line). With I1-I5 this means a poison number is picked only when it independently clears the known-limit bar.
   - I8: determinism. The same world run twice gives the same output and the same `inputs_sha256`.
   - I9 (R7): with no change request, nothing is picked.
   - I10 (replay): documents and sightings recorded after the case opened never change the output; removing them gives the same output and the same `inputs_sha256`.
   - I11: adding a second, different document under an existing document's id never makes any number eligible.
   - L1 (liveness, the over-blocking floor): in a clean world (every document from the vendor's own domain or a subdomain, outside the request's thread, with an unrelated subject, visible, vendor-labeled, not fax, no near-miss pairs, nothing recorded late, no shared ids, a request present), if some number is seen in at least `min_source_types` counting channels at least `min_age_days` before the cutoff, a number is picked. Every safety invariant above passes on "no pick"; this one does not.

   The structural checks (I1-I7, I9) recompute the rule's frame from raw inputs. Where no document id is shared they follow select() exactly, so there they catch regressions rather than design defects; where two different documents share an id the reference is conservative, as R7 asks (change context, a lookalike if any copy is one, dated by its earliest copy). The metamorphic checks (I8, I10, I11, probes showing attacker evidence never makes a number eligible, and a probe showing an older in-context sighting blocks a number) and L1 are where current defects show.

3. **A counterexample becomes a regression, not a silent retry.** When Hypothesis finds a violating world it shrinks it to a minimal one, the lab writes it out as a named case under a lab-owned generated-corpus directory (a `world.json` holding the select() inputs field for field, readable next to `corpus/`; the corpus's own shape has no `recorded_at`, so it could not hold a late-recorded document), and the run fails. The minimal world is the finding handed to the rule's owner; the frozen case keeps it from recurring. The lab never edits `select()`.

4. **The frontier sweep is analysis, not a gate.** A parametric K01-shape world (the vendor's own number seen only by email, so never provable alone, plus a poison number seeded with `channels in {1,2,3}`, `lead_days in {0,30,60,90,180,365}`, label set, visibility) runs through `select()` across the grid; each cell records whether the poison is picked, eligible, or rejected and by which reason. The output is a frontier table (`frontier.json` plus a readable summary): the boundary between "rejected" and "picked". It asserts only the one invariant that must hold everywhere (a poison below the `min_source_types` / `min_age_days` thresholds is never picked); the rest is measurement.

5. **The over-block sweep is analysis, not a gate.** One real, established vendor number (visible, vendor-labeled, two counting channels, first seen 200 days before the request, which is younger than the 365-day established line so an older near-miss sibling can still block it) plus exactly one piece of noise, swept across noise type (a lookalike-domain email; a copy of the real number in an old in-thread message; a copy in a same-subject reply; a near-miss sibling; a hidden copy; a bank-labeled copy; the attacker's own in-thread message naming the real number as a bank line; a fax-labeled copy; a conflicting duplicate of one of its documents; a copy recorded after the case opened) and noise age (0, 30, 90, 180, 365, 730 days before the request). Each cell records whether the real number is still picked and, if not, its reasons. The output is `overblock.json` plus a summary: per noise type, the ages at which that noise alone blocks the genuine number. Every noise type runs twice: alone, and again with a planted provable number (contract + listing, 150 days) that ranks below the real number on its own; those rows record whether the noise hands the pick to the planted number (review I6). Pick moves are measured, not gated: a move to a pre-seeded provable number is the K01 class. It asserts three things in both families and measures the rest: with no noise the real number is picked, a hidden copy never blocks it or moves the call, and a late-recorded copy never does.

6. **Pass bar, fixed before the run.** The property suite is green when a fixed example budget (at least 2000 worlds per property, seed pinned in CI) finds zero violations. A found violation is triaged, not tuned away: either a real rule defect (minimal case handed to the rule's owner, invariant kept) or an invariant that is too strong (relaxed, with the reason and the original result logged under Resolved and carried below). The sweeps have no pass/fail beyond their stated asserts; they publish tables.

7. **A `lab-fuzz` CI job runs the properties, not the sweeps.** It first runs the lab checks that must stay green (the L1 floor, the oracle's broken-double tests, the coverage floor), then the property suite at the pinned seed and example budget, and fails on any violation or any newly-written counterexample file (review I1). It triggers on changes to `packages/ledger/src/dialback_ledger/select.py`, the lab-owned generated-corpus directory, and the lab's generator/oracle. The frontier and over-block sweeps run on demand (`lab frontier`, `lab overblock`) and are not required checks: they are measurement. The two pytest tests that carry this gate are tagged `selection_gate`; `lab-smoke` (the voice slice's job, spec 0017) deselects them so each job gates its own concern, and a local `cd evals && uv run pytest` still runs everything.

## Acceptance

- [ ] A Hypothesis strategy produces valid `select()` inputs (the `DocumentRef` / `Sighting` / `ChangeRequest` shapes), parametric over the per-sighting attacker knobs above; a generated world runs through `select()` without error and its output validates against `selection.schema.json`.
- [ ] The generator covers its knobs: over a fixed, seeded sample of worlds, every rejection reason fires and every knob path appears (missing request, late-recorded documents, shared ids, subject replies, subdomain and lookalike senders, first-sorting twins, near-miss siblings, mixed visibility, party and timing within one number, non-default settings) at a stated floor, at least half the worlds hold an eligible number (the safety invariants are trivially true in a world with nothing eligible), and the first-cut generator (one shared profile per number) fails this check.
- [ ] Each invariant I1-I11 and L1 is a property test over the generator, and each is shown to fail first against a deliberately broken `select()` double (so the property can actually catch its target), then to pass against the real rule at the pinned example budget.
- [ ] A violating world (produced by the broken double) is shrunk and written as a named regression case in the generated-corpus directory, as a `world.json` that round-trips exactly, and the run exits non-zero.
- [ ] The regression cases the lab writes are themselves loaded and re-run against the real rule on every `lab-fuzz` run (a found case stays found).
- [ ] `lab frontier` runs the K01-shape sweep across the parameter grid and writes `frontier.json` plus a readable summary table; the one threshold invariant (a sub-threshold poison is never picked) holds across the whole grid.
- [ ] The frontier output states, in plain terms, the minimum attacker effort that reaches a pick (for example, "at least 2 counting channels and at least 90 days of lead"), derived from the grid, not hand-written.
- [ ] `lab overblock` runs the over-block sweep and writes `overblock.json` plus a per-noise summary; with or without a planted number, no noise leaves the real number picked, and a hidden or late-recorded copy never blocks it or moves the call; the planted rows report which noise hands the pick to the planted number.
- [ ] A `lab-fuzz` CI job runs the property suite at the pinned seed and budget and fails the PR on any violation or any newly-written counterexample, triggered by changes to `select.py`, the generated corpus, or the lab generator/oracle.
- [ ] The lab imports only the pure `dialback_ledger` selection rule and its lookalike classifier: no database, network, model, live account, or edit to `select.py`; every number it writes is in the fictional NPA-555-0100 to 0199 block.

## Tests (written first)

| Acceptance item | Test |
|---|---|
| 1 | `evals/tests/test_worldgen.py::test_generated_world_runs_and_validates` |
| 2 | `evals/tests/test_worldgen.py::test_generator_covers_every_reason_and_knob` |
| 3 | `evals/tests/test_invariants.py::test_each_invariant_catches_a_broken_rule`, `::test_all_invariants_hold_on_the_real_rule`, `::test_clean_world_picks_a_provable_number`, `::test_first_seen_in_context_is_checked_even_with_later_clean_evidence`, `::test_reference_counts_a_lookalike_copy_on_a_conflicted_id`, `::test_near_miss_check_covers_every_eligible_number` |
| 4 | `evals/tests/test_generated_corpus.py::test_fuzz_against_a_broken_rule_freezes_a_case`, `::test_frozen_world_round_trips`, `::test_round_trip_check_sees_a_lost_late_record`, `::test_freeze_never_replaces_a_different_world` |
| 5 | `evals/tests/test_generated_corpus.py::test_frozen_findings_rerun_against_the_rule` |
| 6 | `evals/tests/test_frontier.py::test_sweep_writes_table_and_threshold_holds` |
| 7 | `evals/tests/test_frontier.py::test_min_effort_is_derived_from_the_grid` |
| 8 | `evals/tests/test_frontier.py::test_overblock_sweep_baseline_and_inert_noise`, `::test_overblock_records_when_noise_hands_the_pick_to_a_planted_number`, `evals/tests/test_cli.py::test_lab_overblock_writes_table` |
| 9 | `evals/tests/test_cli.py::test_lab_fuzz_fails_on_a_violation`, `evals/tests/test_ci_wiring.py` |
| 10 | `python3 scripts/ci/boundaries.py` (no new cross-slice imports), `evals/tests/test_generated_corpus.py::test_lab_files_hold_only_fictional_numbers`, `::test_freeze_refuses_a_real_looking_number` |

Driven with the pure `select()` and a broken-rule double: no account, no key, no database. Hypothesis is already a workspace dependency (it drives `packages/ledger/tests/test_select_properties.py`).

## Out of scope

- Any change to `select.py` or the selection rule's behavior. This spec only measures and pressures it; defects go to the rule's owner (`packages/ledger`).
- Generative pressure on the other deterministic surfaces (the speech guard, `decide()`): the same pattern applies and is a later spec.
- The LLM document reader's injection corpus and the persona-LLM attacker (`DIALBACK_LAB_ATTACKER=llm`): those earn their cost only once the reader (spec 0022's quarantined `llm` mode) exists, since the reader is the first place an injection payload can act. Deferred to that reader's spec.
- The `lab` schema and any Postgres storage of generated worlds or frontier results; promoting the generated-corpus or frontier shapes to `packages/contracts`.
- Wilson intervals, held-out sealing and rate statistics: those are the full lab's, and the property suite's pass/fail is counterexample-based, not rate-based.
- Goal-directed search (Hypothesis `target()` steering draws toward a nearly-eligible poison): a later addition; this spec's search is random draws plus shrinking.

## Open questions

- Q60: the pinned example budget and seed for `lab-fuzz` (at least 2000 per property is the floor; the CI time budget may raise or lower it). Decide against the real CI runner.
- Q61: where the generated-corpus findings live. First cut keeps them lab-owned (`evals/corpus-generated/`) so a lab finding never writes into another slice's `corpus/`; revisit if the two should merge.
- Q62: whether the frontier sweep should also vary the real vendor number's establishment (a weaker real number lowers the bar a poison must clear). First cut holds the real number fixed and sweeps only the poison, and the table notes this as a limit.
- Q64: whether the over-block sweep should also vary the real number's own age. First cut uses one age (200 days); the blocking ages it reports for lookalike, in-thread and near-miss noise depend on that age, and the summary says so.

## Resolved and carried

- **Q63 (resolved by the user, Nov 8):** extend this spec beyond its first draft with per-sighting variation, deliberate near-miss siblings, the extra knobs (missing request, late-recorded documents, shared ids, subject replies, vendor subdomains, non-default settings), the I9-I11 and L1 invariants, and the over-block sweep. The first-cut generator gave each number one shared profile and drew no near-miss pairs; an exploratory census found the near-miss reason in 26 of 1000 worlds.
- **Renumbered** from 0030 to 0033 on Nov 8: main took 0030 to 0032 for other specs.
- **Findings, triaged under Decision 6 as defects in the rule (invariants kept; the rule's owner not yet contacted).** Four frozen cases in `evals/corpus-generated/`:
  - `near-miss-unanchored-by-shared-id` (v1 settings): a second document under the anchor's only document id puts it in change context; select() anchors near misses only on clean evidence, so the sibling is unblocked and picked.
  - `near-miss-unanchored-by-old-lookalike` (min_source_types 1, min_age_days 30, window 90): a lookalike email pulls a same-subject anchor into context and the pick moves to the sibling. Possible only when `same_subject_window_days` exceeds `min_age_days`.
  - `lookalike-erased-by-first-sorting-twin` (v1 settings): select() keeps the copy of a shared id whose repr sorts first, which is not chronological; a first-sorting second copy of a lookalike email erases its cutoff pull-back and the attacker number is picked.
  - `fuzz-seed0-m-shared-id-helped-06bbe3c8` (v1 settings, found by lab fuzz alone): the same erasure, here unblocking the genuine number.
  Reachability today: the three shared-id cases need two documents under one id, which select_number never passes (`ledger.documents.id` is the primary key and `write_ingest` skips an id it holds); the lookalike case needs ablation settings. All four are latent in production.
- **Finding, reachable today (measured, not frozen; whether to gate it is open with the user).** select()'s party and fax checks read change-context sightings too, so the attacker's own email naming the vendor's number as a bank or fax line (ingestion labels it so) makes that number unprovable; with a planted provable number present the pick moves to it, so the verification call reaches the attacker. The over-block rows `bank_label_in_thread` show it at every age. It removes the K01 attacker's need to outrank the real number.
- **Gate decision (user, Nov 8): "Gate on it now":** the findings are frozen as regressions, lab-fuzz stays red until the rule changes, and the finding is written up for the rule's owner. Open with the user: whether to keep it red now that the frozen ones are known to be latent.
- **Spec edits made during execution, with the original wording:**
  - Decision 3 and acceptance: "(same JSON shape as `corpus/`)" became a field-for-field `world.json`: the corpus shape has no `recorded_at`.
  - Decision 4: "a real established vendor number" became the vendor's own number seen only by email: against a provable real number, rank order means no grid cell can pick the poison.
  - Decision 5: the real number's age "400 days" became 200 (past the 365-day line an older near-miss sibling cannot block it); "a near-miss sibling seen earlier" became "a near-miss sibling"; the planted rows and `bank_label_in_thread` were added after review (I6).
  - Decision 7: the `selection_gate` routing (Task 5) and the green-checks step (review I1).
  - Acceptance item 2: the "at least half the worlds hold an eligible number" floor was added after the first passing run showed 36% to 42% (exploratory tuning of the generator's mix; it tightens the oracle).
  - Decision 2: I1 was "a number whose every visible, counting sighting is inside the change context is never eligible", which I4 with I5 already implied; it became R2's first-seen rule (review I5). I7 applied only to poison picks; it now covers every eligible number (review I4).
  - The "attacker evidence never helps" checks flag only numbers that become eligible; the plan's first wording also flagged any pick change. Pick moves to another number are measured in the planted over-block rows instead (Task 2 ruling, review I6).
- Any further violation found and triaged, and any invariant relaxed, is logged here with the reason and the original result (Decision 6).

## Suggested layout

```
evals/src/dialback_evals/
  worldgen.py     Hypothesis strategies: attacker knobs -> select() inputs; clean worlds
  invariants.py   I1-I11 and L1 as properties over a world and a select() output
  frontier.py     the K01-shape frontier sweep and the over-block sweep, with their tables
  gencorpus.py    write/load a shrunk counterexample as a named JSON case
  cli.py          lab fuzz (property suite), lab frontier and lab overblock (the sweeps)
evals/corpus-generated/   shrunk counterexamples, lab-owned regressions
.github/workflows/lab-fuzz.yml
```

## Commands

```bash
cd evals && uv run pytest -q
uv run --no-project --with ruff ruff check apps packages scripts evals
python3 scripts/ci/boundaries.py
uv run python -m dialback_evals.cli fuzz --examples 2000 --seed 0
uv run python -m dialback_evals.cli frontier --out evals/out/frontier.json
uv run python -m dialback_evals.cli overblock --out evals/out/overblock.json
```
