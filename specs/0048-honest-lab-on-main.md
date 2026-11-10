# 0048: Make the attack lab honest, and land it on main

**Owner:** Sami  **Slice:** call and adversary  **Target day:** not set

## Goal

The attack lab lives on two unmerged branches: the voice lab (spec 0017, `sami/attack-lab-voice`)
and the fuzz lab (spec 0033, `sami/attack-lab-generative`). It still claims more than it shows:

- **Its own copies.** The voice lab builds facts with its own copy of the worker's code, and asks
  production's company check what the right answer is (FINDINGS C9).
- **Rules no scenario reaches.** Removing any one of 27 of the 37 decision rules leaves it green (C9).
- **Generator gaps.** The fuzz generator never reaches some boundaries (B5).
- **Seeds that drift.** Its seeds don't pin the worlds (B10).

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

None in `packages/contracts`. One change in Abhi's code (`call_facts`, A1, Q102) and one in the
voice service's (A1).

## What "caught" means here

The starting point is already red: today's rule fails I12, and part B will fail on A10 at once. So
every check below is **differential**. A change is caught when, on some input, the changed code's
result differs from production's result on the same input. That is how spec 0046 defines a break
being found, and it is the only meaning of "caught", "seen" or "turns red" in this spec. Every
sample is drawn with pinned draws (C1).

## A. The voice lab tests production, or says it doesn't

1. **The call's report and its facts come from production's code.** The lab chains production's
   functions, and writes no copies of them:
   - **The voice service's report.** It uses `_report_body` and `_with_connect_facts` from
     `apps/voice/src/dialback_voice/app.py`, made public. They decide that answers ride along only
     on `answers_recorded`, and that `dialed_number_is_pick` is true.
   - **The digits.** It uses production's `spoken_last_four` and `compare_last_four`, which is what
     `calls.py` does when a report arrives. The rest of that step (hashing and storing) affects no
     fact, and the lab leaves it out.
   - **The facts.** It uses `call_facts(report, case, generation, context) -> CaseFacts`, pulled out
     of `Worker.decide` (`apps/core/src/dialback_core/worker.py`). This is everything that method
     does after its database reads: whether the dialed number was this version's pick, earlier
     calls, the company match with trade names, and `facts_from_answers`.
     - It lives in `apps/core`, so it can keep calling `company.py`.
     - The worker calls it, and its behavior doesn't change: the core's tests pass unchanged.
   - **Only declared inputs remain.** The lab's own `build_facts` is deleted. All it still builds by
     hand are the inputs production would read from the database: the case, the generation events,
     and the change-context result. These come from the scenario, and each is listed.
