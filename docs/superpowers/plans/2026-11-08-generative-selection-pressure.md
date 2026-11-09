# Generative Selection Pressure Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Put the pure number-selection rule (`dialback_ledger.select`) under adversarial property-based search in the attack lab, freeze any counterexample as a regression, sweep the K01 known-limit to a frontier table, and sweep single pieces of attacker noise to an over-block table.

**Architecture:** A Hypothesis strategy builds `select()` input worlds parametric over attacker knobs; an invariant oracle asserts the rule's guarantees as universals plus metamorphic inertness properties (attacker-added evidence never helps); a driver shrinks and freezes violations as corpus-shaped JSON; two parametric sweeps measure the minimum attacker effort that reaches a pick (frontier) and which single piece of attacker noise blocks a genuine number on its own (over-block). All of it imports only the pure `select()` and reads its output against `selection.schema.json`. It never edits the rule.

**Tech Stack:** Python 3.13, Hypothesis (already a dev dependency), jsonschema, argparse CLI, pytest.

**Spec:** `specs/0033-generative-selection-pressure.md`

**Base branch:** This plan extends the `evals` lab package and its `lab` CLI (`dialback_evals.cli:main`) from spec 0017, which are not on `origin/main`. The branch `sami/attack-lab-generative` integrated them by merging `sami/attack-lab-voice` (merge `6441eef`), so it carries both the 0017 package and main's ledger rule. Every "Modify" path below (`cli.py`, `pyproject.toml`) refers to the 0017 version of that file.

## Global Constraints

- The lab imports only `dialback_ledger.select` (pure). No database, network, model, live account. **Never edit `packages/ledger/src/dialback_ledger/select.py`** or any rule behavior; a property failure is a finding for the rule's owner, not a fix here.
- `python3 scripts/ci/boundaries.py` must stay green: no new cross-slice import beyond the `dialback-ledger` package dependency.
- Add `dialback-ledger` and `jsonschema>=4` to `evals/pyproject.toml` `dependencies`; `hypothesis>=6` is already in the `dev` group.
- Lab-written findings live in `evals/corpus-generated/`, never in `corpus/` (that is another slice).
- No contract change. `packages/contracts/schemas/selection.schema.json` is read only.
- ruff: line-length 110, target py313. Run `uv run --no-project --with ruff ruff check apps packages scripts evals`.
- Every commit message ends with: `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Plain ASCII hyphen only in anything written (docs, code, comments); no en/em dashes.

## Review Focus

- **No-pick worlds (all candidates rejected).** `select()` returns `outcome: "no_provable_number"` with no `e164`; every invariant and the frontier threshold must treat "no pick" as valid, not crash or mis-flag. Pinned in Task 2 (`test_no_pick_world_is_valid`) and Task 4 (threshold holds when nothing is picked).
- **The near-miss established carve-out.** `select()` does NOT flag `near_miss_of_older_number` when both numbers are at least `established_days` (365) old. Invariant I7 must count such a number as legitimately established, not call its selection a poison-pick. Pinned in Task 2 (`test_established_near_miss_is_not_a_violation`).
- **Threshold boundary cells.** A poison seen in exactly `min_source_types` (2) channels, exactly `min_age_days` (90) days before the cutoff, sits on the eligibility edge; off-by-one here flips the frontier. Pinned in Task 4 (`test_boundary_cells`).
- **Reproducibility of a fuzz failure.** An unpinned seed makes a CI failure unrepeatable. The driver pins the seed, and a found counterexample is frozen so the next run fails deterministically on the file, not on a fresh random draw. Pinned in Task 3 (`test_fuzz_against_a_broken_rule_freezes_a_case`) and Task 5 (`test_lab_fuzz_fails_on_a_violation`).
- **Frozen-world round trip.** A world written to JSON and reloaded must produce byte-identical `select()` inputs, or a regression silently changes meaning. Pinned in Task 3 (`test_frozen_world_round_trips`).

---

### Task 1: The world generator

**Files:**
- Create: `evals/src/dialback_evals/worldgen.py`
- Modify: `evals/pyproject.toml` (add `dialback-ledger`, `jsonschema>=4` to `dependencies`)
- Test: `evals/tests/test_worldgen.py`

**Interfaces:**
- Consumes: `dialback_ledger.select` dataclasses `DocumentRef(id, channel, received_at, sender, thread_id, subject, recorded_at)`, `Sighting(e164, document_id, channel, received_at, visibility, role, party, recorded_at)`, `ChangeRequest(document_id, received_at, thread_id, subject, sender)`, `Settings`, and `select(**kwargs) -> dict`.
- Produces:
  - `@dataclass(frozen=True) World` with fields: `kwargs: dict` (the full `select()` keyword inputs), `poison: frozenset[str]` (e164s the generator seeded as attacker-controlled), `settings: Settings`.
  - `make_world(*, numbers: list[NumberSpec], vendor_domains=("acme.example",), request_at=T0, settings=DEFAULT) -> World` - a deterministic builder (no Hypothesis) used by tests and the frontier. `NumberSpec` is a small dataclass of the per-number knobs: `e164, channels: list[str], lead_days: int, visibility: str, role: str, party: str, sender: str("own"|"lookalike"|"other"), in_thread: bool, subject_in_context: bool, near_miss_of: str | None, is_poison: bool`.
  - `worlds(settings=DEFAULT) -> hypothesis.strategies.SearchStrategy[World]` - a `@composite` strategy drawing 1..6 `NumberSpec`s over the knob domains, at least one a real established vendor number, then delegating to `make_world`.
  - `run(world: World) -> dict` = `select(**world.kwargs)`.
- Notes: `recorded_at` is set before `case_opened_at` for all sightings; `requested_cutoff`/`case_opened_at` derive from `request_at`. Channels drawn from `{email, contract, vendor_portal, listing, paper_mail, vendor_master}`; `visibility` from `{visible, white, tiny, off_page}`; `role` from `{main, ar, billing, fax, unknown}`; `party` from `{vendor, bank, customer, unknown}`.

- [ ] **Step 1: Write the failing test** in `evals/tests/test_worldgen.py`

```python
def test_generated_world_runs_and_validates():
    schema = json.loads((ROOT / "packages/contracts/schemas/selection.schema.json").read_text())
    @given(worlds())
    @settings(max_examples=200, deadline=None)
    def check(world):
        out = run(world)
        assert list(Draft202012Validator(schema).iter_errors(out)) == []
    check()

