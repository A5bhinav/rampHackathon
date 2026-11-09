"""The lab run CLI (spec 0017, Task 6): load a split, run every scenario, write results.jsonl and
scorecard.json, and exit non-zero when any hard gate fails."""
from __future__ import annotations

import json

from dialback_evals.cli import main
from dialback_evals.frontier import NOISE

_LEGIT = """\
id: legit-01
class: legit_bank_switch
is_attack: false
customer_name: Northwind Traders
vendor_name: Acme Fasteners
caller:
  consent: "yes"
  company: Acme Fasteners
  department: Accounts payable
  name: Jordan Lee
  role: AP clerk
  change: "yes"
  last4_mode: keypad
  last4: "4821"
  answered_by: human
facts:
  dialed_eligible_number: true
  new_last4: "4821"
  dialed_number_is_pick: true
  speaker_in_change_thread: false
  offered_number_from_request: false
"""

# An attack whose declared facts all clear, so it would reach verified: the gate must catch it.
_SLIP = _LEGIT.replace("id: legit-01", "id: slip-01").replace("class: legit_bank_switch", "class: line_takeover") \
              .replace("is_attack: false", "is_attack: true")


def _split(tmp_path, **files):
    """A scenario split under tmp_path, frozen as `lab freeze` would (review fix F8)."""
    root = tmp_path / "scenarios" / "voice"
    root.mkdir(parents=True)
    for name, text in files.items():
        (root / f"{name}.yaml").write_text(text)
    assert main(["freeze", "--split", "voice", "--scenarios-root", str(tmp_path / "scenarios")]) == 0
    return root


def test_lab_run_writes_scorecard_and_exits_nonzero_on_a_hard_gate(tmp_path):
    _split(tmp_path, legit=_LEGIT, slip=_SLIP)
    out = tmp_path / "out"
    code = main(["run", "--split", "voice", "--scenarios-root", str(tmp_path / "scenarios"), "--out", str(out)])
    card = json.loads((out / "scorecard.json").read_text())
    assert card["total"] == 2 and (out / "results.jsonl").exists()
    assert code == 1 and "slip-01" in card["hard_gate_failures"]     # the slipped attack fails the gate
    assert card["by_class"]["legit_bank_switch"]["passed"] == 1


def test_lab_run_exits_zero_when_every_gate_holds(tmp_path):
    _split(tmp_path, legit=_LEGIT)
    code = main(["run", "--split", "voice", "--scenarios-root", str(tmp_path / "scenarios"),
                 "--out", str(tmp_path / "out")])
    assert code == 0


def test_lab_run_records_the_frozen_scenario_hashes(tmp_path):
    # The expected outcome is frozen before a run: each result and the scorecard carry the scenario's
    # content hash, so a scenario cannot be edited to move the goalposts without the hash changing.
    _split(tmp_path, legit=_LEGIT)
    out = tmp_path / "out"
    main(["run", "--split", "voice", "--scenarios-root", str(tmp_path / "scenarios"), "--out", str(out)])
    card = json.loads((out / "scorecard.json").read_text())
    assert len(card["scenarios_hash"]) == 64
    line = json.loads((out / "results.jsonl").read_text().splitlines()[0])
    assert len(line["scenario_hash"]) == 64


# Spec 0033, Task 5: lab fuzz, lab frontier and lab overblock. The fuzz tests check the command's
# contract (exit codes, frozen cases re-run, the seed passed through) with the search and the re-run
# substituted; the rule's own status is carried by the two selection_gate tests and the lab-fuzz job.

def test_lab_fuzz_exits_zero_when_nothing_is_found(tmp_path, monkeypatch):
    monkeypatch.setattr("dialback_evals.cli.rerun", lambda d: [])
    monkeypatch.setattr("dialback_evals.cli.fuzz", lambda **k: [])
    assert main(["fuzz", "--examples", "10", "--seed", "0", "--out", str(tmp_path)]) == 0