2. **Its own check of what the call said (R4).** The lab scans every line the call speaks with its
   own detector, and doesn't import `dialback_policy.guard`. It flags:
   - three or more digits in a row, as numerals or number words ("oh" included);
   - amounts;
   - a bank's name, from a list in `evals`;
   - any of the vendor's names;
   - any phone number other than the callback.

   The callback number is allowed only in the fixed lines that say it: the disclosure, the voicemail
   line (`script.py:61`) and the consent-refused line (`script.py:67`). The fixed wording "last four
   digits" is a single number word, so it is below the threshold.

   **The agent says nothing of its own** (added Nov 10). Production agents return no text: code
   owns every spoken line. So a gate fails any call where the agent's reply carries text. This
   catches what no word list can, such as an agent agreeing in words to call another number ("sure,
   I'll use the number you gave me"). If an agent that speaks is ever built, this gate has to be
   revisited.
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
   - Where the author's truth and production's check disagree, the scorecard reports a finding. That
     is how A10 shows.
   - An A10 voice scenario is added, and goes on `lab-smoke`'s known-failures list (D3).
5. **Every reachable rule has a scenario.**
   - Add scenarios for NR05, NV03, NR07, NR10, V01, V05 and V07, which an honest scripted call can
     reach (C9).
   - **The rule-removal sweep (`lab sweep`).** Removing one rule is **seen** when some voice
     scenario or generated report (part B) gets a different outcome than with the full table.
   - Every rule must be seen, except the rules listed in `evals/UNREACHABLE.md`, each with its
     reason. A reason is either that no honest call or generated report reaches the rule, or that
     another rule always gives the same outcome (V08, for example).
6. **Smaller fixes (C9).**
   - The R8 gate checks that the disclosure says it's an AI and that the line is recorded, not only
     that it came first.
   - The loader refuses: `inject` without `inject_phase`, an `offer_other_number` the line can't
     play, and YAML booleans in free-text fields (`no` read as `False`).
   - `expect_end` can say the caller declined.
   - One injection scenario for each phase an injection can reach, instead of one in total.
   - A scenario that speaks the digits. Once spec 0043 lands, spoken digits must never count.
   - `lab-smoke` also triggers on `worker.py`, `calls.py`, the file that holds `call_facts`, and
     `apps/voice/.../app.py`.
   - Spec 0017 and the evals README stop saying the lab imports no core code, and stop showing
     `--split dev`. l13 (voicemail) says it covers the text line only.

## B. A call-report fuzzer (`lab reports`): how answers become facts and outcomes

This tests the step after the call, the way the fuzz lab tests `select()`. No call is simulated.

1. **What it generates.** A Hypothesis strategy builds the voice service's call result and the
   case's details.
   - **Recorded values for each answer:**
     - consent `yes` or `no`;
     - the change `yes`, `no` or `unsure`;
     - spoken digit values, set against the new and old accounts so that every comparison result
       comes up;
     - flags;
     - an offered number;
     - any answer missing, recorded twice with different values, or out of order.
   - **Company answers** from a hand-written phrase bank, each with a known truth:
     - the vendor's legal name, with case and suffix variants, or a trade name;
     - another company;
     - a negation ("Not Acme, this is Globex");
     - a reseller ("a reseller for Acme");
     - a correction ("Acme? No, Globex");
     - a partial name ("Acme");
     - filler words around any of these.
   - **The call:** every call result in the contract, who answered, and the direction.
   - **The voice service's checks:** each check passing or failing.
   - **Case details:** trade names; earlier calls; whether the speaker is in the change thread;
     whether a number was offered from the request; whether the dialed number was this version's
     pick.
2. **Two domains.**
   - **Reachable today:** whatever production can send. It is built from a call result through A1's
     chain, so the voice service's own rules apply: answers only on `answers_recorded`, and the pick
     always true.
   - **Contract only:** report bodies the contract allows but production never sends today, such
     as answers on earlier failed calls (A16), or `direction: inbound`.
   - Only the reachable domain is gated. A violation that occurs only in the contract-only domain is
     reported to the owner as a future-state finding.
3. **What it runs.** A1's chain, then `decide()`.
4. **The checks.**
   - **F1. Verified only on an honest, full confirmation.** "Honest, full confirmation" is judged
     by the generator's truth: the call ended `answers_recorded`; a human answered; we dialed out;
     the number dialed was this version's pick; consent and the change are both `yes`; the company
     answer names the vendor; the digits match the new account (and the old and new accounts don't
     end alike); every check passed; there is no line risk; the speaker isn't from the change
     thread; no number was offered from the request; and no earlier call counts against it.

     This check restates the verify conditions, so it can't find a mistake in their design (B9).
     What it does catch is a fact computed wrongly, such as A10.
   - **F2. Less certainty never helps (metamorphic).** None of these moves may turn an outcome that
     isn't verified into verified:
     - the change from `yes` to `unsure` or `no`;
     - the digits to `dont_know`, `refused` or a near miss;
     - consent from `yes` to `no`;
     - the company answer to a partial name, another company or a negation;
     - a human answer to a machine or an unknown answerer;
     - adding a second, different value to an answer;
     - dropping one of the confirming answers (consent, the change, the digits, the company, the
       name or the role);
     - adding an earlier call with a concern (contract-only domain);
     - setting a check to its failing value;
     - marking the speaker as being from the change thread.

     Dropping a flag, an offered number or a contradicting answer may help, and should: those are
     evidence against the change. So they aren't moves.
   - **F3. The company check against the truth.**
     - `every_name_matches` is never `True` when the truth is another company, a negation, a
       reseller or a correction.
     - The share of names-the-vendor answers it rejects is reported (over-blocking), not gated.
     - A strict reference matcher must pass F3, to show the phrase bank's truth labels are sound.
5. **Proving each check can fail.** Each check is first shown failing on a deliberately broken
   double:
   - **F1:** a `decide()` whose V07 accepts `unsure` and whose NR09 is turned off. NR09 refuses any
     unsure answer first, so the V07 change alone changes nothing (corrected Nov 10).
   - **F2:** one double per move, each letting that move's less certain value through. For example,
     a `decide()` that skips the digit rules when the digits are `dont_know`.
   - **F3:** a company check that accepts any answer containing the vendor's name. Production may
     well fail this too: that is A10.

   Then each check runs on production, at seed 0, on 2000 reports.
6. **Findings.** A violating report is shrunk, frozen under `evals/reports-generated/` (Q104) and
   re-run on every run.
   - A violation is triaged as a product defect or as a check that is too strong. It is never
     tuned away.
   - A10's finding goes to `company.py`'s owner, Abhi.
7. **Where it runs.**
   - `lab reports` is its own command. `lab run` and `lab sweep` don't need Hypothesis to start.
   - The production run is a test marked `report_gate`. The steps that must stay green deselect it,
     as they do `selection_gate`.
   - It has its own CI job, `lab-reports`. That job triggers on `packages/decision`, on `worker.py`,
     `calls.py` and `company.py` in `apps/core`, on the voice service's `app.py`, and on `evals`.
     It carries a known-failures list (D3).

## C. The fuzz generator: pinned draws and its boundaries (B5, B6, B7, B10)

1. **Pinned draws everywhere.** `lab fuzz --search first`, the property tests and every lab sample
   use spec 0046's `pinned_draws()`, so a seed gives the same worlds under pytest, the CLI and a
   worker (B10).
2. **New knobs.**
   - A request without a thread.
   - A requested cutoff earlier than the request.
   - Ages exactly at `min_age_days`, at the 365-day established line and at the same-subject
     window.
   - Near misses seen at the same time.
   - A late sighting on an on-time document.
   - An upper-case "FAX" role. Spec 0046's D03 was never found because the generator never draws
     one.

   Each of B5's seven deliberate changes is caught (differential, over 2000 pinned worlds at seed
   0), with one test per change.
