# One Email Can't Veto a Number: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A label against a number (a bank's, a customer's, `unknown`, or a fax) can refuse the number
only when it meets the same bar as trust. Weaker labels neither refuse nor support it, and spec 0079's
guard stays on top.

**Architecture:**
- **The rule:** today's rule body becomes `_select_rule(..., label_bar)`, and `_select_v1` calls it with
  `label_bar=False`. With the bar on, support (age, source types, ranking) reads only the vendor's
  sightings, and a label refuses only when established. The near-miss check and `first_seen_at` keep
  today's first sighting.
- **The guard:** `select()` runs spec 0079's guard over `_select_rule(label_bar=label_bar())`.
- **The flags:** a new `DIALBACK_LABEL_BAR` picks among three rule versions.
- **The lab:** I3 learns the same bar.

**Tech Stack:** Python 3.12, pytest, Hypothesis, uv. Postgres for the storage tests: on this Mac, run
them with Docker `postgres:16` plus a psql shim.

**Spec:** `specs/0091-one-email-cant-veto.md` (approved Nov 10). The measurements behind it are in
FINDINGS A4 on `sami/findings-rule-t`. The prototype is `umeasure/select_w4.py` in session
`c52163f2`'s scratchpad: read it, don't copy it.

## Global Constraints

- **ASCII hyphens only:** no em or en dashes in code, comments, docs or commit messages.
- **Tests:**
  - Only the tests the spec lists under "Existing tests that change" may change.
  - Any other existing test that fails means stop and report it. Don't edit it.
  - Never skip or loosen a test, including `test_every_test_passes_a_reader_to_ingest`.
- **Settings:** come from `Settings`: `min_source_types` 2, `min_age_days` 90,
  `same_subject_window_days` 30. Never hard-code them.
- **Flag:** `DIALBACK_LABEL_BAR` is read only in `flags.py`. It is on by default and for any
  unknown value; only `off` (trimmed, any case) turns it off.
- **Rule versions:**

  | `DIALBACK_CONTEXT_GUARD` | `DIALBACK_LABEL_BAR` | `rule_version` |
  |---|---|---|
  | off | any | `ledger-select-v1` (`RULE_VERSION_V1`) |
  | on | off | `ledger-select-v3` (`RULE_VERSION_GUARD`) |
  | on | on | `ledger-select-v4` (`RULE_VERSION`) |

  v4 is the next free name; v2 is spec 0047's.
- **No contract change:** `rule_version` is free text in the schema and the database.
- **Phone numbers:** fictional only (NPA-555-0100 to 0199).
- **Git:**
  - Sync check before every commit and push.
  - Stage files by name.
  - Every commit ends with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
  - Push after each commit.
  - Open no PR without Sami's go-ahead.
  - Use `--force-with-lease` only, after parking a backup branch.
- **Worktrees:** paths must not contain `+` (it breaks `packages/contracts/scripts/validate.py`).

## Review Focus

Five inputs the spec implies but no table row exercises, each pinned by a test in Task 2:

1. **Casing.** A differently-cased party (`Vendor`) or role (`FAX`) is a label against, as spec
   0021 says. It is ignored when weak and never counts as support.
2. **Hidden text.** A labeled sighting in hidden text (white, tiny, off the page) neither supports
   nor opposes.
3. **Other domains.** Labels in email from outside the vendor's domains (a bank's own domain) never
   count toward an established veto.
4. **Mixed kinds.** A bank label in one source type and a fax label in another, both old, establish
   together, and the number is refused with both reasons.
5. **Late documents.** A labeling document received after the request but recorded before the case
   opened doesn't count toward an established veto.

---

### Task 1: Bring main into spec 0079's branch

This changes PR #121's branch. Approving this plan approves that.

**Files:**
- Resolve: `packages/ledger/src/dialback_ledger/flags.py`, `packages/ledger/tests/test_store.py`
- Modify: the ingestion calls below, `evals/known-failures.json`, and the lab files A25 names
- Test: everything listed under "Commands" in specs 0079 and 0091

**Interfaces:**
- Produces: `sami/0079-guard` with `origin/main` merged in and every suite green. Later tasks branch
  from it.

- [ ] **Step 1:** Run a sync check. Park `backup/0079-guard-pre-main` at the branch tip. Use a
  worktree without `+` in its path. Then run `git merge origin/main`.
