"""The dev scenario split (spec 0017, Task 7): every attack class is covered, and the whole split
upholds every hard gate (attacks never verify, reachable legit changes do, the rest fail closed)."""
from __future__ import annotations

import datetime as dt
from pathlib import Path

from dialback_decision import decide
from dialback_evals.runner import build_facts, run_scenario
from dialback_evals.scenario import load_scenarios
from dialback_evals.scorers import score
from dialback_voice.agent import ScriptedAgent

_DIR = Path(__file__).resolve().parent.parent / "scenarios" / "voice"


def _clock():
    box = {"t": dt.datetime(2026, 11, 1, 17, 0, tzinfo=dt.UTC)}

    def now():
        box["t"] += dt.timedelta(seconds=10)
        return box["t"]
    return now


def test_every_attack_class_is_covered():
    scenarios = load_scenarios(_DIR)
    covered = {s.scenario_class for s in scenarios}
    assert {"hijacked_thread_new_number", "line_takeover", "legit_bank_switch"} <= covered
    attack_classes = {s.scenario_class for s in scenarios if s.is_attack}
    assert len(attack_classes) >= 6               # the attack classes are well represented
    assert len(scenarios) >= 10                   # the dev split floor (Q56)


def test_the_whole_dev_split_upholds_every_gate():
    for s in load_scenarios(_DIR):
        r = run_scenario(s, ScriptedAgent(), clock=_clock())
        res = score(s, r, decide(build_facts(s, r)))
        assert res.passed, f"{s.id}: failures={res.failures} outcome={res.outcome} call={res.call_result}"
