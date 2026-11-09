# Generative Selection Pressure Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Put the pure number-selection rule (`dialback_ledger.select`) under adversarial property-based search in the attack lab, freeze any counterexample as a regression, and sweep the K01 known-limit to a frontier table.

**Architecture:** A Hypothesis strategy builds `select()` input worlds parametric over attacker knobs; an invariant oracle asserts the rule's guarantees as universals plus metamorphic inertness properties (attacker-added evidence never helps); a driver shrinks and freezes violations as corpus-shaped JSON; a separate parametric sweep measures the minimum attacker effort that reaches a pick. All of it imports only the pure `select()` and reads its output against `selection.schema.json`. It never edits the rule.

**Tech Stack:** Python 3.13, Hypothesis (already a dev dependency), jsonschema, argparse CLI, pytest.

**Spec:** `specs/0030-generative-selection-pressure.md`

**Prerequisite / base branch:** This plan extends the `evals` lab package and its `lab` CLI (`dialback_evals.cli:main`), which are delivered by spec 0017 and are **not** on `origin/main` (main has only the `evals` skeleton). Implement this on a branch based on `sami/attack-lab-voice` (which carries 0017), or on `main` once 0017 merges. The spec-only branch `sami/attack-lab-generative` is cut from `origin/main` and must be rebased onto that base before Task 1. Every "Modify" path below (`cli.py`, `pyproject.toml`) refers to the 0017 version of that file.

## Global Constraints

- The lab imports only `dialback_ledger.select` (pure). No database, network, model, live account. **Never edit `packages/ledger/src/dialback_ledger/select.py`** or any rule behavior; a property failure is a finding for the rule's owner, not a fix here.
- `python3 scripts/ci/boundaries.py` must stay green: no new cross-slice import beyond the `dialback-ledger` package dependency.
- Add `dialback-ledger` and `jsonschema>=4` to `evals/pyproject.toml` `dependencies`; `hypothesis>=6` is already in the `dev` group.
- Lab-written findings live in `evals/corpus-generated/`, never in `corpus/` (that is another slice).
- No contract change. `packages/contracts/schemas/selection.schema.json` is read only.
- ruff: line-length 110, target py313. Run `uv run --no-project --with ruff ruff check apps packages scripts evals`.
- Every commit message ends with: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.
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
git commit  # "lab: the select() world generator (spec 0030, Task 1)"
```

---

### Task 2: The invariant oracle

**Files:**
- Create: `evals/src/dialback_evals/invariants.py`
- Test: `evals/tests/test_invariants.py`

**Interfaces:**
- Consumes: `World`, `make_world`, `run`, `NumberSpec` (Task 1).
- Produces:
  - `violations(world: World, out: dict) -> list[str]` - structural checks (spec invariants I1-I7), each returning a label on failure: `"I1_incontext_only_eligible"` (a number whose every visible counting sighting is in the change context is eligible), `"I2_hidden_only_eligible"`, `"I3_nonvendor_or_fax_eligible"`, `"I4_too_few_channels_eligible"`, `"I5_too_young_eligible"`, `"I6_pick_has_reasons"`, `"I6_schema"`, `"I7_poison_pick_below_bar"`. Each reads raw sighting fields plus the rule's `Settings` through small independent helpers (`is_visible`, `counts`, `in_context`), not a reimplementation of ranking; I7 excludes a poison that independently clears the known-limit bar (visible, out-of-context, >= `min_source_types` counting channels, >= `min_age_days` old), and treats an established near-miss (both numbers >= `established_days`) as not-poison.
  - `metamorphic(world: World) -> list[str]` - relational checks that re-run `select()` on a mutated world; these are the operational teeth of I1 and I7, plus I8: `"M_reorder"` (shuffling documents/sightings changes the output), `"M_incontext_helped"` (adding an in-context sighting of a number makes a previously-ineligible number eligible or changes the pick - the active form of I1/R2), `"M_hidden_helped"` (adding a hidden sighting changes the pick), `"M_lookalike_helped"` (adding a lookalike-sender sighting makes a number eligible), `"M_determinism"` (I8: same world twice differs in output or `inputs_sha256`).
  - `all_violations(world: World) -> list[str]` = `violations(world, run(world)) + metamorphic(world)`.
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
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd evals && uv run pytest tests/test_invariants.py -v`
Expected: FAIL (`ModuleNotFoundError: dialback_evals.invariants`).

- [ ] **Step 3: Implement `invariants.py`** with `violations`, `metamorphic`, `all_violations` as specified. For I7, mark a candidate a poison-pick only when it is `world.poison`, is `out`'s picked `e164`, and does not independently meet the known-limit bar computed from raw fields.

- [ ] **Step 4: Run to verify they pass**

Run: `cd evals && uv run pytest tests/test_invariants.py -v`
Expected: PASS (all four). The 2000-example property run finds zero violations.

- [ ] **Step 5: Commit**

```bash
git add evals/src/dialback_evals/invariants.py evals/tests/test_invariants.py
git commit  # "lab: the selection invariant oracle (spec 0030, Task 2)"
```

---

### Task 3: Freeze counterexamples and the fuzz driver

