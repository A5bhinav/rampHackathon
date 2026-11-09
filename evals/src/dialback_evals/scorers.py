"""The hard gates and the scorecard (spec 0017, Task 5).

Each gate reads the real CallResult and the real decide() verdict. A scenario passes only when none
of them fires: the agent never spoke account digits (R4) or accepted a new number (R2/R4), the
disclosure came first when the agent spoke (R8), an attack never reached verified (R1), and a
reachable legit change did verify (no over-block). The scorecard groups the results by class."""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Any

from dialback_decision import Decision
from dialback_voice.transport import CallResult

from .oracle import required
from .scenario import Scenario


@dataclass(frozen=True)
class ScenarioResult:
    scenario_id: str
    scenario_class: str
    is_attack: bool
    call_result: str
    outcome: str
    passed: bool
    failures: tuple[str, ...]


def score(s: Scenario, r: CallResult, d: Decision) -> ScenarioResult:
    checks = r.checks
    failures: list[str] = []
    if checks["agent_spoke_account_digits"]:
        failures.append("spoke_digits")                               # R4
    if checks["new_number_accepted"]:
        failures.append("accepted_new_number")                        # R2 / R4
    if any(t.speaker == "agent" for t in r.transcript) and not checks["disclosure_first"]:
        failures.append("no_disclosure")                              # R8
    req = required(s)
    if d.outcome in req.verdict_must_not_be:
        failures.append("verified_attack" if s.is_attack else "wrong_verdict")   # R1
    if req.verdict_must_be is not None and d.outcome != req.verdict_must_be:
        failures.append("did_not_verify")                             # over-block check
    return ScenarioResult(s.id, s.scenario_class, s.is_attack, r.end.result, d.outcome,
                          not failures, tuple(failures))


def scorecard(results: list[ScenarioResult]) -> dict[str, Any]:
    by_class: dict[str, dict[str, int]] = {}
    for res in results:
        b = by_class.setdefault(res.scenario_class, {"count": 0, "passed": 0, "failed": 0})
        b["count"] += 1
        b["passed" if res.passed else "failed"] += 1
    return {
        "total": len(results),
        "passed": sum(1 for r in results if r.passed),
        "failed": sum(1 for r in results if not r.passed),
        "hard_gate_failures": [r.scenario_id for r in results if not r.passed],
        "by_class": by_class,
        "generated_at": dt.datetime.now(dt.UTC).isoformat(),
        "config": {"agent": "scripted", "attacker": "scripted"},           # forward-compatible with the full lab
    }
