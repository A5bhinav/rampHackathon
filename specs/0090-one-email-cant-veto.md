# 0090: One email can't veto a number

**Owner:** Sami (in `packages/ledger`)  **Slice:** evidence  **Target day:** not set
**Builds on:** spec 0079 (`sami/0079-guard`, #121). This lands after it, stacked on its branch once
main is merged into that branch (the lab lives on main; the branch is 36 commits behind).
**Status:** draft. Q153 and Q154 are open; the measured rule changes if Q153 is answered yes.

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
   evidence for the number; the veto hid that. The near-miss check's "which number is older" is
   Q153 (proposed: today's, every visible sighting outside the change context in a counting
   channel, labeled or not).
3. **A label vetoes only when established.** The labels against a number veto it (reasons
   `not_vendor_party`, `fax_line`, as today) only if their documents span at least
   `min_source_types` (2) source types, and the earliest is at least `min_age_days` (90) before the
   request. A labeling document counts only if it is in a counting channel (today's `counts`), and
   is received at or before the request, is not the request, is not in its thread, is not under its
   subject within 30 days of the request, and is not from a lookalike sender. Those facts belong to
   the document, so the lookalike pull can't undo an honest label, and "the request" means
   `min(requested_cutoff, request.received_at)`. (If an old lookalike email pulls the labeling
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
| 0177 older than 0142, one honest "0177 isn't ours" on file | 0142 | 0142 | **0177** |

**The accepted cost is the last row.** It needs the planted number to outrank the real one: the
vendor's records were already poisoned (K01), and today's rule calls 0177 there too without the
warning. Sami, Nov 10: if the attacker's number is the most trusted, something else already failed.

Measured (Nov 10; exploratory: one seed, fixture dates, random labels; FINDINGS A4 and the session
scratchpad's `umeasure/measure_w_plan.md`, rule W3):
- **Picks a planted number where today's rule doesn't:** 37 of 2000 pinned generated draws (28
  distinct), all that last row's shape, and the A6 world's 3 probes.
- **Calls nobody:** 736 of 2000 (today 816, 0079 948). Honest over-block rows that call nobody: 13 of
  61 (today 29).
- **Unchanged:** the 20 corpus outcomes and the demo picks (neither has a label against any number,
  so they can't test this). On all 441 label-free worlds the output is today's, field for field.
- **Lab metamorphic checks (2000 pinned), on the inner rule alone, not yet inside the guard:** the
  label probe (I12) goes from 380 failures to 0 (this lab copy; spec 0079 counted 442 on a later
  one), and `M_lookalike_helped` stays 0. `M_shared_id_helped` goes from 230 to 263, over the bar
  set before the run; every extra case is A5's latent first-sorting duplicate id, which
  production can't reach (ids are primary keys). Q154.
- **A defect found by the builder check (Nov 10), not in the numbers above:** rule 2 as measured
  also applied to the near-miss check. A real line whose oldest sightings carry a label (the stub
  reader gives a plain "Tel:" line party `unknown`) then stops anchoring a planted number one
  digit off it, and the planted number is called where today's rule calls nobody. It breaks spec
  0021's properties 5 and 6 on hand-built worlds. Q153.
- **Still to measure:** the final rule inside the guard, after Q153.

## Contract touched

None (my call): the same outcomes and reasons, and the schema's reason descriptions stay true.
`reasons.py`'s text changes: `not_vendor_party` and `fax_line` say the labels are established, and
`too_few_source_types` says "seen as the vendor's" (a number seen in two channels, one calling it a
bank's, now gets that reason). The web copy that repeats them follows.

## Acceptance

- [ ] The table's six cases give the results shown, as fixed worlds.
- [ ] On every label-free world (generated, corpus, demo), the result is `_select_v1`'s, field for
  field except `rule_version`, `number_selection_id` and `inputs_sha256`, against 0079's frozen copy.
- [ ] Every number today's rule finds eligible, the new rule finds eligible, in the same order.
- [ ] Spec 0079's guard: the call list is a prefix of the new rule's on the case.
- [ ] A weak label leaves the contact point `eligible` in `_store` (FINDINGS A26 for weak labels).
- [ ] The flag off gives spec 0079 exactly; default and unknown values give this rule.
- [ ] Failure path: a case with no request still gives `no_provable_number` (R7); a world where
  labels are established still refuses the number with today's reason.

## Tests (written first)

| Item | Test |
|---|---|
| The six cases | `packages/ledger/tests/test_label_bar.py::test_the_table` |
| Label-free equals today | `test_label_bar.py::test_no_labels_means_todays_rule` (with 0079's `select_v1_frozen.py`) |
| Eligibility only grows | `test_label_bar.py::test_todays_eligible_stay_eligible_in_order` |
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
- **Q153, open:** does the near-miss check keep today's "which number is older" (every clean
  sighting, labeled or not)? Proposed yes: it keeps today's refusals of one-digit-off numbers,
  including a vendor's main line next to its own fax, which is today's behavior. A yes is a third
  fix after two (the plan's stop rule), so it is measured again before this spec is approved.
- **Q154, open:** accept `M_shared_id_helped` rising from 230 to 263, all through A5's latent
  duplicate ids, or fix A5 first?

## Commands

```bash
cd packages/ledger && uv run pytest -q
uv run lab fuzz --examples 2000 --seed 0
uv run --with jsonschema --with rfc8785 --with pyyaml --with openapi-spec-validator python packages/contracts/scripts/validate.py
```
