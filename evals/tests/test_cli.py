"""The lab run CLI (spec 0017, Task 6): load a split, run every scenario, write results.jsonl and
scorecard.json, and exit non-zero when any hard gate fails."""
from __future__ import annotations

import json

from dialback_evals.cli import main

_LEGIT = """\
id: legit-01
class: legit_bank_switch
is_attack: false
customer_name: Northwind Traders
vendor_name: Acme Fasteners
caller:
  consent: "yes"
  company: Acme Fasteners
  company_is: vendor
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


# Spec 0048, C4 (FINDINGS B7).

_NO_HYPOTHESIS = """
import sys

class _NoHypothesis:   # the dev-only package is not installed where the lab runs scenarios
    def find_spec(self, name, path=None, target=None):
        if name == "hypothesis" or name.startswith("hypothesis."):
            raise ModuleNotFoundError(f"No module named {{name!r}} (blocked by the test)")
        return None

sys.meta_path.insert(0, _NoHypothesis())
from dialback_evals.cli import main
raise SystemExit(main({argv!r}))
"""


def _without_hypothesis(argv: list[str]):
    import subprocess
    import sys
    return subprocess.run([sys.executable, "-c", _NO_HYPOTHESIS.format(argv=argv)], capture_output=True, text=True,
                          check=False)


def test_lab_run_works_without_hypothesis(tmp_path):
    # B7, M2: `lab run` needs nothing from the dev-only hypothesis package.
    done = _without_hypothesis(["run", "--split", "voice", "--out", str(tmp_path)])
    assert done.returncode == 0, done.stdout + done.stderr
    assert "passed" in done.stdout


# Spec 0048, D3: each command holds itself to its job's strict known-failures list.

def _list(tmp_path, job, entries):
    path = tmp_path / "known.json"
    path.write_text(json.dumps({job: entries}))
    return str(path)


def test_lab_run_holds_the_known_failures_list(tmp_path):
    root = _split(tmp_path, slip=_SLIP)
    args = ["run", "--split", "voice", "--scenarios-root", str(root.parent), "--out", str(tmp_path / "out")]
    assert main(args) == 1                                             # no list: any failure is red
    held = {"case": "slip-01", "labels": ["verified_attack"], "finding": "A10", "owner": "Abhi"}
    assert main([*args, "--known-failures", _list(tmp_path, "lab-smoke", [held])]) == 0
    wrong = {**held, "labels": ["verified_attack", "spoke_digits"]}       # listed, but no longer fails like that
    assert main([*args, "--known-failures", _list(tmp_path, "lab-smoke", [wrong])]) == 1
    assert main([*args, "--known-failures", _list(tmp_path, "lab-smoke", [])]) == 1   # unlisted
    orphan = {**held, "finding": "A99"}                                  # no such finding in FINDINGS.md
    assert main([*args, "--known-failures", _list(tmp_path, "lab-smoke", [orphan])]) == 1