def test_make_world_is_deterministic():
    spec = NumberSpec(e164="+14155550142", channels=["contract", "listing"], lead_days=400,
                      visibility="visible", role="ar", party="vendor", sender="own",
                      in_thread=False, subject_in_context=False, near_miss_of=None, is_poison=False)
    a, b = make_world(numbers=[spec]), make_world(numbers=[spec])
    assert run(a)["inputs_sha256"] == run(b)["inputs_sha256"]
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd evals && uv run pytest tests/test_worldgen.py -v`
Expected: FAIL (`ModuleNotFoundError: dialback_evals.worldgen`).

- [ ] **Step 3: Add deps and implement `worldgen.py`.** Add `dialback-ledger` and `jsonschema>=4` to `evals/pyproject.toml` `dependencies`, then `uv sync --all-packages`. Implement `NumberSpec`, `World`, `make_world`, `worlds`, `run` with the signatures above. `make_world` maps each `NumberSpec` to one `DocumentRef` + one `Sighting` (lookalike sender -> a near-lookalike domain of `vendor_domains[0]`; `in_thread`/`subject_in_context` set the doc's `thread_id`/`subject` to the request's; `near_miss_of` perturbs one digit of the referenced e164).

- [ ] **Step 4: Run to verify it passes**

Run: `cd evals && uv run pytest tests/test_worldgen.py -v`
Expected: PASS (both tests).

- [ ] **Step 5: Commit**

```bash
git add evals/src/dialback_evals/worldgen.py evals/tests/test_worldgen.py evals/pyproject.toml uv.lock
git commit  # "lab: the select() world generator (spec 0033, Task 1)"
```

---

### Task 1b: Per-sighting knobs, the extra knobs, and the coverage test

Added after the first cut (spec 0033, Q63; ledger line "Task 1: Ruling: reopened"). The first cut gave each number one shared profile and drew no deliberate near-miss pairs.

**Files:**
- Modify: `evals/src/dialback_evals/worldgen.py`
- Test: `evals/tests/test_worldgen.py`

**Interfaces:**
- Consumes: Task 1's `NumberSpec`, `World`, `make_world`, `run`, `worlds`; `dialback_ledger.select.near_miss`.
- Produces (Task 1's names unchanged; additions only):
  - `@dataclass(frozen=True) SightingSpec(channel, lead_days, visibility="visible", role="ar", party="vendor", sender="own", in_thread=False, subject="other", recorded_late=False, shared_id=False)`. `sender` in `{own, subdomain, lookalike, combosquat, other}` maps to `ar@acme.example`, `ar@billing.acme.example`, `ar@acrne.example`, `ar@acme-billing.example`, `ar@other.example`. `subject` in `{other, same, re, fwd, external}`; the last three prefix the request's subject with `Re: `, `Fwd: `, `[EXTERNAL] `.
  - `NumberSpec.sightings: tuple[SightingSpec, ...] = ()`. When set, one document per `SightingSpec`; when empty, Task 1's per-number expansion, unchanged, so the deterministic builders in Tasks 2 and 4 keep working. `NumberSpec.sender` also accepts the new sender values.
  - `make_world(..., request_present: bool = True)`. When False: `request=None` and no request document.
  - A `recorded_late` sighting and its document get `recorded_at = case_opened_at + 1 day`. A `shared_id` sighting adds a second `DocumentRef` under the same id with a different subject and sender.
  - `worlds(settings: Settings | None = None)`. With `None` it draws the settings: the v1 defaults half the time, else `min_source_types` 1 to 3, `min_age_days` in {0, 30, 90, 180}, `near_miss_distance` 0 to 2, `same_subject_window_days` in {0, 7, 30, 90}. Request present 9 times in 10. 1 to 5 numbers, each with 1 to 3 independently drawn `SightingSpec`s. 0 to 2 near-miss siblings (one digit off, two adjacent digits swapped, or two digits off) of the real number or of a drawn number. Plus Task 1's clean, established real number.
  - `clean_worlds(settings: Settings | None = None) -> SearchStrategy[World]`. Only clean knobs: own or subdomain sender, not in thread, subject `other`, visible, party `vendor`, role not fax, not late, no shared id, request present. No near-miss pair at the world's `near_miss_distance` (checked with `near_miss`). Plus one number built to meet the L1 premise: the first `min_source_types` counting channels, first seen at least `min_age_days + 30` days before the request.
  - `profile(world: World) -> Counter[str]`: the knob paths present in a world, read from its raw inputs: `no_request`, `late`, `shared_id`, `reply_subject`, `subdomain`, `lookalike`, `near_miss_pair`, `mixed_visibility`, `mixed_party`, `mixed_timing`, `non_default_settings`.

- [ ] **Step 1: Write the failing tests** in `evals/tests/test_worldgen.py`

```python
REASONS = {"first_seen_in_change_context", "too_few_source_types", "younger_than_minimum_age",
           "near_miss_of_older_number", "hidden_text_only", "not_vendor_party", "fax_line"}
