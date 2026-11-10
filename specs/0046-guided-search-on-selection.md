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

The lab has two kinds of check (FINDINGS B9).
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
   - `gain(world)` is the largest `drop` over the probes in use and every number, or 0 if none is
     positive.
   - A **near miss** is a `drop` of 1 or more where `n` is still ineligible after the move. A
     **break** is `n` becoming eligible, which today's `M_` labels already report.
   - `gain` is computed only from the `select()` runs the checks already make on that world. It
     costs no extra calls.
   - Every reason weighs 1 (Q87).
   - **Two variants**, both fixed now:
     - `gain` uses all three moves;
     - `gain_new` leaves out any move whose `M_` label a frozen case still fails. Today that leaves
       only the in-context sighting: 8 of the 9 frozen cases still fail, on the I12, shared-id and
       lookalike labels.
2. **Steering.** A guided search reports its score to Hypothesis with `target()` and lets
   Hypothesis's own hill climbing do the rest. No custom mutator in this spec.
   - Hypothesis 6.170.14 (pinned in `uv.lock`) runs one hill-climbing pass at the halfway mark
     below 1000 examples. At 1000 or more it starts at world 200 and interleaves, giving targeting
     up to half the budget (`hypothesis/internal/conjecture/engine.py`). So every arm sees the same
     first 200 worlds at a seed.
3. **Keep going.** The search runs its whole budget and no longer stops at the first failure. It
   keeps an archive:
   - for each violation label, the first world that showed it;
   - for each must-not-help move, the near miss with the highest `drop`.
   - A label already frozen in `evals/corpus-generated` is still counted but not frozen again.
   - An exception raised inside `select()` or the checks is a label of its own,
     `crash_<ExceptionName>`, so a crash is a finding and the search continues. `rerun` reports a
     crash on a frozen case the same way, instead of raising.
   - Nothing is raised inside Hypothesis, so Hypothesis never shrinks or replays.
4. **Shrinking.** The lab shrinks the archive itself, after the search.
   - It greedily removes one number, then one document with its sightings, then one sighting, and
     then resets the settings to the v1 defaults. It keeps each change only if the label survives.
   - The change request and its own document are always kept, since production never builds a
     request without its document.
   - The result is **1-minimal**: removing any other single number, document or sighting makes the
     label go away.
5. **What gates.**
   - Each new label's shrunk world is frozen in `evals/corpus-generated`, as today, and `lab fuzz`
     re-runs every frozen case.
   - Near misses go to `evals/out/near-misses.json` and never change the exit code.
   - The `lab-fuzz` CI job keeps its current command until the measurement below passes (Q89).
   - The archive is adopted as the fix for FINDINGS B3 whatever the measurement says. It is not an
     experiment: at the same seed it sees every world today's search sees, and more.
6. **CLI.**
   - `lab fuzz --search first|archive|guided|guided-new`. The default, `first`, is today's search,
     unchanged.
   - `lab redteam-bench` runs the measurement.

## The measurement (fixed before any run)

**Question.** Does steering by either score find planted breaks that the same search without
steering misses?

**Planted breaks.**
- Before any score code exists, a fresh session writes 12 one-line breaks of `select.py` into
  `evals/redteam/breaks.json`:
  - 6 of kind `defense`: a defense is deleted or weakened;
  - 6 of kind `helps`: a must-not-help move now helps.
- That session sees `select.py`, spec 0021 and the list of must-not-help moves, but not this spec's
  score.
- Each entry has an `id`, its `kind`, the `line` number, the exact `old` text of that line, the
  `new` text, one sentence on `why`, and a `witness`: a world on which the broken rule's output
  differs from the real rule's.
- A break is valid only if its line number and text both match, the module compiles, and its
  witness shows a difference. Validity is checked before the file is committed, so a break that
  changes nothing is never locked in.
- The file records the commit and sha256 of the `select.py` it was written against. The bench
  loads both the real and the broken rule from that exact source, so a later fix to `select.py`
  does not change the measurement.
- The last commit that changes `breaks.json` must be an ancestor of the first commit that adds
  `evals/src/dialback_evals/guided.py`, which holds the score. The bench checks this when it runs,
  since it runs locally with full history, and refuses otherwise.

**Arms.** Each arm runs at a budget of 1000 worlds. The search counts the distinct worlds it
evaluates and evaluates none past the budget. Decision 4's shrink runs afterwards and counts toward
neither the budget nor what was found.
- `archive`: decision 3's search, with no `target()`.
- `guided`: `archive` plus `target(gain)`.
- `guided-new`: `archive` plus `target(gain_new)`. The frozen cases it reads are re-run against the
  pinned rule.

There is no arm for today's search. At the same seed it sees a subset of the archive's worlds, so
comparing the two would win by construction.

**Found.** A run finds a break when some world it evaluated shows a violation label on the broken
rule that the pinned real rule does not show on the same world. The real-rule run is measurement:
it sits outside the budget and is the same for every arm.

**Seeds.**
- Dev seeds 0 to 9 are for debugging the bench.
- Held-out seeds 1000 to 1009 run once, for the result.
- The scores (decision 1), the budget and the bars are fixed by this spec. Any change to them after
  a dev run is logged in FINDINGS with its reason, and the result then uses seeds 2000 to 2009.

**Primary metric.** Per held-out seed: the number of `helps` breaks a guided arm finds, minus the
number `archive` finds.

**Pass bar.** For each guided arm against `archive`: a one-sided exact sign test over the seeds
where the two differ gives p <= 0.025. That is 0.05 split across the two guided arms. It needs at
least 6 differing seeds; with fewer, there is no evidence, and that counts as a fail.