3. **The coverage floor.**
   - `context_label_on_provable` counts a label on the pick only.
   - The eligible-number floor leaves out the planted real number, and keeps its 50% bar. Counted
     that way it was 27% (B11), so the generator changes until it reaches 50%. If it can't without
     distorting the worlds, the build stops and reports. The bar is never lowered to fit (decided
     Nov 10).
   - A test shows the first-cut generator fails the coverage check, as 0033's acceptance says.
4. **B7's minors.**
   - `lab run` works without the dev-only `hypothesis`.
   - The unseeded property test gets a fixed seed.
   - New tests for the near-miss edges at exactly 365 days and for a same-time tie.
   - The schema is found from the package, not a fixed relative path.
   - `invariants.py` stops importing worldgen's private `_address`.
   - `lab frontier` prints its grid.
   - The new commands go in `AGENTS.md` and the evals README.

## D. Landing on main

1. **Renumbering.** Every number below takes the next free one when its PR opens. Every reference
   follows: spec text, FINDINGS and code comments.
   - The lab's `0033`.
   - Its Q60, Q61 and Q65, which collide with main.
   - Spec 0017's Q56, Q57 and Q58, which collide with main's spec 0030.
   - Spec 0046's Q87 to Q91, which collide with spec 0045, written first.
2. **PR order.** Each PR is stacked on the one before, and kept as close to 400 changed lines as its
   parts allow. Any of them may be split further. The voice and fuzz labs never share a PR.
   1. `apps/core`: pull out `call_facts`, with no change in behavior (Abhi reviews). `apps/voice`:
      make the report helpers public.
   2. The voice lab (0017), with part A. Nothing from the voice lab merges until part A's PR is
      approved, so main never carries the lab C9 criticizes. FINDINGS lands here and each later PR
      updates it.
   3. The fuzz lab's core (the renumbered 0033): the generator, the checks, freezing, `lab fuzz`,
      the frozen worlds and the `lab-fuzz` job, with part C.
   4. The sweeps: `lab frontier` and `lab overblock`.
   5. Spec 0046's search that keeps going, then its guided search and its measurement. `lab-fuzz`
      switches to `--search archive` here. It needs that search before it can carry its
      known-failures list, since today's search stops at the first hit, which is always a known
      one (B3).
   6. Part B, `lab reports`.