KNOBS = {"no_request", "late", "shared_id", "reply_subject", "subdomain", "lookalike", "near_miss_pair",
         "mixed_visibility", "mixed_party", "mixed_timing", "non_default_settings"}

def test_generator_covers_every_reason_and_knob():
    seen, n = Counter(), [0]
    @given(worlds())
    @settings(max_examples=600, deadline=None, derandomize=True, database=None)
    def sample(world):
        n[0] += 1
        out = run(world)
        seen.update({r for c in out["candidates"] for r in c["reasons"]})
        seen.update(k for k, v in profile(world).items() if v)
    sample()
    low = {k: seen[k] / n[0] for k in REASONS | KNOBS if seen[k] / n[0] < (0.05 if k in REASONS else 0.03)}
    assert low == {}, low   # every reason fires in >= 5% of worlds, every knob path in >= 3%

def test_clean_worlds_have_no_attacker_knob():
    from dialback_evals.worldgen import clean_worlds   # local import: the RED run needs only profile()
    @given(clean_worlds())
    @settings(max_examples=300, deadline=None, derandomize=True, database=None)
    def check(world):
        p = profile(world)
        assert not any(p[k] for k in ("no_request", "late", "shared_id", "reply_subject", "lookalike",
                                      "near_miss_pair", "mixed_visibility", "mixed_party"))
        assert all(s.visibility == "visible" and s.party == "vendor" and s.role != "fax"
                   for s in world.kwargs["sightings"])
    check()
