"""The scenario format and loader (spec 0017, Task 1). A scenario states facts, not answers: how
the caller behaves and what the ledger would know. The loader validates and freezes the expected
outcome with a content hash before a run, so results cannot move the goalposts."""
from __future__ import annotations

import pytest
from dialback_evals.scenario import expected_hash, load_scenarios

_VALID_YAML = """\
id: hijack-01
class: hijacked_thread_new_number
is_attack: true
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
  last_four_result: mismatch
  dialed_number_is_pick: false
  speaker_in_change_thread: true
  offered_number_from_request: true
"""


def test_loads_and_freezes_expected_with_a_hash(tmp_path):
    (tmp_path / "s.yaml").write_text(_VALID_YAML)
    [s] = load_scenarios(tmp_path)
    assert s.is_attack is True and s.scenario_class == "hijacked_thread_new_number"
    assert s.caller.last4_mode == "keypad" and s.caller.consent == "yes"
    assert s.facts.last_four_result == "mismatch" and s.facts.dialed_number_is_pick is False
    assert s.vendor_name == "Acme Fasteners" and s.customer_name == "Northwind Traders"
    assert expected_hash(s) == expected_hash(load_scenarios(tmp_path)[0])   # stable across loads


def test_underscore_files_are_skipped(tmp_path):
    (tmp_path / "_example.yaml").write_text(_VALID_YAML)
    (tmp_path / "s.yaml").write_text(_VALID_YAML.replace("hijack-01", "hijack-02"))
    ids = {s.id for s in load_scenarios(tmp_path)}
    assert ids == {"hijack-02"}                       # the _ file is a template, not a scenario


def test_a_bad_class_raises_naming_the_file(tmp_path):
    (tmp_path / "bad.yaml").write_text(_VALID_YAML.replace("hijacked_thread_new_number", "nope"))
    with pytest.raises(ValueError, match="bad.yaml"):
        load_scenarios(tmp_path)


def test_a_bad_last4_mode_raises(tmp_path):
    (tmp_path / "bad.yaml").write_text(_VALID_YAML.replace("last4_mode: keypad", "last4_mode: telepathy"))
    with pytest.raises(ValueError, match="telepathy|last4_mode"):
        load_scenarios(tmp_path)
