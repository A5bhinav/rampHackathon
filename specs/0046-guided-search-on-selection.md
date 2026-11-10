# 0046: A guided red-team search on number selection

**Owner:** Sami  **Slice:** call and adversary  **Target day:** not set

## Goal

The fuzz lab (spec 0033) throws random worlds at `select()`, the rule that picks which number
Dialback calls (R2), and stops at the first failure. Nothing learns from results (FINDINGS B3, D).

This spec adds a search that learns. It scores each world by how much an attacker move helped,
steers toward higher scores, keeps going after a failure, and reports near misses. It also fixes,
before any run, the measurement that decides whether the steering is worth keeping.

It is the "goal-directed search" that spec 0033 left out of scope. Like 0033 it imports only the
pure `select()` and never edits it: a finding goes to the rule's owner (`packages/ledger`, Inesh).

## Contract touched

None. Everything here is lab-internal (`evals`).

## Why the score is metamorphic

The lab has two kinds of check.
- **Direct checks** (I1 to I7, I9) recompute what the rule should say. Where no document id is
  shared they follow `select()`'s own reading of the spec exactly (spec 0033, decision 2). So they
  catch regressions, such as a deleted branch, but not mistakes in the design itself.
- **Metamorphic checks** run the world twice: as it is, and with one attacker move added. They need
  no right answer, only the design rule "attacker evidence never helps". The design holes found so
  far came from them (A1 through I12, the shared-id defect through I11).

So the score that steers the search is metamorphic. The direct checks stay, as the regression net.

## Decisions (my calls; Sami can overrule)

1. **The score.**
   - The **must-not-help moves** are the probes `metamorphic()` already runs that add attacker
     evidence: an in-context contract sighting (`M_incontext_helped`), the lookalike emails at 30,
     100 and 400 days (`M_lookalike_helped`), and the shared-id twins (`M_shared_id_helped`).
   - For each such probe `p`, and each number `n` that is ineligible in the base run:
     `drop(n, p) = len(reasons_base(n)) - len(reasons_p(n))`. `reasons` is the candidate's reason
     list in the `select()` output, which the schema keeps unique. A number missing from a probe's
     output counts as 0 and is logged as an anomaly.
   - `gain(world)` is the largest `drop` over every such probe and number, or 0 if none is positive.
   - A **near miss** is a `drop` of 1 or more where `n` is still ineligible after the move. A
     **break** is `n` becoming eligible, which today's `M_` labels already report.
   - `gain` is computed only from the `select()` runs the checks already make on that world. It
     costs no extra calls.
   - Every reason weighs 1 (Q87).
2. **Steering.** The guided search reports `gain` to Hypothesis with `target()` and lets
   Hypothesis's own hill climbing do the rest. No custom mutator in this spec.
3. **Keep going.** The search runs its whole budget and no longer stops at the first failure. It
   keeps an archive:
   - for each violation label, the first world that showed it;
   - for each must-not-help move, the near miss with the highest `drop`.
   A label already frozen in `evals/corpus-generated` is still counted but not frozen again. An
   exception raised inside `select()` or the checks is a label of its own (`crash:<type>`), so a
   crash is a finding and the search continues.
4. **Shrinking.** Hypothesis can only shrink the failure it stops on, so the lab shrinks the archive
   itself, after the search. It greedily removes one number, then one document with its sightings,
   then one sighting, and then resets the settings to the v1 defaults, keeping each change only if
   the label survives. The result is **1-minimal**: removing any single number, document or
   sighting makes the label go away.
5. **What gates.** Each new label's shrunk world is frozen in `evals/corpus-generated`, as today, and
   `lab fuzz` re-runs every frozen case. Near misses go to `evals/out/near-misses.json` and never
   change the exit code. The `lab-fuzz` CI job keeps its current command until the measurement
   below passes (Q89).
6. **CLI.**
   - `lab fuzz --search first|archive|guided`. The default, `first`, is today's behavior,
     unchanged.
   - `lab redteam-bench` runs the measurement.

## The measurement (fixed before any run)

**Question.** Does steering by `gain` find planted breaks that random search with the same archive
misses?

**Planted breaks.**
- Before any score code exists, a fresh session writes 12 one-line breaks of `select.py` into
  `evals/redteam/breaks.json`:
  - 6 of kind `defense`: a defense is deleted or weakened;
  - 6 of kind `helps`: a must-not-help move now helps.
- That session sees `select.py`, spec 0021 and the list of must-not-help moves, but not this spec's
  score.
- Each break is the exact line it replaces and its replacement. The bench applies it to a copy of the
  module, loaded under a private name inside `dialback_ledger`, and stops with an error naming the
  break if the line no longer matches.
- The file and its sha256 are committed before the score code. That commit is an ancestor of the
  first commit that adds the score.

**Arms.** Each arm runs at a budget of 300 worlds. The checker counts its calls and evaluates no
world past the budget.
- `first`: today's fuzz, which stops at the first failure.
- `archive`: random search with decision 3's archive, and no `target()`.
- `guided`: `archive` plus `target(gain)`.

**Found.** A run finds a break when some world it evaluated shows a violation label on the broken
rule that the real rule does not show on the same world. The real-rule run is measurement: it sits
outside the budget and is the same for every arm.

**Seeds.**
- Dev seeds 0 to 9 are for debugging the bench.
- Held-out seeds 1000 to 1009 run once, for the result.
- The score (decision 1), the budget and the bar are fixed by this spec. Any change to them after a
  dev run is logged in FINDINGS with its reason, and the result then uses seeds 2000 to 2009
  instead.

**Primary metric.** Per held-out seed: the number of `helps` breaks `guided` finds, minus the number
`archive` finds.