**Guard.** On `defense` breaks, the guided arm loses on no more seeds than it wins.

**Kill.** A guided arm that fails its bar or its guard is dropped. If both pass, the one with the
larger total primary metric is kept, and `guided-new` on a tie. The result and every attempt go in
FINDINGS.

**Also reported, with no bar:**
- for each break and arm, the index of the first world that found it;
- breaks no arm finds: blind spots of the checks, which is a finding about the lab;
- on the pinned rule, the share of dev-seed worlds with `gain` and with `gain_new` of 1 or more;
- the Hypothesis version.

**Cost.** Exploratory timing on today's checks, 300 worlds at seed 0: about 30 ms and 46 `select()`
calls per world. The measurement is 3 arms x 10 seeds x 12 breaks x 1000 worlds, plus the
real-rule runs: roughly 6 hours on one core, or under an hour across 8 processes. It runs on demand
and never in CI.

## Acceptance

- [ ] Exposing the probe outputs changes no label. `all_violations` returns the same labels as
  before on every frozen case and on 300 worlds at seed 0.
- [ ] `guided` and `guided-new` make exactly as many `select()` calls per world as `archive`.
- [ ] `gain` is 0 when no must-not-help move changes any reason, and 0 when a move swaps one reason
  for another. On a hand-built world and broken double where one move removes exactly one reason,
  adds none, and leaves the number ineligible, `gain` is 1 and a near miss is recorded.
- [ ] `gain_new` leaves out a move whose label a frozen case still fails, and uses it again once no
  frozen case fails it.
- [ ] `archive` and both guided searches run the whole budget after a failure and record every
  distinct label. On today's rule at seed 0 they record at least two labels. (In an exploratory
  census of 300 worlds at seed 0, I12 fired in 82 and I11 in 34.)
- [ ] Every world the new searches freeze is 1-minimal, and keeps the request and its document.
- [ ] A crash inside `select()` is frozen as `crash_<ExceptionName>` and the search continues. A
  frozen crash case is reported by `rerun` as that label.
- [ ] Near misses are written to `evals/out/near-misses.json`, never to `corpus-generated`, and
  never change the exit code.
- [ ] `lab fuzz` with no `--search` behaves as today. The existing CLI tests pass unchanged.
- [ ] A break whose line number or text doesn't match, which fails to compile, or whose witness
  shows no difference is refused, with its id named.
- [ ] The bench runs the rule recorded in `breaks.json`, not the working tree's `select.py`. It
  refuses to run if `breaks.json` changed after `guided.py` was added.
- [ ] `lab redteam-bench` writes `evals/out/redteam-bench.json`: the per-seed counts, both sign
  tests, the guards and the extra reports. It refuses held-out seeds without `--held-out`.
- [ ] The new modules import only `dialback_ledger.select`, `dialback_ledger.lookalike`, the lab's
  own modules, Hypothesis and the standard library. Every number the lab writes is fictional, and
  `select.py` is unchanged.

## Tests (written first)

| Acceptance item | Test |
|---|---|
| 1 | `evals/tests/test_guided.py::test_exposing_probes_keeps_every_label` |
| 2 | `evals/tests/test_guided.py::test_guided_makes_no_extra_select_calls` |
| 3 | `evals/tests/test_guided.py::test_gain_is_zero_when_nothing_changes`, `::test_a_swapped_reason_scores_zero`, `::test_one_removed_defense_is_a_near_miss` |
| 4 | `evals/tests/test_guided.py::test_gain_new_skips_moves_with_open_labels` |
| 5 | `evals/tests/test_guided.py::test_archive_keeps_going_after_a_failure` |
| 6 | `evals/tests/test_guided.py::test_frozen_world_is_one_minimal` |
| 7 | `evals/tests/test_guided.py::test_a_crash_is_a_finding`, `evals/tests/test_generated_corpus.py::test_rerun_reports_a_frozen_crash` |
| 8 | `evals/tests/test_guided.py::test_near_misses_never_gate` |
| 9 | `evals/tests/test_cli.py` (unchanged), `::test_fuzz_default_search_is_today` |
| 10 | `evals/tests/test_redteam_bench.py::test_an_invalid_break_is_refused` |
| 11 | `evals/tests/test_redteam_bench.py::test_bench_runs_the_pinned_rule`, `::test_commit_order_is_checked` (on a temporary git repository, so it runs in CI's shallow checkout) |
| 12 | `evals/tests/test_redteam_bench.py::test_found_means_a_label_the_real_rule_lacks`, `::test_sign_test_bar`, `::test_held_out_needs_the_flag` |
| 13 | `evals/tests/test_guided.py::test_new_modules_import_only_the_pure_rule`, `evals/tests/test_generated_corpus.py::test_lab_files_hold_only_fictional_numbers` |

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
- **Q89.** Whether `lab-fuzz` switches to `--search archive` or a guided search is a follow-up PR.
  The archive part is settled (decision 5); the guided part waits for the measurement.
- **Q90.** 10 held-out seeds keep the measurement under an hour across 8 processes, but give little
  power: a guided arm passes with 9 wins if all 10 seeds differ, or 6 of 6 if only 6 differ. A
  larger budget or more seeds can't be chosen after seeing results.
- **Q91.** Who writes the planted breaks. A fresh session is my call. Inesh, as the rule's owner,
  would write more realistic ones.

## Commands

```bash
cd evals && uv run pytest -q
uv run --no-project --with ruff ruff check apps packages scripts evals
uv run lab fuzz --search guided --examples 1000 --seed 0 --out evals/corpus-generated
uv run lab redteam-bench --seeds 0-9
uv run lab redteam-bench --seeds 1000-1009 --held-out
```