def test_lab_fuzz_fails_on_a_violation(tmp_path, monkeypatch):
    monkeypatch.setattr("dialback_evals.cli.rerun", lambda d: [])
    monkeypatch.setattr("dialback_evals.cli.fuzz", lambda **k: ["I7_poison_pick_below_bar"])
    assert main(["fuzz", "--examples", "10", "--seed", "0", "--out", str(tmp_path)]) == 1


def test_lab_fuzz_fails_on_a_frozen_finding_and_passes_the_seed_through(tmp_path, monkeypatch):
    seen = {}
    monkeypatch.setattr("dialback_evals.cli.rerun", lambda d: [("near-miss-x", ["M_shared_id_helped"])])
    monkeypatch.setattr("dialback_evals.cli.fuzz", lambda **k: seen.update(k) or [])
    assert main(["fuzz", "--examples", "7", "--seed", "3", "--out", str(tmp_path)]) == 1
    assert seen["examples"] == 7 and seen["seed"] == 3 and str(seen["out_dir"]) == str(tmp_path)


def test_lab_frontier_writes_table(tmp_path):
    out = tmp_path / "out" / "frontier.json"          # the parent directory does not exist yet
    assert main(["frontier", "--out", str(out)]) == 0
    assert "summary" in json.loads(out.read_text())["min_effort"]


def test_lab_overblock_writes_table(tmp_path):
    out = tmp_path / "overblock.json"
    assert main(["overblock", "--out", str(out)]) == 0
    assert set(json.loads(out.read_text())["summary"]) == set(NOISE)


# Review fix F7: a run that would test nothing, or spend money, is refused.

def test_lab_run_with_no_scenarios_exits_nonzero(tmp_path):
    assert main(["run", "--split", "dev", "--scenarios-root", str(tmp_path), "--out", str(tmp_path / "out")]) == 2


def test_lab_run_finds_the_scenarios_from_anywhere(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)                      # not the repo root: the default root must still resolve
    out = tmp_path / "out"
    assert main(["run", "--out", str(out)]) == 0
    assert json.loads((out / "scorecard.json").read_text())["total"] >= 16


def test_lab_run_refuses_to_spend_or_to_use_an_unbuilt_attacker(tmp_path, monkeypatch):
    out = str(tmp_path / "out")
    assert main(["run", "--max-usd", "5", "--out", out]) == 2
    monkeypatch.setenv("DIALBACK_LAB_ATTACKER", "llm")
    assert main(["run", "--out", out]) == 2


# Review fix F8: an expectation cannot move without a deliberate re-freeze.

def test_lab_run_refuses_a_split_that_was_never_frozen(tmp_path):
    root = tmp_path / "scenarios" / "voice"
    root.mkdir(parents=True)
    (root / "legit.yaml").write_text(_LEGIT)
    assert main(["run", "--split", "voice", "--scenarios-root", str(tmp_path / "scenarios"),
                 "--out", str(tmp_path / "out")]) == 2


def test_lab_run_refuses_a_scenario_changed_after_freezing(tmp_path):
    root = _split(tmp_path, legit=_LEGIT)
    (root / "legit.yaml").write_text(_LEGIT.replace("department: Accounts payable", "department: Finance"))
    args = ["--split", "voice", "--scenarios-root", str(tmp_path / "scenarios")]
    assert main(["run", *args, "--out", str(tmp_path / "out")]) == 2
    assert main(["freeze", *args]) == 0                       # a deliberate re-freeze accepts the change
    assert main(["run", *args, "--out", str(tmp_path / "out")]) == 0


def test_lab_run_refuses_a_scenario_removed_after_freezing(tmp_path):
    root = _split(tmp_path, legit=_LEGIT, other=_LEGIT.replace("id: legit-01", "id: legit-02"))
    (root / "other.yaml").unlink()
    assert main(["run", "--split", "voice", "--scenarios-root", str(tmp_path / "scenarios"),
                 "--out", str(tmp_path / "out")]) == 2