**Pass bar.** A one-sided exact sign test over the seeds where the two arms differ gives p <= 0.05,
with at least 5 differing seeds. Fewer than 5 differing seeds is no evidence, and counts as a fail.

**Guard.** On `defense` breaks, `guided` loses on no more seeds than it wins.

**Kill.** If the bar or the guard fails, the score is dropped. The archive is kept if `archive`
beats `first` by the same test. Either way the result, with every attempt, goes in FINDINGS.

**Also reported, with no bar:**
- `archive` against `first`, by the same test;
- for each break and arm, the index of the first world that found it;
- breaks no arm finds: blind spots of the checks, which is a finding about the lab;
- on the real rule, the share of dev-seed worlds with `gain` of 1 or more.

**Cost.** Exploratory timing on today's checks, 300 worlds at seed 0: about 30 ms and 46 `select()`
calls per world. The measurement is 3 arms x 10 seeds x 12 breaks x 300 worlds, plus the real-rule
runs: roughly 2 hours on one core. It runs on demand, in parallel processes, and never in CI.

## Acceptance

- [ ] Exposing the probe outputs changes no label. `all_violations` returns the same labels as
  before on every frozen case and on 300 worlds at seed 0.
- [ ] `guided` makes exactly as many `select()` calls per world as `archive`.
- [ ] `gain` is 0 when no must-not-help move changes any reason, and 0 when a move swaps one
  reason for another. On a hand-built world and broken double where one move removes exactly one
  reason, adds none, and leaves the number ineligible, `gain` is 1 and a near miss is recorded.
- [ ] `archive` and `guided` run the whole budget after a failure and record every distinct
  label. On today's rule at seed 0 they record at least two labels. (In an exploratory census of
  300 worlds at seed 0, I12 fired in 82 and I11 in 34.)
- [ ] Every world the new searches freeze is 1-minimal.
- [ ] A crash inside `select()` is frozen as `crash:<type>` and the search continues.
- [ ] Near misses are written to `evals/out/near-misses.json`, never to `corpus-generated`, and
  never change the exit code.
- [ ] `lab fuzz` with no `--search` behaves exactly as today. The existing CLI tests pass unchanged.
- [ ] The breaks file's sha256 matches, and its commit precedes the score code. A break whose line
  no longer matches stops the bench with an error naming it.
- [ ] `lab redteam-bench` writes `evals/out/redteam-bench.json`: the per-seed counts, the sign
  test, the guard and the extra reports. It refuses held-out seeds without `--held-out`.
- [ ] The lab still imports only the pure selection rule and its lookalike classifier, writes only
  fictional numbers, and leaves `select.py` unchanged.

## Tests (written first)

| Acceptance item | Test |
|---|---|
| 1 | `evals/tests/test_guided.py::test_exposing_probes_keeps_every_label` |
| 2 | `evals/tests/test_guided.py::test_gain_makes_no_extra_select_calls` |
| 3 | `evals/tests/test_guided.py::test_gain_is_zero_when_nothing_changes`, `::test_a_swapped_reason_scores_zero`, `::test_one_removed_defense_is_a_near_miss` |
| 4 | `evals/tests/test_guided.py::test_archive_keeps_going_after_a_failure` |
| 5 | `evals/tests/test_guided.py::test_frozen_world_is_one_minimal` |
| 6 | `evals/tests/test_guided.py::test_a_crash_is_a_finding` |
| 7 | `evals/tests/test_guided.py::test_near_misses_never_gate` |
| 8 | `evals/tests/test_cli.py` (unchanged), `::test_fuzz_default_search_is_today` |
| 9 | `evals/tests/test_redteam_bench.py::test_breaks_hash_and_commit_order`, `::test_a_stale_break_stops_the_bench` |
| 10 | `evals/tests/test_redteam_bench.py::test_found_means_a_label_the_real_rule_lacks`, `::test_sign_test_bar`, `::test_held_out_needs_the_flag` |
| 11 | `python3 scripts/ci/boundaries.py`, `evals/tests/test_generated_corpus.py::test_lab_files_hold_only_fictional_numbers` |

## Out of scope

- Any change to `select.py`.
- **The text-to-label step.** The search writes labels in the shapes ingestion emits, so a bug in
  the reader is invisible to it. FINDINGS D names that as its own first piece.
- **The lookalike classifier.** Both kinds of check borrow it, so a bug in it is invisible to both.
- **An LLM attacker** on the call or in documents.
- **Switching `lab-fuzz` CI** to the new search (Q89).
- Custom mutators, or an archive finer than one slot per label.
- Making the direct checks independent of `select()`'s reading of the spec.

## Open questions

These are my calls; Sami can overrule them.

- **Q87.** Every reason weighs 1 in `gain`, so a move that trades a strong defense for a weak one
  scores 0. Ranking defenses is a follow-up if near misses turn out too rare to steer by.
- **Q88.** Near misses stay lab numbers. A person decides which ones go to Inesh.
- **Q89.** Whether `lab-fuzz` switches to `--search archive` or `guided` is a follow-up PR, decided
  by the measurement's result.
- **Q90.** A budget of 300 worlds and 10 held-out seeds keeps the measurement near 2 hours on one
  core. A larger budget can't be chosen after seeing results.
- **Q91.** Who writes the planted breaks. A fresh session is my call. Inesh, as the rule's owner,
  would write more realistic ones.

## Commands

```bash
cd evals && uv run pytest -q
uv run --no-project --with ruff ruff check apps packages scripts evals
python3 scripts/ci/boundaries.py
uv run lab fuzz --search guided --examples 300 --seed 0 --out evals/corpus-generated
uv run lab redteam-bench --seeds 0-9
uv run lab redteam-bench --seeds 1000-1009 --held-out
```
