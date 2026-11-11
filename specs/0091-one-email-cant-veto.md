# 0091: One email can't veto a number

**Owner:** Sami (in `packages/ledger`)  **Slice:** evidence  **Target day:** not set
**Builds on:** spec 0079 (`sami/0079-guard`, #121). This branch (`sami/label-bar`) carries both
specs, with main merged in, and lands in place of #121, after the contract PR #120.
**Status:** approved by Sami, Nov 10; the fourth revision of rule 3 approved Nov 11 (Q162). The rule
below is that revision (prototype W5), and so are the measured numbers.

## Goal

Today one sighting that labels a number as someone else's (a bank's, a customer's, `unknown`) or as
a fax refuses that number, however new the sighting and however much evidence stands against it
(spec 0021). One email can then move the call to a planted number (FINDINGS A1, A4) or block it
(A2, and anyone who knows a vendor's public number). The call is the real test. It only needs the
number to reach the vendor, and one email says nothing about that. So: **it takes as much evidence
to take trust away from a number as to give it.** It serves R1 (never verify an attack) and R7
(fail closed), and stops one email from forcing every case to a person.

Why now: the email door (specs 0084 to 0088) files change-request emails into the ledger, which
makes A1 reachable with live mail.

## The rule

Changes to today's rule, `_select_v1`, applied inside spec 0079's guard. Everything not named here
is today's: R2 (first seen in the change context), hidden text, the change context and its
lookalike pull for support, near-miss logic, ranking, settings.

1. **A label against a number** is a visible sighting whose party isn't `vendor`, or whose role is
   `fax` in any case.
2. **Support counts only the vendor's sightings.** Age, source types and ranking read only sightings
   labeled the vendor's and not a fax. Today a sighting that says "the bank's line" still counts as
   evidence for the number; the veto hid that. The near-miss check ("which number is older") and
   the displayed `first_seen_at` keep today's first sighting: every visible sighting outside the
   change context in a counting channel, labeled or not (Q153). Otherwise a real line whose oldest
   sightings carry a label (the stub reader gives a plain "Tel:" line party `unknown`) stops
   anchoring a planted number one digit off it.
3. **A label vetoes only when established.** A label against a number counts toward an established
   veto only if its own document:
   - is in a counting channel (today's `counts`);
   - was received at least `min_age_days` (90) before the request time
     `asked = min(requested_cutoff, request.received_at)`;
   - is not from a lookalike sender.

   With the production setting (90 days), a fresh document fails the age test whoever sent it, and
   an old one counts whatever thread or subject a later request uses. Established: the counted labels span at least `min_source_types`
   (2) channels. Then the labels veto the number (reasons `not_vendor_party`, `fax_line`, as today).
   A case with no request has no established labels. (If an old lookalike email pulls the labeling
   documents into the change context, the guard in rule 5 withholds the case, as spec 0079 does
   today: the labeled number is still refused and nobody else is called.)
4. **Weaker labels are ignored.** They neither veto nor support.
5. **Spec 0079's guard stays.** `select()` calls the longest common prefix of the new rule on the
   case and on the case without the change context (0079's pre-context run, today's cutoff kept).
6. **Kill switch (my call):** `DIALBACK_LABEL_BAR` in `flags.py`, on by default, any unknown value
   on. `DIALBACK_CONTEXT_GUARD=off` stays the full rollback to today's rule, whatever this flag says.
   So there are three states, each with its own `rule_version` (spec 0047's rule): both on is this
   spec; the label bar off is spec 0079 exactly (`ledger-select-v3`); the guard off is `ledger-select-v1`.
7. **`rule_version`:** the next free name when this merges, for "both on" only (spec 0079 has
   claimed v3). `active_rule_version()` names it under the same flags.

## What this changes

| Case (real number 0142, planted 0177) | Today | 0079 | This spec |
|---|---|---|---|
| A1: a label in the change email (three frozen cases) | 0177 | nobody | 0142 |
| The same label in a separate email the day before | 0177 | 0177 | 0142 |
| That label with no plant (an attacker who only wants to block) | nobody | nobody | 0142 |
| An honest request that mentions 0142 (A2) | nobody | nobody | 0142 |
| Vendor email plus a contract calling 0142 a bank's | nobody | nobody | nobody |
| An old "Tel:" line plus one fresh email calling 0142 a fax | 0177 | 0177 | 0142 |
| 0177 older than 0142, two honest warnings, the request replies on one's thread | 0142 | 0142 | nobody (a person checks) |
| 0177 older than 0142, one honest "0177 isn't ours" on file | 0142 | 0142 | **0177** |

**The accepted cost is the last row.** It needs the planted number to outrank the real one: the
vendor's records were already poisoned (K01), and today's rule calls 0177 there too without the
warning. Sami, Nov 10: if the attacker's number is the most trusted, something else already failed.

Measured (Nov 10 and 11). Each version's bars were written before its run (the session scratchpad's
`umeasure/measure_w_plan.md`, addenda 1 to 7). Seed 0's numbers are exploratory; seeds 1 to 3 were
never looked at while revising, and confirm them. "K1" is a world where the rule picks a planted
number and today's rule doesn't.

| Version | What changed | K1, seed 0 (2000 pinned) | Calls nobody | Why it was revised |
|---|---|---|---|---|
| W | weak labels are ignored | 49 (38 distinct) | 733 | a labeled sighting still counted as support |
| W2 | support reads only the vendor's sightings | 37 (28) | 736 | a lookalike's pull undid honest labels |
| W3 | a label is judged by its own document | 37 (28) | 736 | the near-miss anchor was lost (builder check) |
| W4 | the near-miss check keeps today's first sighting | 37 (28) | 743 | one fresh email completed a veto; a request's thread undid warnings (final review) |
| W5 | each counted label is 90+ days old; no request, thread or subject exclusions | 37 (28) | 743 | this spec |

Today's rule calls nobody in 816 of those 2000, spec 0079 alone in 948.
- **Fresh seeds 1, 2 and 3 (W5 inside the guard):** K1 is 14, 38 and 58. Every one is the accepted
  shape: the planted number already outranks the real one, and only weak labels stood against it.
  Calls nobody: 727, 765, 608 (today 808, 809, 664).
- **The final review's two worlds:**
  - One old "Tel:" line plus one fresh email calling the real line a fax: today and 0079 call the
    planted number, this rule the real line.
  - A request replying on an old warning's thread: today calls the real line, and W4 called the
    planted one. This rule withholds, since 0079's guard compares with a run without the thread.
- **Unchanged:**
  - The 20 corpus outcomes and the demo picks. Neither has a label against any number.
  - All 441 label-free worlds give today's output field for field.
  - The builder check's near-miss worlds call nobody.
  - Spec 0021's suite fails only the three tests listed below.
  - Honest over-block rows calling nobody: 13 of 61 (today 29). Planted rows calling the planted
    number: 8 (today 28).
- **Lab metamorphic checks, on what ships (W5 inside the guard), seeds 0 to 3:**
  - The label probe (I12) goes from 380, 497, 511 and 542 failures to 0.
  - `M_lookalike_helped` stays 0.
  - `M_shared_id_helped` goes from 230, 208, 240 and 213 to 271, 228, 263 and 265, all through
    duplicate ids (A5, Q154).
- **The scorecard's search** (`lab fuzz --search archive`, seed 0, 2000 examples, 1622 worlds):
  - A1's label (`M_incontext_label_moved_pick`) flags 356 worlds on main (71391df) and 0 on this
    branch (ca66b6c).
  - `M_shared_id_helped` goes from 168 to 181.
  - The branch adds `I1`, `I4` and `I5` on 9 duplicate-id worlds, listed under A5.
- **Not compared:** draws without pinning changed when the measurement environment was rebuilt on
  Nov 11, so the earlier unpinned figures aren't comparable.

## Contract touched

None (my call): the same outcomes and reasons, and the schema's reason descriptions stay true.
`reasons.py`'s text changes in two places, both because support is now only the vendor's (rule 2):
`too_few_source_types` says "seen as the vendor's" (a number seen in two channels, one calling it a
bank's, now gets that reason), and `younger_than_minimum_age` says "first seen as the vendor's number
too recently" (its age runs from the first sighting labeled the vendor's). `not_vendor_party` and
`fax_line` keep today's sentences: the dashboard serves the current text for every stored selection,
whatever its rule version, and those sentences stay true for all of them. The web data that repeats
them follows.

## Acceptance

- [x] The table's eight cases give the results shown, as fixed worlds.
  (`test_label_bar.py::test_the_table`)
- [x] On every label-free world (generated, corpus, demo), the result is `_select_v1`'s, field for
  field except `rule_version`, `number_selection_id` and `inputs_sha256`, against 0079's frozen copy.
  (`test_label_bar.py::test_no_labels_means_todays_rule`, `::test_corpus_and_demo_worlds_are_todays`)
- [x] Every number today's rule finds eligible, the new rule finds eligible, in the same order.
  (`test_label_bar.py::test_todays_eligible_stay_eligible_in_order`)
- [x] A real line one digit from a planted number still anchors it when the real line's oldest
  sightings carry a label: the builder check's worlds 1, 1b and 1c call nobody, as today.
  (`test_label_bar.py::test_labeled_real_line_still_anchors_its_near_miss`)
- [x] Spec 0079's guard: the call list is a prefix of the new rule's on the case.
  (`test_select_properties.py::test_new_rule_is_a_prefix_of_the_inner_rule`)
- [x] A weak label leaves the contact point `eligible` in `_store` (FINDINGS A26 for weak labels).
  (`test_label_bar.py::test_weak_label_keeps_contact_point_eligible`, Postgres)
- [x] The three flag states of rule 6 give their rules and `rule_version` names; default and unknown
  values give this rule. (`test_label_bar.py::test_three_flag_states_name_their_rule`,
  `::test_unknown_flag_values_run_this_rule`, `test_flags.py::test_label_bar_defaults_on`)
- [x] Failure path: a case with no request still gives `no_provable_number` (R7); a world where
  labels are established still refuses the number with today's reason.
  (`test_label_bar.py::test_no_request_still_fails_closed`, `::test_two_old_sources_veto`)

## Tests (written first)

| Item | Test |
|---|---|
| The eight cases | `packages/ledger/tests/test_label_bar.py::test_the_table` |
| The fourth revision (Q162) | `test_label_bar.py::test_one_fresh_email_cannot_complete_a_veto`, `::test_a_request_cannot_erase_old_warnings_by_its_thread`, `::test_the_requested_cutoff_bounds_the_label_age` |
| Label-free equals today | `test_label_bar.py::test_no_labels_means_todays_rule` (with 0079's `select_v1_frozen.py`) |
| The corpus and demo worlds equal today | `test_label_bar.py::test_corpus_and_demo_worlds_are_todays` |
| Eligibility only grows | `test_label_bar.py::test_todays_eligible_stay_eligible_in_order` |
| Near-miss anchor kept | `test_label_bar.py::test_labeled_real_line_still_anchors_its_near_miss` (worlds 1, 1b, 1c) |
| Established labels still veto | `test_label_bar.py::test_two_old_sources_veto`, `::test_attacker_cannot_unestablish` (lookalike pull, same subject) |
| Storage | `test_label_bar.py::test_weak_label_keeps_contact_point_eligible` (Postgres) |
| Flag | `packages/ledger/tests/test_flags.py::test_label_bar_defaults_on` |

**Existing tests that change, and why (none is weakened silently):**
- `test_select_properties.py::test_a_banks_or_customers_number_is_refused`, `::test_a_fax_line_is_refused`,
  `::test_a_fax_label_in_any_case_is_a_fax`: the number is still refused, but for
  `too_few_source_types`, since the labeled sighting no longer counts as support. Each gains an
  established-label twin that asserts today's reason.
- 0079's `test_select_guard.py::test_change_context_label_is_withheld` becomes
  `..._is_ignored`: the real number is picked.
- 0079's `test_select_guard.py::test_lookalike_hole_never_calls_the_plant`: the A6 world now calls
  the plant, the accepted cost above. The test asserts that and cites this spec.
- 0079's prefix property becomes "a prefix of the new rule".
- The lab: `I3_nonvendor_or_fax_eligible` follows rule 3; A1's known-failure entries come off.
- Found while building (this list was short):
  - **Worlds that no longer withhold:** tests of how `withheld` is handled move to a world that still
    withholds with both flags on, with their assertions kept: `test_risk.py::test_a_vendor_the_rule_withholds_needs_a_person`,
    core's `test_api.py::test_the_vendor_risk_list_says_when_a_person_is_needed` and
    `test_worker.py::test_withheld_selection_goes_to_review`, and 0079's
    `test_withheld_keeps_contact_point_status` and `test_flag_default_and_unknown_values_give_the_new_rule`.
  - **Tests that mean spec 0079 alone:** `test_backups_are_cut_to_the_agreed_prefix`,
    `test_no_pick_today_stays_no_provable_number` and `test_context_that_moves_nothing_changes_nothing`
    run with the label bar off, and each gains a check with it on.
  - **The version name:** `test_store.py`'s gate and `corpus-results.json` read `ledger-select-v4`.
  - **Planted breaks:** two entries in `evals/tests/test_breaks_selection.py` patch the near-miss
    loop's new key, and are still caught at the same worlds.
  - **Lab tests that need a label to refuse a number** run on today's rule alone (the `_today_rule`
    fixture, `DIALBACK_CONTEXT_GUARD=off`), with their assertions kept: `test_invariants.py::test_a_rule_that_refuses_the_case_passes_i12`,
    `::test_fail_closed_fix_adds_no_violation_today_lacks`, `::test_context_blind_fix_opens_the_lookalike_hole`
    and `::test_context_blind_rule_passes_the_planted_bank_label_world`, and `test_docgen.py::test_g7_sweeps_every_layout_of_the_real_number`
    and `::test_stub_report_has_every_count`.

## Out of scope

- A5 (duplicate document ids), which this measures but doesn't fix.
- **Past change requests as support (Q152).** Under the email door, every request is filed in the
  ledger, so an attacker's earlier requests are history for later cases and could count as support
  for their number. Read in spec 0087, not run.
- Showing ignored labels to the person on the case page (needs a contract field).
- The stub reader's party `unknown` (a plain "Tel:" line) still counts as a label against, as
  today. Treating it as neutral is a separate change.
- The call side: line takeover (A7) and insiders.

## Open questions

- **Q151, resolved:** the accepted cost (the last row). Sami, Nov 10.
- **Q152, open:** should documents filed as any case's change request never count as support?
- **Q153, resolved:** the near-miss check keeps today's "which number is older". Sami, Nov 10
  ("Yes, fix it"); measured again as W4 above.
- **Q154, resolved: accepted.** `M_shared_id_helped` rises from 230 to 271, all through A5's latent
  duplicate ids; production can't file two documents under one id, and A5 gets its own fix. Sami,
  Nov 10. While building, the same gap also showed up as `I1`, `I4` and `I5` hits in the archive
  search (a number a weak label used to refuse becomes eligible, so the lab's worst-of-both-copies
  view of a duplicate id now applies to it). They are listed under A5 too.
- **Q161, resolved: the middle option is rejected.** It used this rule only where today's rule
  calls nobody, and otherwise called what both rules agree on, else nobody. In generated worlds it
  sent the honest-warning case (the table's last row) to a person instead of the planted number.
  But an attacker who adds one weak label to the real number ("Fax: 0142" in the change request)
  makes today's rule call nobody, and it then calls the planted number too (predicted, then run).
  So its gain was an artifact of random test labels, and it costs more manual reviews. Sami, Nov 10:
  keep this rule (FINDINGS A4).
- **Q162, resolved:** the fourth revision: each counted label must itself be 90+ days old, and the
  request/thread/subject exclusions go. Sami, Nov 11.

## Commands

```bash
cd packages/ledger && uv run pytest -q
uv run lab fuzz --examples 2000 --seed 0
uv run --with jsonschema --with rfc8785 --with pyyaml --with openapi-spec-validator python packages/contracts/scripts/validate.py
```
