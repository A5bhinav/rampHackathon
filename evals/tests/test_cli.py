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
  department: Accounts payable
  name_role: "Jordan Lee, AP clerk"
  change: "yes"
  last4_mode: keypad
  last4: "4821"
  answered_by: human
facts:
  dialed_eligible_number: true
  last_four_result: match
  dialed_number_is_pick: true
  speaker_in_change_thread: false
  offered_number_from_request: false
"""

# An attack whose declared facts all clear, so it would reach verified: the gate must catch it.
_SLIP = _LEGIT.replace("id: legit-01", "id: slip-01").replace("class: legit_bank_switch", "class: line_takeover") \
              .replace("is_attack: false", "is_attack: true")


def test_lab_run_writes_scorecard_and_exits_nonzero_on_a_hard_gate(tmp_path):
    root = tmp_path / "scenarios" / "voice"
    root.mkdir(parents=True)
    (root / "legit.yaml").write_text(_LEGIT)
    (root / "slip.yaml").write_text(_SLIP)
    out = tmp_path / "out"
    code = main(["run", "--split", "voice", "--scenarios-root", str(tmp_path / "scenarios"), "--out", str(out)])
    card = json.loads((out / "scorecard.json").read_text())
    assert card["total"] == 2 and (out / "results.jsonl").exists()
    assert code == 1 and "slip-01" in card["hard_gate_failures"]     # the slipped attack fails the gate
    assert card["by_class"]["legit_bank_switch"]["passed"] == 1


def test_lab_run_exits_zero_when_every_gate_holds(tmp_path):
    root = tmp_path / "scenarios" / "voice"
    root.mkdir(parents=True)
    (root / "legit.yaml").write_text(_LEGIT)
    code = main(["run", "--split", "voice", "--scenarios-root", str(tmp_path / "scenarios"),
                 "--out", str(tmp_path / "out")])
    assert code == 0


def test_lab_run_records_the_frozen_scenario_hashes(tmp_path):
    # The expected outcome is frozen before a run: each result and the scorecard carry the scenario's
    # content hash, so a scenario cannot be edited to move the goalposts without the hash changing.
    root = tmp_path / "scenarios" / "voice"
    root.mkdir(parents=True)
    (root / "legit.yaml").write_text(_LEGIT)
    out = tmp_path / "out"
    main(["run", "--split", "voice", "--scenarios-root", str(tmp_path / "scenarios"), "--out", str(out)])
    card = json.loads((out / "scorecard.json").read_text())
    assert len(card["scenarios_hash"]) == 64
    line = json.loads((out / "results.jsonl").read_text().splitlines()[0])
    assert len(line["scenario_hash"]) == 64
