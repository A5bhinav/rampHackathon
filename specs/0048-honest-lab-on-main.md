# 0048: Make the attack lab honest, and land it on main

**Owner:** Sami  **Slice:** call and adversary  **Target day:** not set

## Goal

The attack lab lives on two unmerged branches: the voice lab (spec 0017, `sami/attack-lab-voice`)
and the fuzz lab (spec 0033, `sami/attack-lab-generative`). It still claims more than it shows:

- **Its own copies.** The voice lab builds facts with its own copy of the worker's code, and asks
  production's company check what the right answer is (FINDINGS C9).
- **Rules no scenario reaches.** Removing any one of 27 of the 37 decision rules leaves it green (C9).
- **Generator gaps.** The fuzz lab's generator never reaches some boundaries (B5).

When this spec is done:

- every voice-lab verdict rests on production's code, or says plainly that it rests on a declared
  fact;
- a new call-report fuzzer tests how answers become facts and outcomes, including uncertain and
  contradictory answers;
- the fuzz generator reaches the boundaries it misses;
- the lab is on main, in reviewable PRs.

It serves R1 (never verify an attack), R3 (code decides), R7 (fail closed) and ADR-11 (the lab runs
production code).

## Contract touched

None in `packages/contracts`. One change outside `evals`: a pure function pulled out of the core's
worker (decision A1, Q93).

## A. The voice lab tests production, or says it doesn't

1. **Facts come from production's code.**
   - `call_facts(report, case, generation, context) -> CaseFacts` is pulled out of `Worker.decide`
     (`apps/core/src/dialback_core/worker.py`). It covers everything that method does after its
     database reads: whether the dialed number was this version's pick, earlier calls, the company
     match with trade names, and `facts_from_answers`.
   - The worker calls it, and its behavior doesn't change: the core's tests pass unchanged.
   - The lab calls it with a case, generation events and a change-context result built from the
     scenario. The lab's own `build_facts` is deleted.
2. **Its own check of what the call said (R4).**
   - The lab scans every line the call speaks, fixed wording included, with its own detector: digit
     runs, number words ("oh" included), and amounts. It doesn't import
     `dialback_policy.guard`. The callback number in the disclosure is the one allowed run.
   - Today, one bug in the product's guard would blind both the guard and the gate that tests it
     (C9).
3. **Declared facts are labelled.**
   - A scenario whose verdict rests on a fact production never computes that way lists it under
     `declared`. Today that's a07 (`line_risk`), a04 (`dialed_number_is_pick: false`), a06
     (`direction: inbound`) and a01 (`offered_number_from_request: true` with no number offered).
   - The loader refuses a scenario that sets one of these without declaring it. The list of
     uncomputed facts lives in `evals`.
   - The scorecard reports declared scenarios in their own section and leaves them out of the
     per-class defense rows.
4. **The oracle doesn't ask the product.**
   - A new field, `caller.company_is: vendor | other | partial`, is the scenario author's judgment
     of what the caller said. The oracle reads it instead of calling `every_name_matches`.
   - Where the author's truth and production's check disagree, that is a finding (it is how A10
     shows). It is never resolved silently.
5. **Every reachable rule has a scenario.**
   - Add scenarios for NR05, NV03, NR07, NR10, V01, V05 and V07, which an honest scripted call can
     reach (C9).
   - **The rule-removal sweep.** Removing any one rule from the table must turn `lab run` red,
     counting the voice scenarios and part B together. The only exceptions are rules listed in
     `evals/UNREACHABLE.md`, each with the reason no honest call or generated report can reach it
     (Q94).
6. **Smaller fixes (C9).**
   - The R8 gate checks that the disclosure says it's an AI and that the line is recorded, not only
     that it came first.
   - The loader refuses: `inject` without `inject_phase`, an `offer_other_number` the line can't
     play, and YAML booleans in free-text fields (`no` read as `False`).
   - `expect_end` can say the caller declined.
   - One injection scenario for each phase an injection can reach, instead of one in total.
   - A scenario that speaks the digits. Once spec 0043 lands, spoken digits must never count.
   - `lab-smoke` also triggers on `worker.py`, `calls.py` and the file that holds `call_facts`.
   - Spec 0017 and the evals README stop saying the lab imports no core code, and stop showing
     `--split dev`. l13 (voicemail) says it covers the text line only.

## B. A call-report fuzzer: how answers become facts and outcomes

This tests the step after the call, the way the fuzz lab tests `select()`. No call is simulated:
each case is a generated call report, which is what the call hands to the core.