```

- [ ] **Step 2: Add `profile(world)` only, leaving generation unchanged**, so the coverage test can run against the first-cut generator.

- [ ] **Step 3: Run to verify it fails for the right reason**

Run: `cd evals && uv run pytest tests/test_worldgen.py -v`
Expected: FAIL. `test_generator_covers_every_reason_and_knob` fails on its floors (the first cut never draws a missing request, late records, shared ids, reply subjects, subdomains, mixed per-sighting values or non-default settings); `test_clean_worlds_have_no_attacker_knob` fails with `ImportError` (`clean_worlds` not yet written).

- [ ] **Step 4: Implement** `SightingSpec`, the per-sighting path in `make_world`, `request_present`, the sender and subject variants, late records, shared ids, the settings draw, near-miss siblings, and `clean_worlds`, with the signatures above.

- [ ] **Step 5: Run to verify it passes, and the whole suite is green**

Run: `cd evals && uv run pytest tests/test_worldgen.py -v` then `cd evals && uv run pytest -q`
Expected: PASS; no earlier test changes.

- [ ] **Step 6: Commit**

```bash
git add evals/src/dialback_evals/worldgen.py evals/tests/test_worldgen.py
git commit  # "lab: per-sighting and extra generator knobs, with a coverage test (spec 0033, Task 1b)"
```

---

### Task 2: The invariant oracle

**Files:**
- Create: `evals/src/dialback_evals/invariants.py`
- Test: `evals/tests/test_invariants.py`

**Interfaces:**
- Consumes: `World`, `make_world`, `run`, `NumberSpec`, `SightingSpec`, `clean_worlds`, `profile` (Tasks 1 and 1b).
- Produces:
  - `violations(world: World, out: dict) -> list[str]` - structural checks (spec invariants I1-I7 and I9), each returning a label on failure: `"I1_incontext_only_eligible"` (a number whose every visible counting sighting is in the change context is eligible), `"I2_hidden_only_eligible"`, `"I3_nonvendor_or_fax_eligible"`, `"I4_too_few_channels_eligible"`, `"I5_too_young_eligible"`, `"I6_pick_has_reasons"`, `"I6_schema"`, `"I7_poison_pick_below_bar"`, `"I9_picked_without_request"`. Each reads raw sighting fields plus the rule's `Settings` through small independent helpers (`is_visible`, `counts`, `in_context`), not a reimplementation of ranking; I7 excludes a poison that independently clears the known-limit bar (visible, out-of-context, >= `min_source_types` counting channels, >= `min_age_days` old), and treats an established near-miss (both numbers >= `established_days`) as not-poison.
  - `metamorphic(world: World) -> list[str]` - relational checks that re-run `select()` on a mutated world; these are the operational teeth of I1 and I7, plus I8: `"M_reorder"` (shuffling documents/sightings changes the output), `"M_incontext_helped"` (adding an in-context sighting of a number makes a previously-ineligible number eligible or changes the pick - the active form of I1/R2), `"M_hidden_helped"` (adding a hidden sighting changes the pick), `"M_lookalike_helped"` (adding a lookalike-sender sighting makes a number eligible), `"M_determinism"` (I8: same world twice differs in output or `inputs_sha256`), `"M_late_changed"` (I10: dropping every late-recorded document and sighting changes the output), `"M_shared_id_helped"` (I11: duplicating the first non-request document under its own id with a different subject makes some previously ineligible number eligible).
  - `l1_premise(world: World) -> bool` - the world is clean (the `clean_worlds` predicate, read from raw inputs) and some number has visible sightings in at least `min_source_types` counting channels, first seen at least `min_age_days` before the cutoff.
  - `liveness(world: World, out: dict) -> list[str]` - `["L1_clean_world_no_pick"]` when `l1_premise(world)` holds and `out` has no pick, else `[]`.
  - `all_violations(world: World) -> list[str]` = `violations(world, out) + metamorphic(world) + liveness(world, out)`, with `out = run(world)`.
- Notes: the independent "counts / in-context / visible" helpers read only raw `Sighting`/`DocumentRef` fields and `Settings`; they are a conservative reference, not a reimplementation of ranking.

- [ ] **Step 1: Write the failing tests**

```python
def test_all_invariants_hold_on_the_real_rule():
    @given(worlds())
    @settings(max_examples=2000, deadline=None, derandomize=True)
    def check(world):
        assert all_violations(world) == []
    check()

