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


def _lab_commands() -> list[str]:
    """Every `lab` subcommand the CLI defines, read from its own help."""
    import contextlib
    import io
    import re

    from dialback_evals.cli import main
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.suppress(SystemExit):
        main(["--help"])
    return re.search(r"\{([a-z,-]+)\}", out.getvalue()).group(1).split(",")


def test_every_lab_command_is_documented():
    # Spec 0048, C4 (FINDINGS B7, M11): every lab command is in AGENTS.md's commands table and the evals README.
    commands = _lab_commands()
    assert {"run", "fuzz", "frontier", "overblock"} <= set(commands)
    agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    table = agents[agents.index("## Commands"):agents.index("## Never")]
    readme = (ROOT / "evals" / "README.md").read_text(encoding="utf-8")
    assert {c: (f"lab {c}" in table, f"lab {c}" in readme) for c in commands} == {c: (True, True) for c in commands}