- [ ] **Step 2:** Resolve the two conflicts by keeping both sides. `flags.py` keeps both
  `context_guard()` and `anthropic_api_key()`. `test_store.py` keeps both imports,
  `from dialback_ledger.select import RULE_VERSION` and `from dialback_ledger.reader import StubReader`.
- [ ] **Step 3:** Run `cd packages/ledger && uv run pytest -q tests/test_ingest.py::test_every_test_passes_a_reader_to_ingest`.
  Expected: FAIL, listing `apps/core/tests/test_api.py`, `apps/core/tests/test_worker.py`,
  `packages/ledger/tests/test_demo_picks.py`, `test_risk.py`, `test_select_guard.py` and possibly
  `test_store.py`.
- [ ] **Step 4:** Add `reader=StubReader()` to each call it lists. That is the reader they already
  use by default.
- [ ] **Step 5:** Run the ledger suite (with Postgres), `cd apps/core && uv run pytest -q`, and the
  lab: `cd evals && uv run pytest -q` and `uv run lab fuzz --examples 2000 --seed 0`.
  Expected: only the lab's spec 0079 coordination fails, as FINDINGS A25 says.
- [ ] **Step 6:** Make that coordination and nothing else:
  - remove the four A1 entries from `evals/known-failures.json`, since they now pass;
  - teach the lab's schema check and selection invariants the `withheld` outcome.

  Re-run Step 5. Expected: all green. Stop and report any other failure.
- [ ] **Step 7:** Commit the merge with the resolution summary in the message, then push `sami/0079-guard`.

### Task 2: The label bar inside the rule

**Files:**
- Modify: `packages/ledger/src/dialback_ledger/select.py` (`_select_v1` becomes `_select_rule`, plus a wrapper)
- Create: `packages/ledger/tests/test_label_bar.py`

**Interfaces:**
- Consumes: `_world.world`, `frozen`, `calls`, and `provable`/`in_thread` from `test_select_guard.py`
  (move both helpers into `_world.py` and import them from there).
- Produces:
  - `_select_rule(*, case_id, dataset_id, vendor_id, vendor_domains, request, requested_cutoff, case_opened_at, documents, sightings, settings=DEFAULT, rule_version: str, label_bar: bool) -> dict`;
  - `_select_v1(**case, rule_version=RULE_VERSION_V1) -> dict`, which calls `_select_rule(label_bar=False)`.

- [ ] **Step 1:** Create a branch `sami/label-bar` from the `sami/0079-guard` Task 1 pushed. Write
  `test_label_bar.py`. It calls `_select_rule(**w, rule_version="t", label_bar=True)` as `inner(w)`.
  Use REAL `+14155550142`, PLANTED `+16455550177`, P `+14155550143`, X `+13035550101`, and the
  `_world` row format, one document per row.

  | Test | World | Assert |
  |---|---|---|
  | `test_a_weak_label_in_the_request_thread_is_ignored` (parametrized bank, fax, bare) | `provable(REAL,200) + provable(PLANTED,150) + [in_thread(REAL, role=..., party=...)]` | `frozen` picks PLANTED; `inner` picks REAL |
  | `test_a_weak_label_the_day_before_is_ignored` | the same, with the label row in its own thread `t-sep`, subject `One more thing`, `days_before` 1 | `frozen` PLANTED; `inner` REAL |
  | `test_a_label_alone_cannot_block` | `provable(REAL,200)` + that day-before row | `frozen` calls nobody; `inner` REAL |
  | `test_a_labeled_sighting_is_not_support` | REAL in a vendor email (party vendor) and a contract (party bank), both 200 days | both call nobody; `inner`'s REAL reasons are exactly `["too_few_source_types"]` |
  | `test_two_old_sources_veto` | `provable(REAL,400) + provable(X,500)` + X in a contract (150d, party unknown) and a listing (120d, party bank) | `inner` refuses X with `not_vendor_party`; `inner` picks REAL |
  | `test_attacker_cannot_unestablish` | the last world, plus a lookalike email at 160d | `inner` still refuses X and picks REAL |
  | `test_a_label_under_the_request_subject_does_not_establish` | as `test_two_old_sources_veto`, but the 120d listing's subject is `Updated banking details` and it is moved to 20d | X has no veto reason |
  | `test_labeled_real_line_still_anchors_its_near_miss` (worlds 1, 1b, 1c) | the builder check's `q0090/probe.py` worlds: REAL's oldest sightings "Tel:" (`unknown`/`unknown`) or `fax`/`vendor`; P one digit off, contract and listing at 200d | `inner` calls nobody; P keeps `near_miss_of_older_number` |
  | Review Focus 1 to 5 | one world each, as listed there | weak means no veto and no support; established means a veto (item 4: both reasons) |