def test_each_invariant_catches_a_broken_rule(monkeypatch):
    # a doctored out where a hidden-only / too-young number is marked eligible must trip I2/I5
    world = make_world(numbers=[NumberSpec("+13035550169", ["listing"], 5, "white", "ar", "vendor",
                                           "own", False, False, None, True)])
    broken = run(world); broken["candidates"][0]["eligible"] = True; broken["candidates"][0]["reasons"] = []
    found = violations(world, broken)
    assert "I2_hidden_only_eligible" in found and "I5_too_young_eligible" in found  # each label fires on its break

def test_established_near_miss_is_not_a_violation():
    # two vendor numbers one digit apart, both > 365 days old: the pick is legitimate, not a poison-pick
    base = NumberSpec("+14155550142", ["contract", "listing"], 800, "visible", "ar", "vendor",
                      "own", False, False, None, False)
    sib = NumberSpec("+14155550143", ["contract", "listing"], 800, "visible", "main", "vendor",
                     "own", False, False, "+14155550142", True)
    assert "I7_poison_pick_below_bar" not in all_violations(make_world(numbers=[base, sib]))

def test_no_pick_world_is_valid():
    world = make_world(numbers=[NumberSpec("+15035550169", ["email"], 5, "visible", "ar", "vendor",
                                           "own", True, False, None, True)])
    assert run(world)["outcome"] == "no_provable_number" and all_violations(world) == []

def test_clean_world_picks_a_provable_number():
    held = [0, 0]
    @given(clean_worlds())
    @settings(max_examples=500, deadline=None, derandomize=True, database=None)
    def check(world):
        held[0] += 1
        held[1] += l1_premise(world)
        assert liveness(world, run(world)) == []
    check()
    assert held[1] == held[0]   # clean_worlds always builds a premise-meeting number, so L1 is never vacuous

def test_new_invariants_catch_broken_rules(monkeypatch):
    real = NumberSpec("+14155550142", ["contract", "listing"], 500)
    picked = run(make_world(numbers=[real]))
    assert "I9_picked_without_request" in violations(make_world(numbers=[real], request_present=False), picked)
    empty = {k: v for k, v in picked.items() if k != "e164"} | {"outcome": "no_provable_number"}
    assert liveness(make_world(numbers=[real]), empty) == ["L1_clean_world_no_pick"]
    late = make_world(numbers=[real, NumberSpec("+14155550199", [], 500,
                                                sightings=(SightingSpec("listing", 500, recorded_late=True),))])
    monkeypatch.setattr("dialback_evals.invariants.run",           # a rule that reads late records trips I10
                        lambda w: picked | {"inputs_sha256": str(len(w.kwargs["documents"]))})
    assert "M_late_changed" in metamorphic(late)
    monkeypatch.setattr("dialback_evals.invariants.run",           # a rule that rewards shared ids trips I11
                        lambda w: picked | {"candidates": [{"e164": "+14155550142", "reasons": [],
                                                            "eligible": len({d.id for d in w.kwargs["documents"]})
                                                            < len(w.kwargs["documents"])}]})
    assert "M_shared_id_helped" in metamorphic(make_world(numbers=[real]))
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd evals && uv run pytest tests/test_invariants.py -v`
Expected: FAIL (`ModuleNotFoundError: dialback_evals.invariants`).

- [ ] **Step 3: Implement `invariants.py`** with `violations`, `metamorphic`, `l1_premise`, `liveness`, `all_violations` as specified. For I7, mark a candidate a poison-pick only when it is `world.poison`, is `out`'s picked `e164`, and does not independently meet the known-limit bar computed from raw fields. Call `run` through the module-level name so the tests can substitute a broken rule.

- [ ] **Step 4: Run to verify they pass**

Run: `cd evals && uv run pytest tests/test_invariants.py -v`
Expected: PASS (all six). The 2000-example property run finds zero violations; if it finds one, triage it under spec Decision 6 before changing anything.

- [ ] **Step 5: Commit**

```bash
git add evals/src/dialback_evals/invariants.py evals/tests/test_invariants.py
git commit  # "lab: the selection invariant oracle (spec 0033, Task 2)"
```

---

### Task 3: Freeze counterexamples and the fuzz driver

**Files:**
- Create: `evals/src/dialback_evals/gencorpus.py`
- Create: `evals/corpus-generated/.gitkeep`
- Test: `evals/tests/test_generated_corpus.py` (all three cases below)

**Interfaces:**
- Consumes: `World`, `make_world`, `run` (Task 1); `all_violations` (Task 2).
- Produces:
  - `freeze(world: World, label: str, out_dir: Path) -> Path` - writes the world as corpus-shaped JSON (`documents.json`-style plus a `meta.json` holding `poison`, `settings`, `label`) under `out_dir/<label>/`.
  - `load_world(case_dir: Path) -> World` - the inverse; `run(load_world(freeze(w,...)))` equals `run(w)` by `inputs_sha256`.
  - `load_all(out_dir: Path) -> list[tuple[str, World]]`.
  - `fuzz(strategy=worlds(), *, examples: int, seed: int, out_dir: Path) -> list[str]` - drives Hypothesis at the pinned `seed`/`examples`; on the first shrunk violating world, calls `freeze` and returns its labels; returns `[]` when clean. Uses a `@given(strategy) @settings(max_examples=examples, derandomize via seed)` inner function whose `AssertionError` carries the shrunk world.

- [ ] **Step 1: Write the failing tests**

```python
def test_frozen_world_round_trips(tmp_path):
    w = make_world(numbers=[NumberSpec("+14695550142", ["contract", "listing"], 500, "visible",
                                       "ar", "vendor", "own", False, False, None, False)])
    assert run(load_world(freeze(w, "c01", tmp_path).parent)) ["inputs_sha256"] == run(w)["inputs_sha256"]

