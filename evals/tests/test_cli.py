"""The lab run CLI (spec 0017, Task 6): load a split, run every scenario, write results.jsonl and
scorecard.json, and exit non-zero when any hard gate fails."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
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


# Spec 0083, Task 5: lab fuzz, lab frontier and lab overblock. The fuzz tests check the command's
# contract (exit codes, frozen cases re-run, the seed passed through) with the search and the re-run
# substituted; the rule's own status is carried by the two selection_gate tests and the lab-fuzz job.

def test_lab_fuzz_exits_zero_when_nothing_is_found(tmp_path, monkeypatch):
    monkeypatch.setattr("dialback_evals.gencorpus.rerun", lambda d: [])
    monkeypatch.setattr("dialback_evals.gencorpus.fuzz", lambda **k: [])
    assert main(["fuzz", "--examples", "10", "--seed", "0", "--out", str(tmp_path)]) == 0


def test_lab_fuzz_fails_on_a_violation(tmp_path, monkeypatch):
    monkeypatch.setattr("dialback_evals.gencorpus.rerun", lambda d: [])
    monkeypatch.setattr("dialback_evals.gencorpus.fuzz", lambda **k: ["I7_poison_pick_below_bar"])
    assert main(["fuzz", "--examples", "10", "--seed", "0", "--out", str(tmp_path)]) == 1


def test_lab_fuzz_fails_on_a_frozen_finding_and_passes_the_seed_through(tmp_path, monkeypatch):
    seen = {}
    monkeypatch.setattr("dialback_evals.gencorpus.rerun", lambda d: [("near-miss-x", ["M_shared_id_helped"])])
    monkeypatch.setattr("dialback_evals.gencorpus.fuzz", lambda **k: seen.update(k) or [])
    assert main(["fuzz", "--examples", "7", "--seed", "3", "--out", str(tmp_path)]) == 1
    assert seen["examples"] == 7 and seen["seed"] == 3 and str(seen["out_dir"]) == str(tmp_path)


def test_fuzz_default_search_is_today(tmp_path, monkeypatch):
    # Spec 0046: with no --search, or --search first, lab fuzz makes exactly today's call and nothing else.
    monkeypatch.chdir(tmp_path)
    seen = []
    monkeypatch.setattr("dialback_evals.gencorpus.rerun", lambda d: [])
    monkeypatch.setattr("dialback_evals.gencorpus.fuzz", lambda **k: seen.append(k) or [])
    monkeypatch.setattr("dialback_evals.guided.archive_fuzz", lambda *a, **k: pytest.fail("the archive search ran"))
    for extra in ([], ["--search", "first"]):
        assert main(["fuzz", "--examples", "7", "--seed", "3", "--out", "cg", *extra]) == 0
    assert [{k: v for k, v in call.items() if k != "keep"} for call in seen] == [
        {"examples": 7, "seed": 3, "out_dir": Path("cg")}] * 2
    assert not (tmp_path / "evals").exists()                 # no near-miss file, nothing else written


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


def test_lab_frontier_prints_its_grid(tmp_path, capsys):
    # B7, M4: the frontier prints its grid, not one sentence: a row per channel count, a column per lead.
    from dialback_evals.frontier import DEFAULT_GRID
    assert main(["frontier", "--out", str(tmp_path / "frontier.json")]) == 0
    lines = capsys.readouterr().out.splitlines()
    header = next(line for line in lines if line.split()[:1] == ["channels"])
    assert [int(x) for x in header.split()[1:]] == DEFAULT_GRID["lead_days"]
    rows = {int(line.split()[0]): line.split()[1:] for line in lines if line.split()[:1] in
            [[str(c)] for c in DEFAULT_GRID["channels"]]}
    assert sorted(rows) == DEFAULT_GRID["channels"]
    assert all(len(cells) == len(DEFAULT_GRID["lead_days"]) and set(cells) <= {"P", "."} for cells in rows.values())
    assert "P" in "".join(c for cells in rows.values() for c in cells)      # the grid shows where the pick moves


# Spec 0048, part B: lab reports.

def test_lab_reports_fails_on_a_reachable_violation_and_freezes_it(tmp_path, monkeypatch, capsys):
    from dialback_evals import reports
    out = tmp_path / "reports-generated"
    real = reports.run_checks
    monkeypatch.setattr(reports, "run_checks", lambda **kw: real(**kw, decide=reports.doubles.v07_accepts_unsure_nr09_off))
    assert main(["reports", "--examples", "60", "--seed", "0", "--out", str(out)]) == 1
    assert reports.load_all(out) and "reachable F1:" in capsys.readouterr().out


def test_lab_reports_passes_when_nothing_is_found(tmp_path, monkeypatch):
    from dialback_evals import reports
    monkeypatch.setattr(reports, "check_f1", lambda cases, decide=None: reports.Found())
    monkeypatch.setattr(reports, "check_f2", lambda cases, decide=None, moves=(): reports.Found())
    monkeypatch.setattr(reports, "check_f3", lambda cases, matcher=None: reports.Found())
    assert main(["reports", "--examples", "20", "--seed", "0", "--out", str(tmp_path / "r")]) == 0


def test_lab_sweep_starts_without_hypothesis():
    # Spec 0048, B7: lab sweep (like lab run) starts without the dev-only hypothesis package; it imports it
    # only when it draws the generated reports.
    done = _without_hypothesis(["sweep", "--help"])
    assert done.returncode == 0, done.stdout + done.stderr
    assert "--unreachable" in done.stdout



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


def test_lab_fuzz_counts_a_known_hit_and_does_not_freeze_it(tmp_path, monkeypatch, capsys):
    from dialback_evals.gencorpus import load_all
    monkeypatch.setattr("dialback_evals.gencorpus.all_violations", lambda w: ["M_shared_id_helped"])
    entry = {"case": "elsewhere", "labels": ["M_shared_id_helped"], "finding": "A5", "owner": "Inesh"}
    out = tmp_path / "corpus"
    listed = _list(tmp_path, "lab-fuzz", [entry])
    monkeypatch.setattr("dialback_evals.gencorpus.rerun", lambda d: [("elsewhere", ["M_shared_id_helped"])])
    assert main(["fuzz", "--examples", "5", "--seed", "0", "--out", str(out), "--known-failures", listed]) == 0
    assert load_all(out) == [] and "1 search hit(s) showed only listed labels" in capsys.readouterr().out
    monkeypatch.setattr("dialback_evals.gencorpus.all_violations", lambda w: ["I5_too_young_eligible"])
    assert main(["fuzz", "--examples", "5", "--seed", "0", "--out", str(out), "--known-failures", listed]) == 1
    assert len(load_all(out)) == 1                                      # an unlisted hit is frozen, and red


def test_lab_fuzz_archive_holds_the_known_failures_list(tmp_path, monkeypatch, capsys):
    # Spec 0048, D2 step 5 and D3: the archive search runs the whole budget, so every failing world is a hit.
    # Hits with only listed labels are counted and never frozen; an unlisted label is frozen, and red.
    from dialback_evals.gencorpus import load_all
    from dialback_evals.guided import Evaluation
    entry = {"case": "elsewhere", "labels": ["M_shared_id_helped"], "finding": "A5", "owner": "Inesh"}
    out, near = tmp_path / "corpus", tmp_path / "out" / "near-misses.json"
    args = ["fuzz", "--search", "archive", "--examples", "5", "--seed", "0", "--out", str(out),
            "--near-misses", str(near), "--known-failures", _list(tmp_path, "lab-fuzz", [entry])]
    monkeypatch.setattr("dialback_evals.gencorpus.rerun", lambda d: [("elsewhere", ["M_shared_id_helped"])])
    monkeypatch.setattr("dialback_evals.guided.evaluate", lambda w, **k: Evaluation(labels=["M_shared_id_helped"]))
    assert main(args) == 0
    printed = capsys.readouterr().out
    assert load_all(out) == [] and "search hit(s) showed only listed labels" in printed and near.is_file()
    monkeypatch.setattr("dialback_evals.guided.evaluate", lambda w, **k: Evaluation(labels=["I5_too_young_eligible"]))
    assert main(args) == 1
    assert len(load_all(out)) == 1                                      # the unlisted label's world is frozen
    assert "I5_too_young_eligible" in capsys.readouterr().out
