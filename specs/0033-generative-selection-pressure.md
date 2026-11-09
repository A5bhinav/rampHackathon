# 0033: The attack lab, generative selection pressure

**Owner:** Sami  **Slice:** call and adversary  **Target day:** after the voice slice (0017); the first generative cut, grown before the full lab's statistics.

## Goal

The attack lab (`evals/README.md`) is meant to measure how often Dialback is fooled, but today the only adversarial pressure on number selection (`ledger-select-v1`, spec 0021) is the frozen corpus (`corpus/`, spec 0020): 20 hand-authored cases scored by `test_select_corpus.py`. A green run there proves the rule matches those 20 examples, not that it is robust. Each case stresses one defense, and nothing searches for the input that breaks it. Selection is where the fraud decision actually lives (which number Dialback will trust and call, R2), so it is both the highest-value target and the one that can be tested exhaustively rather than by example.

This spec adds a generative layer to the lab that puts `select()` under adversarial search, in two halves. First, a world generator and an invariant oracle: thousands of generated adversarial worlds (documents, sightings, a change request) run through `select()`, and a set of properties that must hold for every world are asserted; any counterexample is shrunk to a minimal world and frozen as a named case, so the lab grows its own regressions from real findings. Second, a known-limit frontier sweep: the one K01 "known limit" (a number pre-seeded across channels, spec 0020) is generalized into a parametric sweep that measures the minimum attacker effort (channels, lead time, labels) that defeats the rule, turning a single accepted anecdote into a quantified residual-risk curve.

It imports only the pure `dialback_ledger.select` function (no database, core, network, model or live account), so it runs today and can gate selection-rule changes. It changes no rule and no contract: a property that fails is a finding handed to the rule's owner (`packages/ledger`, Inesh), never an edit from the lab.

## Contract touched

None. The generator, invariants and frontier report are lab-internal (`evals`). The selection output shape the lab reads is already frozen in `packages/contracts/schemas/selection.schema.json` (owned elsewhere); this spec consumes it and changes nothing. Promoting any generated-corpus or frontier-report shape to `packages/contracts/schemas` is a later, separate contract PR.

## Why this is worth adding over more corpus cases

- `select()` is pure (spec 0021): sightings and their documents' custody in, a `selection.schema.json` document out. No database, clock, network or model. So it can be called thousands of times in-process, which is what property-based search needs.
- The rule's whole contract is a short list of provability tests (`first_seen_in_change_context`, `too_few_source_types`, `younger_than_minimum_age`, `near_miss_of_older_number`, `hidden_text_only`, `not_vendor_party`, `fax_line`). Each is a universally-quantified claim ("no number that is X is ever eligible"), which is a property, not an example. A frozen corpus can only sample these; a generator plus the property closes the gap.
- The one documented hole (K01) is a single point on a curve the corpus never draws. The same generator that fuzzes the rule can sweep that curve.

## Decisions (confirmed)

1. **The generator emits `select()` inputs, nothing more.** A Hypothesis strategy builds a world: a vendor (domains), a change request (received time, thread, subject, sender), and a set of candidate numbers, each with per-sighting knobs an attacker controls: channel, lead time before the request, visibility (`visible` / `white` / `tiny` / `off_page`), role, party, sender domain (own / lookalike / other), thread and subject (in / out of the change context), and near-miss relationship to another number. It builds the public dataclasses `dialback_ledger.select` exposes (`DocumentRef`, `Sighting`, `ChangeRequest`) directly, the same shapes the corpus harness builds, so a generated world and a corpus world are indistinguishable to the rule. No import from another slice's test helpers.

2. **The oracle is a set of invariants, asserted on every generated world.** These are the rule's own guarantees restated as universals (each holds for all worlds):
   - I1 (R2): a number whose every visible, counting sighting is inside the change context is never eligible.
   - I2: a number with no visible sighting (hidden-text-only) is never eligible.
   - I3: a number with any visible non-vendor party, or a fax role, is never eligible.
   - I4: a number seen in fewer than `min_source_types` counting channels is never eligible.
   - I5: a number whose first clean sighting is younger than `min_age_days` before the cutoff is never eligible.
   - I6: the picked number (if any) has an empty reason set, and the output validates against `selection.schema.json`.
   - I7 (the adversarial core): a number the generator marked poison (controlled only through the change context, a lookalike sender, hidden text, or a near-miss of an older number) is selected only if it independently clears the known-limit bar, namely seen visibly, outside the context, in at least `min_source_types` counting channels, at least `min_age_days` before the cutoff. Any other poison pick is a violation.
   - I8: determinism. The same world run twice gives the same output and the same `inputs_sha256`.

3. **A counterexample becomes a regression, not a silent retry.** When Hypothesis finds a violating world it shrinks it to a minimal one, the lab writes it out as a named case under a lab-owned generated-corpus directory (same JSON shape as `corpus/`), and the run fails. The minimal world is the finding handed to the rule's owner; the frozen case keeps it from recurring. The lab never edits `select()`.

4. **The frontier sweep is analysis, not a gate.** A parametric K01-shape world (a real established vendor number, plus a poison number seeded with `channels in {1,2,3}`, `lead_days in {0,30,60,90,180,365}`, label set, visibility) runs through `select()` across the grid; each cell records whether the poison is picked, eligible, or rejected and by which reason. The output is a frontier table (`frontier.json` plus a readable summary): the boundary between "rejected" and "picked". It asserts only the one invariant that must hold everywhere (a poison below the `min_source_types` / `min_age_days` thresholds is never picked); the rest is measurement.