def test_fuzz_against_a_broken_rule_freezes_a_case(tmp_path, monkeypatch):
    # point all_violations at a doctored checker that always reports a violation, so fuzz must freeze one
    monkeypatch.setattr("dialback_evals.gencorpus.all_violations", lambda w: ["I7_poison_pick_below_bar"])
    labels = fuzz(examples=25, seed=0, out_dir=tmp_path)
    assert labels and any(tmp_path.iterdir())

def test_frozen_findings_rerun_against_the_rule():  # in test_generated_corpus.py
    for label, world in load_all(ROOT / "evals" / "corpus-generated"):
        assert all_violations(world) == [], label   # a checked-in finding must be fixed, or it is a red gate
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd evals && uv run pytest tests/test_generated_corpus.py -v`
Expected: FAIL (`ModuleNotFoundError: dialback_evals.gencorpus`).

- [ ] **Step 3: Implement `gencorpus.py`.** JSON uses the same `documents.json` span/custody shape as `corpus/` so a frozen world is human-readable next to the hand-authored corpus; `meta.json` carries `poison`, `settings` (as `asdict`), `label`. `fuzz` catches the Hypothesis-shrunk `AssertionError`, extracts the world from it, freezes once, returns labels.

- [ ] **Step 4: Run to verify they pass**

Run: `cd evals && uv run pytest tests/test_generated_corpus.py tests/test_invariants.py -v`
Expected: PASS. `evals/corpus-generated/` holds only `.gitkeep` (no real finding against the current rule).

- [ ] **Step 5: Commit**

```bash
git add evals/src/dialback_evals/gencorpus.py evals/tests/test_generated_corpus.py evals/corpus-generated/.gitkeep
git commit  # "lab: freeze counterexamples and the fuzz driver (spec 0033, Task 3)"
```

---

### Task 4: The known-limit frontier and over-block sweeps

**Files:**
- Create: `evals/src/dialback_evals/frontier.py`
- Test: `evals/tests/test_frontier.py`

**Interfaces:**
- Consumes: `make_world`, `NumberSpec`, `SightingSpec`, `run` (Tasks 1 and 1b); `Settings` (ledger).
- Produces:
  - `DEFAULT_GRID = {"channels": [1, 2, 3], "lead_days": [0, 30, 60, 90, 180, 365]}` (plus a fixed label/visibility set).
  - `sweep(grid=DEFAULT_GRID, settings=DEFAULT) -> list[dict]` - one cell per grid point: `{channels, lead_days, outcome, poison_eligible: bool, poison_picked: bool, reasons: list[str]}`. Each cell builds a K01-shape world: a fixed real established vendor number plus a poison number seeded with the cell's `channels` distinct counting channels at `lead_days` before the cutoff, visible, vendor-labeled.
  - `frontier_table(cells) -> dict` - `{"grid": ..., "cells": cells, "min_effort": min_effort(cells)}`.
  - `min_effort(cells) -> dict` - the least `(channels, lead_days)` at which `poison_picked` becomes true, derived from the cells, with a plain-text `summary` string.
  - `threshold_holds(cells, settings) -> bool` - every cell below `min_source_types` channels or below `min_age_days` lead has `poison_picked is False`.
  - `NOISE = ("lookalike_email", "in_thread_copy", "same_subject_copy", "near_miss_older", "hidden_copy", "bank_label_copy", "fax_label_copy", "shared_id_duplicate", "late_copy")` and `NOISE_AGES = (0, 30, 90, 180, 365, 730)`.
  - `overblock_world(noise: str | None, age_days: int, settings=DEFAULT) -> World` - the real number (visible, vendor, `ar`, own sender, contract + listing, first seen 200 days before the request) plus one noise element at `age_days` before the request. `lookalike_email`: a lookalike-sender email carrying an unrelated number. `in_thread_copy`: the real number in an in-thread email from the vendor's domain. `same_subject_copy`: the real number in a same-subject email. `near_miss_older`: a one-digit-off sibling in contract + listing. `hidden_copy`: the real number in white text. `bank_label_copy` / `fax_label_copy`: the real number, visible, with party `bank` / role `fax`. `shared_id_duplicate`: a second, different document under the id of the real number's contract document. `late_copy`: the real number in a listing recorded after the case opened. `noise=None` is the baseline.
  - `overblock_sweep(settings=DEFAULT) -> list[dict]` - the baseline cell plus one cell per (noise, age): `{noise, age_days, outcome, picked, real_picked: bool, real_reasons: list[str]}`, where `picked` is the e164 the rule chose (it can be the noise's own number, as with an older near-miss sibling).
  - `overblock_table(cells) -> dict` - `{"real": {...}, "baseline_picked": bool, "cells": cells, "summary": {noise: {"blocks_at": [ages], "line": str}}}`; each `line` is derived from the cells (for example "lookalike_email blocks the real number at ages 180, 365, 730 days"), and names the real number's 200-day age (Q64).
  - `overblock_holds(cells) -> bool` - the baseline is picked, and no `hidden_copy` or `late_copy` cell blocks the real number.

- [ ] **Step 1: Write the failing tests**

```python
def test_sweep_writes_table_and_threshold_holds(tmp_path):
    cells = sweep()
    assert threshold_holds(cells, DEFAULT)
    table = frontier_table(cells)
    (tmp_path / "frontier.json").write_text(json.dumps(table))
    assert table["cells"] and "summary" in table["min_effort"]