1. **What it generates.** A Hypothesis strategy over call reports and case details. It uses only
   values production can produce: the contract's enums.
   - **Recorded values for each answer:**
     - consent `yes` or `no`;
     - the change `yes`, `no` or `unsure`;
     - the digits as one of match, old account, near miss, ambiguous, inconsistent, `dont_know`
       or `refused`;
     - any answer missing, recorded twice with different values, or out of order.
   - **Company answers** from a hand-written phrase bank, each with a known truth:
     - the vendor's legal name, with case and suffix variants, or a trade name;
     - another company;
     - a negation ("Not Acme, this is Globex");
     - a reseller ("a reseller for Acme");
     - a correction ("Acme? No, Globex");
     - a partial name ("Acme");
     - filler words around any of these.
   - **The call:** every call result in the contract (`answers_recorded`, `no_answer`, `voicemail`,
     `busy`, `failed`, `machine`, `declined`, `callback_requested`), who answered, and the
     direction.
   - **The voice service's checks:** each flag on or off, and violations and guard trips at 0 or
     more.
   - **Case details:** trade names; earlier calls with their results and answers; whether the
     speaker is in the change thread; whether a number was offered from the request; whether the
     dialed number was eligible, and whether it was the pick.
2. **What it runs.** Production's `call_facts` (A1), then `decide()`.
3. **The checks.**
   - **F1. Verified only on an honest, full confirmation.** "Honest, full confirmation" is judged
     by the generator's truth: the call ended `answers_recorded`; a human answered; we dialed out;
     the number dialed was this version's pick; consent and the change are both `yes`; the company
     answer names the vendor; the digits match the new account (and the old and new accounts don't
     end alike); every check is clean; there is no line risk; the speaker isn't from the change
     thread; no number was offered from the request; and no earlier call counts against it.

     This check restates the verify conditions, so it can't find a mistake in their design (B9).
     What it does catch is a fact computed wrongly, such as A10.
   - **F2. Less certainty never helps (metamorphic).** None of these moves may turn an outcome
     that isn't verified into verified:
     - the change from `yes` to `unsure` or `no`;
     - the digits to `dont_know`, `refused` or a near miss;
     - consent from `yes` to `no`;
     - the company answer to a partial name, another company or a negation;
     - a human answer to a machine or an unknown answerer;
     - adding a second, different value to any answer;
     - dropping any answer;
     - adding an earlier call with any concern;
     - setting any check flag;
     - marking the speaker as being from the change thread.
   - **F3. The company check against the truth.** `every_name_matches` is never `True` when the
     truth is another company, a negation, a reseller or a correction. The share of
     names-the-vendor answers it rejects is reported (over-blocking), not gated.
   - **F4. A call that didn't record answers contributes none.** For any call result other than
     `answers_recorded`, the outcome is the same with the answers as without them.
4. **Proving each check can fail.** Each check first fails on a deliberately broken double:
   - a company check that accepts any answer containing the vendor's name;
   - an assembly that keeps answers on failed calls;
   - a `decide()` with NR09 removed;
   - and one per F2 move.

   Then it runs on production at seed 0 with 2000 reports.
5. **Findings.** A violating report is shrunk, frozen under `evals/reports-generated/` (Q95) and
   re-run on every run, like spec 0033's frozen worlds.
   - A violation is triaged as a product defect or as a check that is too strong. It is never
     tuned away.
   - A10 is expected to fail F3 at once. That finding goes to `company.py`'s owner (Abhi).

## C. The fuzz generator reaches its boundaries (B5, B6, B7)

1. **New knobs.**
   - A request without a thread.
   - A requested cutoff earlier than the request.
   - Ages exactly at `min_age_days`, at the 365-day established line and at the same-subject
     window.
   - Near misses seen at the same time.
   - A late sighting on an on-time document.

   Each of B5's seven deliberate changes must be caught by 2000 worlds at seed 0, with one test per
   change.
2. **The coverage floor.**
   - `context_label_on_provable` counts a label on the pick only.
   - The eligible-number floor leaves out the planted real number.
   - A test shows the first-cut generator fails the coverage check, as 0033's acceptance says.
3. **B7's minors.**
   - `lab run` works without the dev-only `hypothesis`.
   - The unseeded property test gets a fixed seed.
   - New tests for the near-miss edges at exactly 365 days and for a same-time tie.
   - The schema is found from the package, not a fixed relative path.
   - `invariants.py` stops importing worldgen's private `_address`.
   - `lab frontier` prints its grid.
   - The new commands go in `AGENTS.md` and the evals README.

## D. Landing on main

1. **Renumbering (FINDINGS E).** The lab's `0033` takes the next free spec number when its PR opens,
   and its Q60, Q61 and Q65 take the next free question numbers. Every reference follows: spec
   text, FINDINGS and code comments.
2. **PR order.** Each PR is stacked on the one before. Each is as close to 400 changed lines as its
   parts allow, and the voice and fuzz labs never share a PR.
   1. `apps/core`: pull out `call_facts`, with no change in behavior.
   2. The voice lab (0017), with part A. FINDINGS lands here and each later PR updates it.
   3. The fuzz lab's core (the renumbered 0033): the generator, the checks, freezing, `lab fuzz`,
      the frozen worlds and the `lab-fuzz` job, with part C.
   4. The sweeps: `lab frontier` and `lab overblock`.
   5. Part B, the call-report fuzzer.