3. **A known-failures list per job (Q101, decided).** `lab-fuzz` is red today (8 of the 9 frozen
   worlds still fail), and `lab-reports` and the A10 voice scenario will be red at once. All of them
   run on other owners' PRs. So each job reads a strict list, `evals/known-failures.json`.
   - **What an entry is:** a frozen case or scenario, the labels it fails with, its finding (A1,
     A10, ...), and the owner.
   - **A listed entry must still fail with those labels.** One that starts passing turns the job
     red, with a message to take it off the list, so a fix is noticed.
   - **Anything unlisted that fails turns the job red:** a frozen case, a scenario, or a new search
     hit.
   - **A new search hit is known** only when every label it shows belongs to some listed entry.
     That costs something: a new defect that shows only an already-listed label is hidden until
     its entry is fixed. The job reports how many such hits it saw.
   - **Changing the list:** an entry is added only with its finding written in FINDINGS. An entry
     is removed only when the job says it now passes.
   - Sami decided this on Nov 9. Inesh and Abhi see it in the PRs, since the jobs run on theirs.
4. **Spec 0046's pin.** Tag `e3d57b5` as `lab-0046-select-pin` before any rebase. The planted
   breaks name that commit as the rule under test.
5. **Coordination.**
   - Spec 0043 is being built on `sami/keypad-only-digits-impl` and changes the call flow.
     Whichever of the two lands second refreezes the voice scenarios.
   - When spec 0044 lands, a07's `line_risk` stops being declared.
6. **PRs open only on Sami's OK.** Agents open PRs; a person merges.

## Acceptance

- [ ] The lab gets the call's report and its facts through A1's chain, and `build_facts` is gone.
  The core's and the voice service's tests pass unchanged.
- [ ] With the lab's own detector, each of C9's four blind cases fails `lab run`:
  - a broken digits check;
  - a broken new-number pattern;
  - a removed vendor-name rule;
  - an agent naming a bank and an amount without digits.

  The broken new-number pattern is caught by the gate that fails any agent text. Every honest
  scenario, voicemail and consent refused included, still passes.
- [ ] Every scenario that rests on an uncomputed fact declares it. The loader refuses one that
  doesn't, and the scorecard reports declared scenarios on their own.
- [ ] The oracle imports nothing from `dialback_core`.
- [ ] `lab sweep` sees every rule removed, except the rules in `UNREACHABLE.md`, each with its
  reason.
- [ ] Each item in A6 has a test.
- [ ] Part B's reachable domain can't produce what production never sends.
- [ ] Each of F1 to F3 fails on its broken double. F3 passes the strict reference matcher. Each then
  runs on production at seed 0 on 2000 reports, and its findings are frozen and re-run.
- [ ] One seed gives the same worlds under pytest, the CLI and a worker.
- [ ] Each of B5's seven changes is caught, and each item in C3 and C4 has a test.
- [ ] Within the lab's own spec and question numbers, none collides with main. Collisions with other
  branches are checked by hand when each PR opens (D1).