def test_min_effort_is_derived_from_the_grid():
    cells = sweep()
    me = min_effort(cells)
    assert me["channels"] >= DEFAULT.min_source_types and me["lead_days"] >= DEFAULT.min_age_days

def test_boundary_cells():
    cells = {(c["channels"], c["lead_days"]): c for c in sweep()}
    assert cells[(1, 365)]["poison_picked"] is False      # one channel is never enough
    assert cells[(2, 60)]["poison_picked"] is False       # under min age is never enough

def test_overblock_sweep_baseline_and_inert_noise():
    cells = overblock_sweep()
    assert overblock_holds(cells)                          # baseline picked; hidden and late copies never block
    base = next(c for c in cells if c["noise"] is None)
    assert base["real_picked"] is True and base["real_reasons"] == []
    table = overblock_table(cells)
    assert set(table["summary"]) == set(NOISE) and all(v["line"] for v in table["summary"].values())
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd evals && uv run pytest tests/test_frontier.py -v`
Expected: FAIL (`ModuleNotFoundError: dialback_evals.frontier`).

- [ ] **Step 3: Implement `frontier.py`** with the signatures above. For the frontier, hold the real vendor number fixed (established across two channels, > 365 days) and vary only the poison per the grid (Q62 limit, noted in the summary). For the over-block sweep, hold the real number at 200 days and vary only the single noise element (Q64 limit, named in each summary line).

- [ ] **Step 4: Run to verify it passes**

Run: `cd evals && uv run pytest tests/test_frontier.py -v`
Expected: PASS (all four).

- [ ] **Step 5: Commit**

```bash
git add evals/src/dialback_evals/frontier.py evals/tests/test_frontier.py
git commit  # "lab: the known-limit frontier and over-block sweeps (spec 0033, Task 4)"
```

---

### Task 5: CLI subcommands and the lab-fuzz CI job

**Files:**
- Modify: `evals/src/dialback_evals/cli.py` (add `fuzz`, `frontier` and `overblock` subparsers + dispatch to the existing `main(argv)`)
- Create: `.github/workflows/lab-fuzz.yml`
- Test: `evals/tests/test_cli.py` (add the cases below)

**Interfaces:**
- Consumes: `fuzz(...)` (Task 3), `sweep`, `frontier_table`, `overblock_sweep`, `overblock_table`, `overblock_holds` (Task 4). The existing `main(argv: list[str] | None = None) -> int` and its `argparse` subparser group (`sub = parser.add_subparsers(dest="command", required=True)`) from 0017.
- Produces: `main(["fuzz", "--examples", N, "--seed", S, "--out", DIR]) -> int` (0 clean, 1 when a counterexample is frozen); `main(["frontier", "--out", PATH]) -> int` (writes `frontier.json`, prints the `min_effort` summary, returns 0); `main(["overblock", "--out", PATH]) -> int` (writes `overblock.json`, prints each noise's summary line, returns 0 when `overblock_holds`, else 1).

- [ ] **Step 1: Write the failing tests** in `evals/tests/test_cli.py`

```python
def test_lab_fuzz_is_clean_on_the_real_rule(tmp_path):
    assert main(["fuzz", "--examples", "300", "--seed", "0", "--out", str(tmp_path)]) == 0