- [ ] **Step 2:** Run `cd packages/ledger && uv run pytest -q tests/test_label_bar.py`.
  Expected: FAIL (`_select_rule` doesn't exist).
- [ ] **Step 3:** Implement `_select_rule` with `label_bar=False` byte for byte today's body.
  With `label_bar=True`, exactly spec 0091's rules 1 to 4:
  - **Support:** `clean` keeps only sightings with `party == "vendor"` and a role that isn't fax
    (`str(role).strip().lower() != "fax"`).
  - **Near-miss anchor:** the check and `shown` use today's first sighting (every visible sighting
    outside the context in a counting channel, labeled or not). Keep it as `facts[e]["anchor"]`.
  - **Established:** the labels against a number count when their document passes `counts()`, has
    `received_at <= min(requested_cutoff, request.received_at)`, isn't the request, isn't in its
    thread, isn't under its subject within `same_subject_window_days` of that time, and isn't from a
    lookalike sender. They establish when they span `>= min_source_types` channels and the earliest
    is `<= that time - min_age_days`.
  - **The veto:** `not_vendor_party` and `fax_line` are added only when established.

  `_select_v1` becomes a thin wrapper.
- [ ] **Step 4:** Run `uv run pytest -q tests/test_label_bar.py tests/test_frozen_baseline.py tests/test_select_corpus.py`.
  Expected: PASS. `select()` is unchanged, so every other test still passes: run the whole ledger
  suite to confirm.
- [ ] **Step 5:** Commit and push.

### Task 3: The flag, the versions, and the wiring into select()

**Files:**
- Modify: `flags.py`, `select.py` (`select()`, the constants, `active_rule_version()`)
- Modify (spec-listed only): `test_select_properties.py`, `test_select_guard.py`, `test_flags.py`
- Test: `test_label_bar.py`, `test_flags.py`

**Interfaces:**
- Consumes: `_select_rule` from Task 2.
- Produces:
  - `flags.label_bar(env: Mapping[str, str] | None = None) -> bool`;
  - the constants `RULE_VERSION = "ledger-select-v4"`, `RULE_VERSION_GUARD = "ledger-select-v3"` and
    `RULE_VERSION_V1`;
  - `active_rule_version() -> str`, per the table in Global Constraints.

- [ ] **Step 1:** Write the failing tests.
  - **Flags:** `test_flags.py::test_label_bar_defaults_on` and `::test_label_bar_reads_the_environment`,
    mirroring the `context_guard` tests.
  - **Versions:** in `test_label_bar.py`, `test_three_flag_states_name_their_rule` (parametrized over
    the 4 combinations: `select()`'s `rule_version` and `active_rule_version()` follow the table).
  - **The table:** `test_the_table` runs `select()` with both flags on, and with `DIALBACK_LABEL_BAR=off`
    for the 0079 column. It covers spec 0091's six rows, including the last: the planted number is
    called, the accepted cost Q151 records.
  - **Properties** (Hypothesis, `any_rows`, 300 examples):
    - `test_no_labels_means_todays_rule`: rows mapped to party `vendor` and a role that isn't fax.
      `inner` equals `frozen`, and `select()` with the bar on equals the bar off, except
      `rule_version`, `number_selection_id` and `inputs_sha256`.
    - `test_todays_eligible_stay_eligible_in_order`: every number `frozen` finds eligible, `inner`
      finds eligible, in the same relative rank order.
- [ ] **Step 2:** Run them. Expected: FAIL.
- [ ] **Step 3:** Implement `label_bar()`, the constants and `active_rule_version()`.
  `select()` follows the table: guard off runs `_select_v1`. Otherwise the inner rule is
  `_select_rule(label_bar=label_bar())`, used for both of the guard's runs, named by the active version.
- [ ] **Step 4:** Make exactly the spec's listed changes to existing tests:
  - **The three label property tests**
    (`test_a_banks_or_customers_number_is_refused`, `test_a_fax_line_is_refused`,
    `test_a_fax_label_in_any_case_is_a_fax`): each still asserts the refusal, now with reason
    `too_few_source_types`. Each gains an established-label twin (contract and listing, `days_before`
    200 and 150) asserting today's reason.
  - **`test_change_context_label_is_withheld`:** renamed `..._is_ignored`. It asserts REAL is picked
    with the label bar on, and keeps today's assertions under `DIALBACK_LABEL_BAR=off`.
  - **`test_lookalike_hole_never_calls_the_plant`:** asserts that X is called with the label bar on
    (spec 0091, the accepted cost), with a comment citing it. With the bar off it keeps today's
    assertions.
  - **The prefix property:** `prefix_holds` compares with the inner rule on the case instead of
    `frozen`. `test_the_prefix_check_fails_a_rule_that_skips_the_comparison` uses the same baseline.
  - **Version checks:** tests that assert `RULE_VERSION` for spec 0079 alone set
    `DIALBACK_LABEL_BAR=off` and assert `RULE_VERSION_GUARD`.
- [ ] **Step 5:** Run the whole ledger suite (with Postgres) and `cd apps/core && uv run pytest -q`.
  Expected: PASS. Stop and report any failure in a test the spec doesn't list (for example
  `test_risk.py`).