**Files:**
- Create: `evals/src/dialback_evals/gencorpus.py`
- Create: `evals/corpus-generated/.gitkeep`
- Test: `evals/tests/test_generated_corpus.py` (add round-trip + freeze cases to `test_invariants.py` where noted)

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

Run: `cd evals && uv run pytest tests/test_generated_corpus.py tests/test_invariants.py -v -k "round_trip or freezes or rerun"`
Expected: FAIL (`ModuleNotFoundError: dialback_evals.gencorpus`).

- [ ] **Step 3: Implement `gencorpus.py`.** JSON uses the same `documents.json` span/custody shape as `corpus/` so a frozen world is human-readable next to the hand-authored corpus; `meta.json` carries `poison`, `settings` (as `asdict`), `label`. `fuzz` catches the Hypothesis-shrunk `AssertionError`, extracts the world from it, freezes once, returns labels.

- [ ] **Step 4: Run to verify they pass**

Run: `cd evals && uv run pytest tests/test_generated_corpus.py tests/test_invariants.py -v`
Expected: PASS. `evals/corpus-generated/` holds only `.gitkeep` (no real finding against the current rule).

- [ ] **Step 5: Commit**

```bash
git add evals/src/dialback_evals/gencorpus.py evals/tests/test_generated_corpus.py evals/corpus-generated/.gitkeep
git commit  # "lab: freeze counterexamples and the fuzz driver (spec 0030, Task 3)"
```

---

### Task 4: The known-limit frontier sweep

**Files:**
- Create: `evals/src/dialback_evals/frontier.py`
- Test: `evals/tests/test_frontier.py`

**Interfaces:**
- Consumes: `make_world`, `NumberSpec`, `run` (Task 1); `Settings` (ledger).
- Produces:
  - `DEFAULT_GRID = {"channels": [1, 2, 3], "lead_days": [0, 30, 60, 90, 180, 365]}` (plus a fixed label/visibility set).
  - `sweep(grid=DEFAULT_GRID, settings=DEFAULT) -> list[dict]` - one cell per grid point: `{channels, lead_days, outcome, poison_eligible: bool, poison_picked: bool, reasons: list[str]}`. Each cell builds a K01-shape world: a fixed real established vendor number plus a poison number seeded with the cell's `channels` distinct counting channels at `lead_days` before the cutoff, visible, vendor-labeled.
  - `frontier_table(cells) -> dict` - `{"grid": ..., "cells": cells, "min_effort": min_effort(cells)}`.
  - `min_effort(cells) -> dict` - the least `(channels, lead_days)` at which `poison_picked` becomes true, derived from the cells, with a plain-text `summary` string.
  - `threshold_holds(cells, settings) -> bool` - every cell below `min_source_types` channels or below `min_age_days` lead has `poison_picked is False`.

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
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd evals && uv run pytest tests/test_frontier.py -v`
Expected: FAIL (`ModuleNotFoundError: dialback_evals.frontier`).

- [ ] **Step 3: Implement `frontier.py`** with the signatures above. Hold the real vendor number fixed (established across two channels, > 365 days), vary only the poison per the grid (Q62 limit, noted in the summary).

- [ ] **Step 4: Run to verify it passes**

Run: `cd evals && uv run pytest tests/test_frontier.py -v`
Expected: PASS (all three).

- [ ] **Step 5: Commit**

```bash
git add evals/src/dialback_evals/frontier.py evals/tests/test_frontier.py
git commit  # "lab: the known-limit frontier sweep (spec 0030, Task 4)"
```

---

### Task 5: CLI subcommands and the lab-fuzz CI job

**Files:**
- Modify: `evals/src/dialback_evals/cli.py` (add `fuzz` and `frontier` subparsers + dispatch to the existing `main(argv)`)
- Create: `.github/workflows/lab-fuzz.yml`
- Test: `evals/tests/test_cli.py` (add the two cases below)

**Interfaces:**
- Consumes: `fuzz(...)` (Task 3), `sweep`, `frontier_table` (Task 4). The existing `main(argv: list[str] | None = None) -> int` and its `argparse` subparser group (`sub = parser.add_subparsers(dest="command", required=True)`) from 0017.
- Produces: `main(["fuzz", "--examples", N, "--seed", S, "--out", DIR]) -> int` (0 clean, 1 when a counterexample is frozen); `main(["frontier", "--out", PATH]) -> int` (writes `frontier.json`, prints the `min_effort` summary, returns 0).

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
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd evals && uv run pytest tests/test_cli.py -v -k "fuzz or frontier"`
Expected: FAIL (unknown subcommand `fuzz` -> `SystemExit(2)`).

- [ ] **Step 3: Add the `fuzz` and `frontier` subparsers and dispatch** in `cli.py`, each wired to Task 3/4. Create `.github/workflows/lab-fuzz.yml`: on PR paths `packages/ledger/**`, `evals/**`, the workflow itself, and on push to `main`; steps `uv sync --all-packages --locked`, `cd evals && uv run pytest -q`, then `uv run lab fuzz --examples 2000 --seed 0 --out evals/corpus-generated`.

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
git commit  # "lab: lab fuzz and frontier CLI plus the lab-fuzz CI gate (spec 0030, Task 5)"
```