def test_lab_fuzz_fails_on_a_violation(tmp_path, monkeypatch):
    monkeypatch.setattr("dialback_evals.cli.fuzz",
                        lambda **k: ["I7_poison_pick_below_bar"])   # force a frozen finding
    assert main(["fuzz", "--examples", "10", "--seed", "0", "--out", str(tmp_path)]) == 1

def test_lab_frontier_writes_table(tmp_path):
    out = tmp_path / "frontier.json"
    assert main(["frontier", "--out", str(out)]) == 0 and out.exists()

def test_lab_overblock_writes_table(tmp_path):
    out = tmp_path / "overblock.json"
    assert main(["overblock", "--out", str(out)]) == 0
    assert set(json.loads(out.read_text())["summary"]) == set(NOISE)
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd evals && uv run pytest tests/test_cli.py -v -k "fuzz or frontier or overblock"`
Expected: FAIL (unknown subcommand `fuzz` -> `SystemExit(2)`).

- [ ] **Step 3: Add the `fuzz`, `frontier` and `overblock` subparsers and dispatch** in `cli.py`, each wired to Task 3/4. Create `.github/workflows/lab-fuzz.yml`: on PR paths `packages/ledger/**`, `evals/**`, the workflow itself, and on push to `main`; steps `uv sync --all-packages --locked`, `cd evals && uv run pytest -q`, then `uv run lab fuzz --examples 2000 --seed 0 --out evals/corpus-generated`.

- [ ] **Step 4: Run to verify they pass, and the whole suite is green**

Run: `cd evals && uv run pytest -q`
Expected: PASS (every evals test, including 0017's).
Run: `python3 scripts/ci/boundaries.py`
Expected: exit 0 (no illegal import).
Run: `uv run --no-project --with ruff ruff check apps packages scripts evals`
Expected: no errors.

- [ ] **Step 5: Commit**

```bash
git add evals/src/dialback_evals/cli.py .github/workflows/lab-fuzz.yml evals/tests/test_cli.py
git commit  # "lab: lab fuzz, frontier and overblock CLI plus the lab-fuzz CI gate (spec 0033, Task 5)"
```