- [ ] **Step 6:** Commit and push.

### Task 4: Storage, reason text, and the web's data

**Files:**
- Modify: `packages/ledger/src/dialback_ledger/reasons.py`, `apps/web/src/data/corpus-results.json` (regenerated)
- Test: `test_label_bar.py` (Postgres)

**Interfaces:**
- Consumes: `select_number` and `_store` (unchanged).

- [ ] **Step 1:** Write `test_weak_label_keeps_contact_point_eligible`, modeled on 0079's
  `test_withheld_keeps_contact_point_status`. Ingest a world where REAL has a weak label, select, and
  assert that REAL's `ledger.contact_points.status` is `eligible` with no exclusion reason.
  Run it. Expected: PASS once Task 3 is in. This pins FINDINGS A26 for weak labels.
- [ ] **Step 2:** Set `REASON_TEXT` exactly:
  - `too_few_source_types`: "Not provable: it wasn't seen as the vendor's number in at least two
    independent channels before the request (all email counts as one channel, and the old number on
    file never counts)."
  - `not_vendor_party`: "Not provable: documents from at least two independent channels, months
    before the request, label it as someone else's number, such as a bank's or a customer's."
  - `fax_line`: "Not provable: documents from at least two independent channels, months before the
    request, label it as a fax line."
- [ ] **Step 3:** Run `uv run python scripts/corpus/results.py`. Expected: `corpus-results.json`
  changes only in the reason text and `rule_version` (`ledger-select-v4`). Review the diff by hand.
  Then run `pnpm typecheck` and the web tests.
- [ ] **Step 4:** Commit and push.

### Task 5: The lab learns the bar

**Files:**
- Modify: `evals/src/dialback_evals/invariants.py` (I3), `evals/known-failures.json` (only if it says so)
- Test: `evals/tests/test_invariants.py`

**Interfaces:**
- Produces: `_established_against(f: _Frame, n, world: World) -> bool` in `invariants.py`. It
  re-derives spec 0091's rule 3 from the lab's own frame and never imports the ledger's helper, so
  the oracle stays independent.

- [ ] **Step 1:** Write the failing tests:
  - `test_i3_ignores_a_weak_label`: an eligible number with one fresh bank label outside the context
    gets no I3.
  - `test_i3_flags_an_established_label`: rewrite an honest output so that a number with two old
    labels against it reads eligible, and assert `I3_nonvendor_or_fax_eligible`.
- [ ] **Step 2:** Run `cd evals && uv run pytest -q tests/test_invariants.py`. Expected: the first
  test FAILS.
- [ ] **Step 3:** Change I3 to fire only when `_established_against` is true.
- [ ] **Step 4:** Run `cd evals && uv run pytest -q` and `uv run lab fuzz --examples 2000 --seed 0`.
  Expected: green. More `M_shared_id_helped` hits are known and counted (A5, Q154). Stop and report
  any unlisted failure; don't add it to the list.
- [ ] **Step 5:** Add one status line to FINDINGS A1 and A4 on this branch ("fixed by spec 0091
  when it merges"). Commit and push.

### Task 6: Whole-branch verification

- [ ] **Step 1:** Run every command in spec 0091 and spec 0079, plus
  `uv run --with jsonschema --with rfc8785 --with pyyaml --with openapi-spec-validator python packages/contracts/scripts/validate.py`.
  Expected: all green. `test_select_corpus.py` and `test_demo_picks.py` are unchanged.
- [ ] **Step 2:** Tick spec 0091's acceptance boxes, each with the test that proves it. Push.
- [ ] **Step 3:** Ask Sami about the PR. Don't open it unasked.
