"""Spec 0033, review finding I1: the CI jobs run what the lab claims they run. On a change to the
selection rule, lab-fuzz must run the lab checks that should stay green (the L1 floor, the oracle's
broken-double tests, the coverage floor) as well as the gate; lab-smoke stays the voice slice's job."""
from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def _workflow(name: str) -> tuple[dict, list[str]]:
    wf = yaml.safe_load((ROOT / ".github" / "workflows" / name).read_text())
    triggers = wf.get("on", wf.get(True))            # YAML 1.1 reads a bare `on` key as True
    runs = [step.get("run", "") for job in wf["jobs"].values() for step in job["steps"]]
    return triggers, runs


def test_lab_fuzz_runs_the_green_lab_checks_and_the_gate_on_rule_changes():
    triggers, runs = _workflow("lab-fuzz.yml")
    assert "packages/ledger/**" in triggers["pull_request"]["paths"]
    assert any("pytest" in r and "not selection_gate" in r for r in runs)   # L1, doubles, coverage floor
    assert any("lab fuzz" in r for r in runs)                               # the gate itself


def test_lab_smoke_deselects_the_selection_gate():
    _, runs = _workflow("lab-smoke.yml")
    assert any("pytest" in r and "not selection_gate" in r for r in runs)