5. **Pass bar, fixed before the run.** The property suite is green when a fixed example budget (at least 2000 worlds per property, seed pinned in CI) finds zero violations. A found violation is triaged, not tuned away: either a real rule defect (minimal case handed to the rule's owner, invariant kept) or an invariant that is too strong (relaxed, with the reason and the original result logged in the Resolved list below). The frontier sweep has no pass/fail beyond the one threshold invariant; it publishes a table.

6. **A `lab-fuzz` CI job runs the properties, not the sweep.** It runs the property suite at the pinned seed and example budget and fails on any violation or any newly-written counterexample file. It triggers on changes to `packages/ledger/src/dialback_ledger/select.py`, the lab-owned generated-corpus directory, and the lab's generator/oracle. The frontier sweep runs on demand (`lab frontier`) and is not a required check: it is measurement, and it is not deterministic-cheap.

## Acceptance

- [ ] A Hypothesis strategy produces valid `select()` inputs (the `DocumentRef` / `Sighting` / `ChangeRequest` shapes), parametric over the per-sighting attacker knobs above; a generated world runs through `select()` without error and its output validates against `selection.schema.json`.
- [ ] Each invariant I1-I8 is a property test over the generator, and each is shown to fail first against a deliberately broken `select()` double (so the property can actually catch its target), then to pass against the real rule at the pinned example budget.
- [ ] A violating world (produced by the broken double) is shrunk and written as a named regression case in the generated-corpus directory, in the same JSON shape as `corpus/`, and the run exits non-zero.
- [ ] The regression cases the lab writes are themselves loaded and re-run against the real rule on every `lab-fuzz` run (a found case stays found).
- [ ] `lab frontier` runs the K01-shape sweep across the parameter grid and writes `frontier.json` plus a readable summary table; the one threshold invariant (a sub-threshold poison is never picked) holds across the whole grid.
- [ ] The frontier output states, in plain terms, the minimum attacker effort that reaches a pick (for example, "at least 2 counting channels and at least 90 days of lead"), derived from the grid, not hand-written.
- [ ] A `lab-fuzz` CI job runs the property suite at the pinned seed and budget and fails the PR on any violation or any newly-written counterexample, triggered by changes to `select.py`, the generated corpus, or the lab generator/oracle.
- [ ] The lab imports only `dialback_ledger.select` (pure): no database, network, model, live account, or edit to `select.py`.

## Tests (written first)

| Acceptance item | Test |
|---|---|
| 1 | `evals/tests/test_worldgen.py::test_generated_world_runs_and_validates` |
| 2 | `evals/tests/test_invariants.py::test_each_invariant_catches_a_broken_rule`, `::test_all_invariants_hold_on_the_real_rule` |
| 3 | `evals/tests/test_invariants.py::test_counterexample_is_frozen_as_a_case` |
| 4 | `evals/tests/test_generated_corpus.py::test_frozen_findings_rerun_against_the_rule` |
| 5 | `evals/tests/test_frontier.py::test_sweep_writes_table_and_threshold_holds` |
| 6 | `evals/tests/test_frontier.py::test_min_effort_is_derived_from_the_grid` |
| 7 | `evals/tests/test_cli.py::test_lab_fuzz_fails_on_a_violation` |
| 8 | `python3 scripts/ci/boundaries.py` (no new cross-slice imports) |

Driven with the pure `select()` and a broken-rule double: no account, no key, no database. Hypothesis is already a workspace dependency (it drives `packages/ledger/tests/test_select_properties.py`).

## Out of scope

- Any change to `select.py` or the selection rule's behavior. This spec only measures and pressures it; defects go to the rule's owner (`packages/ledger`).
- Generative pressure on the other deterministic surfaces (the speech guard, `decide()`): the same pattern applies and is a later spec.
- The LLM document reader's injection corpus and the persona-LLM attacker (`DIALBACK_LAB_ATTACKER=llm`): those earn their cost only once the reader (spec 0022's quarantined `llm` mode) exists, since the reader is the first place an injection payload can act. Deferred to that reader's spec.
- The `lab` schema and any Postgres storage of generated worlds or frontier results; promoting the generated-corpus or frontier shapes to `packages/contracts`.
- Wilson intervals, held-out sealing and rate statistics: those are the full lab's, and the property suite's pass/fail is counterexample-based, not rate-based.

## Open questions

- Q60: the pinned example budget and seed for `lab-fuzz` (at least 2000 per property is the floor; the CI time budget may raise or lower it). Decide against the real CI runner.
- Q61: where the generated-corpus findings live. First cut keeps them lab-owned (`evals/corpus-generated/`) so a lab finding never writes into another slice's `corpus/`; revisit if the two should merge.
- Q62: whether the frontier sweep should also vary the real vendor number's establishment (a weaker real number lowers the bar a poison must clear). First cut holds the real number fixed and sweeps only the poison, and the table notes this as a limit.

## Suggested layout

```
evals/src/dialback_evals/
  worldgen.py     Hypothesis strategy: attacker knobs -> select() inputs
  invariants.py   I1-I8 as properties over a world and a select() output
  frontier.py     the K01-shape parametric sweep and the frontier table
  gencorpus.py    write/load a shrunk counterexample as a named JSON case
  cli.py          lab fuzz (property suite) and lab frontier (the sweep)
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
```