- [ ] The tag `lab-0046-select-pin` exists on `e3d57b5`.
- [ ] Each job's known-failures list holds. Every listed entry still fails with its labels, nothing
  unlisted fails, and a listed entry that passes, or an unlisted failure, turns the job red.
- [ ] Each PR's commands pass with those lists.

## Tests (written first)

| Item | Test |
|---|---|
| A1 | `apps/core/tests/test_call_facts.py` (each fact; the worker's facts are unchanged), `apps/voice/tests/test_report_body.py`, `evals/tests/test_runner.py::test_lab_chains_production_code` |
| A2 | `evals/tests/test_breaks.py::test_lab_sees_what_the_guard_misses` (the four cases), `::test_honest_fixed_lines_pass` |
| A3 | `evals/tests/test_scenario.py::test_uncomputed_fact_must_be_declared`, `evals/tests/test_gates.py::test_declared_scenarios_are_reported_apart` |
| A4 | `evals/tests/test_oracle.py::test_oracle_imports_no_core`, `::test_company_truth_disagreement_is_reported` |
| A5 | `evals/tests/test_rule_sweep.py::test_every_rule_is_seen_or_listed` |
| A6 | `evals/tests/test_gates.py::test_disclosure_says_ai_and_recorded`, `evals/tests/test_scenario.py::test_loader_refusals`, `evals/tests/test_ci_wiring.py` |
| B | `evals/tests/test_reports.py::test_reachable_domain_matches_production`, `::test_each_check_catches_its_broken_double`, `::test_reference_matcher_passes_f3`, `::test_checks_on_production` (`report_gate`), `::test_findings_are_frozen_and_rerun` |
| C1 | `evals/tests/test_worldgen.py::test_a_seed_pins_the_worlds` (run in a subprocess and in process) |
| C2 | `evals/tests/test_breaks_selection.py` (one test per B5 change) |
| C3, C4 | `evals/tests/test_worldgen.py::test_first_cut_generator_fails_coverage`, plus one test per item |
| D1 | `evals/tests/test_numbering.py::test_lab_numbers_do_not_collide_with_main` |
| D3 | `evals/tests/test_known_failures.py::test_a_passing_entry_turns_red`, `::test_an_unlisted_failure_turns_red`, `::test_a_hit_with_only_listed_labels_is_known_and_counted`, `::test_an_entry_needs_a_finding` |

## Out of scope

- The end-to-end runner (the next step): the entry points, hold, ingestion, release bot and
  evidence chain.
- The live Claude agent on the call (step 3).
- Scale and statistics (step 4).
- Ingestion's tests against any reader: their own spec.
- Real PDFs and the LLM reader: product work for the ledger.
- Fixing any product defect the lab finds. Findings go to their owners.

## Open questions

Sami approved the spec and every call in it as written on Nov 9. One question is still open, and
it is Abhi's:

- **Q102 (Abhi, open).** Whether Abhi pulls `call_facts` out of `worker.py`, or Sami does it as
  partner, with Abhi's review. Until he answers, `call_facts` may be drafted on a branch for his
  review, but no PR touching `apps/core` opens without his OK.
- **Q101, resolved (Sami, Nov 9):** a strict known-failures list for each job, as D3 describes.
- **Q103, resolved:** `UNREACHABLE.md` is reviewed by Abhi, who owns the decision rules.
- **Q104, resolved:** the report fuzzer's findings live in `evals/reports-generated/`.
- **Q105, resolved for now:** the company phrase bank is hand-written, so it covers only phrasings
  we think of. A model-written bank comes with the live agent.

## Commands

```bash
cd evals && uv run pytest -q -m "not selection_gate and not report_gate"
cd apps/core && uv run pytest -q
cd apps/voice && uv run pytest -q
uv run --no-project --with ruff ruff check apps packages scripts evals
uv run lab run --split voice
uv run lab sweep
uv run lab reports --examples 2000 --seed 0
uv run lab fuzz --examples 2000 --seed 0 --out evals/corpus-generated
```