3. **Red gates on main (Q92).** `lab-fuzz` is red today, because 8 of the 9 frozen worlds still fail,
   and part B will be red at once because of A10. Both jobs run on other owners' PRs.
4. **Spec 0046's pin.** Tag `e3d57b5` as `lab-0046-select-pin` before any rebase. The planted
   breaks name that commit as the rule under test.
5. **Coordination.**
   - Spec 0043 is being built on `sami/keypad-only-digits-impl` and changes the call flow.
     Whichever of the two lands second refreezes the voice scenarios.
   - When spec 0044 lands, a07's `line_risk` stops being declared.
6. **PRs open only on Sami's OK.** Agents open PRs; a person merges.

## Acceptance

- [ ] The lab's facts come from `call_facts`, and `build_facts` is gone. The core's tests pass
  unchanged.
- [ ] With the lab's own detector, each of C9's four blind cases fails `lab run`: the guard's
  detection removed, a broken digits check, a removed vendor-name rule, and an agent naming a bank
  and an amount without digits.
- [ ] Every scenario that rests on an uncomputed fact declares it. The loader refuses one that
  doesn't, and the scorecard reports declared scenarios on their own.
- [ ] The oracle imports nothing from `dialback_core`.
- [ ] Removing any rule turns the lab red, except the rules in `UNREACHABLE.md`, each with its
  reason.
- [ ] Each item in A6 has a test.
- [ ] The report strategy produces only values the contract allows. Each of F1 to F4 fails first on
  its broken double, then runs on production at seed 0 with 2000 reports. Findings are frozen and
  re-run.
- [ ] Each of B5's seven changes is caught, and each item in C2 and C3 has a test.
- [ ] No spec or question number in the lab collides with main or any other branch.
- [ ] The tag `lab-0046-select-pin` exists on `e3d57b5`.
- [ ] Each PR's commands pass, except the known failures Q92 settles.

## Tests (written first)

| Item | Test |
|---|---|
| A1 | `apps/core/tests/test_call_facts.py` (each fact; the worker's facts are unchanged), `evals/tests/test_runner.py::test_lab_uses_production_call_facts` |
| A2 | `evals/tests/test_breaks.py::test_lab_sees_what_the_guard_misses` (the four C9 cases) |
| A3 | `evals/tests/test_scenario.py::test_uncomputed_fact_must_be_declared`, `evals/tests/test_gates.py::test_declared_scenarios_are_reported_apart` |
| A4 | `evals/tests/test_oracle.py::test_oracle_imports_no_core`, `::test_company_truth_disagreement_is_a_finding` |
| A5 | `evals/tests/test_rule_sweep.py::test_removing_any_rule_turns_the_lab_red` |
| A6 | `evals/tests/test_gates.py::test_disclosure_says_ai_and_recorded`, `evals/tests/test_scenario.py::test_loader_refusals`, `evals/tests/test_ci_wiring.py` |
| B | `evals/tests/test_reports.py::test_reports_use_contract_values`, `::test_each_check_catches_its_broken_double`, `::test_checks_on_production`, `::test_findings_are_frozen_and_rerun` |
| C1 | `evals/tests/test_breaks_selection.py` (one test per B5 change) |
| C2, C3 | `evals/tests/test_worldgen.py::test_first_cut_generator_fails_coverage`, plus one test per item |
| D1 | `evals/tests/test_numbering.py::test_no_spec_or_question_number_collides` |

## Out of scope

- The end-to-end runner (the next step): the entry points, hold, ingestion, release bot and
  evidence chain.
- The live Claude agent on the call (step 3).
- Scale and statistics (step 4).
- Ingestion's tests against any reader: their own spec.
- Real PDFs and the LLM reader: product work for the ledger.
- Fixing any product defect the lab finds. Findings go to their owners.

## Open questions

- **Q92 (Sami, with Inesh and Abhi).** How red gates live on main. My recommendation is a strict
  list of known failures for each job: each listed case must keep failing, so a fix gets noticed
  and taken off the list, and any other failure turns the job red. The alternatives are a job that
  isn't required, or a required job that stays red.
- **Q93 (Abhi).** Who pulls `call_facts` out of `worker.py` (Abhi, or Sami as partner with his
  review), and whether it lives in `apps/core` or `packages/decision`.
- **Q94.** `UNREACHABLE.md` is reviewed by Abhi, who owns the decision rules (my call).
- **Q95.** The report fuzzer's findings live in `evals/reports-generated/` (my call).
- **Q96.** The company phrase bank is hand-written, so it covers only phrasings we think of. A
  model-written bank comes with the live agent (my call).

## Commands

```bash
cd evals && uv run pytest -q
cd apps/core && uv run pytest -q
uv run --no-project --with ruff ruff check apps packages scripts evals
uv run lab run --split voice
uv run lab fuzz --examples 2000 --seed 0 --out evals/corpus-generated
```
